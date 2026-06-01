"""T+0 做T状态机回测器。

输入：历史 5M K线 + 1M K线 + 中枢数据（从 structure_snapshots）
输出：逐笔交易记录 + 含交易成本的 PnL 报告

核心流程：
1. 加载历史数据（从 kline_lake）
2. 按交易日循环，每日 reset_daily()
3. 从 structure_snapshots 读取当日 5M 快照提取 ZG/ZD
4. 在每根 1M 柱上调用 state_machine.tick()
5. 信号产生时调用 t0_paper_service.record_t0_signal() 写入 paper 表
6. 14:55 调用 force_sweep()
7. 日终汇总

不做的：
- V1 不支持多股票并行回测
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from typing import Optional

from server.db.database import get_connection
from server.db.kline_lake import query_klines

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    symbol: str
    start_date: str           # "2025-05-25"
    end_date: str             # "2025-05-29"
    t0_qty: int = 100
    commission_rate: float = 0.00015
    slippage_ticks: int = 1
    use_paper_db: bool = True  # True = 写入 paper_fills，False = 纯内存


@dataclass
class BacktestResult:
    symbol: str
    trading_days: int
    total_signals: int
    total_fills: int
    win_count: int
    loss_count: int
    gross_pnl: float
    total_fees: float
    net_pnl: float
    max_drawdown: float
    sharpe_daily: Optional[float]
    trades: list[dict] = field(default_factory=list)
    daily_summary: list[dict] = field(default_factory=list)


def run_backtest(config: BacktestConfig) -> BacktestResult:
    """执行回测主函数。

    数据来源优先级：
    1. kline_lake.query_klines(symbol, freq="5"/"1", start_date, end_date)
    2. 若无数据，抛出 ValueError

    ZG/ZD 来源：
    - 从 structure_snapshots 读取对应日期的 5M 快照
    - 提取 json_extract(snapshot_json, '$.zg') / '$.zd'
    - 若无快照，该交易日 T0 引擎处于 IDLE（无有效边界）
    """
    from server.engines.t0.t0_state_machine import T0StateMachine
    from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account

    # 1. 加载历史 K 线数据
    klines_5m = query_klines(
        config.symbol, freq="5",
        start_date=config.start_date,
        end_date=config.end_date,
        limit=50000,
    )
    klines_1m = query_klines(
        config.symbol, freq="1",
        start_date=config.start_date,
        end_date=config.end_date,
        limit=200000,
    )

    if not klines_1m and not klines_5m:
        raise ValueError(
            f"无历史 K 线数据：{config.symbol} {config.start_date}~{config.end_date}。"
            "请先运行 kline_sync_worker 同步历史数据。"
        )

    # 2. 按日分组 1M K线
    daily_1m: dict[str, list] = {}
    for k in klines_1m:
        day = str(k["date"])[:10]
        daily_1m.setdefault(day, []).append(k)

    # 3. 读取结构快照中的 ZG/ZD
    def get_pivot(symbol: str, date: str) -> tuple[Optional[float], Optional[float]]:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT json_extract(snapshot_json, '$.zd') AS zd,
                       json_extract(snapshot_json, '$.zg') AS zg
                  FROM structure_snapshots
                 WHERE symbol = ? AND level = '5' AND status = 'fresh'
                   AND date(updated_at) <= ?
                 ORDER BY updated_at DESC
                 LIMIT 1
                """,
                (symbol, date),
            ).fetchone()
            if row and row[0] and row[1]:
                return float(row[0]), float(row[1])
            return None, None
        except sqlite3.OperationalError as exc:
            logger.warning("[T0 Backtest] 结构快照不可用 %s %s: %s", symbol, date, exc)
            return None, None
        finally:
            conn.close()

    # 4. 初始化状态机
    machine = T0StateMachine(symbol=config.symbol, t0_qty=config.t0_qty)

    # 确保 paper 账户存在
    if config.use_paper_db:
        get_or_create_t0_account(user_id=1)

    # 回测结果收集
    all_trades: list[dict] = []
    daily_summary: list[dict] = []
    daily_pnls: list[float] = []

    # 按日遍历
    trading_days = sorted(daily_1m.keys())
    for date in trading_days:
        machine.reset_daily(date)
        bars_1m = daily_1m[date]

        # 当日 ZG/ZD
        pivot_zd, pivot_zg = get_pivot(config.symbol, date)

        # 当日 5M K线（用于 ATR）
        bars_5m_today = [k for k in klines_5m if str(k["date"])[:10] == date]
        atr_5m = _estimate_atr_5m(bars_5m_today)

        day_signals = 0
        day_fills = 0
        cumulative_1m: list[dict] = []

        for bar in bars_1m:
            bar_time = str(bar.get("date", ""))
            # 14:55 以后强制平仓
            hhmm = bar_time[11:16] if len(bar_time) > 10 else ""
            if hhmm >= "14:55":
                result = machine.force_sweep(bar["close"])
                if result.signal == "SWEEP":
                    day_signals += 1
                    day_fills += 1
                    if config.use_paper_db:
                        _safe_record(config, result, bar["close"], 1)
                break

            cumulative_1m.append(bar)
            # 至少需要 3 根已收盘 K 线才能判断分型
            if len(cumulative_1m) < 3:
                continue

            result = machine.tick(
                current_price=bar["close"],
                timestamp=bar_time,
                pivot_zd=pivot_zd,
                pivot_zg=pivot_zg,
                klines_1m=cumulative_1m[-20:],  # 最近 20 根
                atr_5m=atr_5m,
            )

            if result.signal:
                day_signals += 1
                day_fills += 1
                trade_record = {
                    "date": date,
                    "time": bar_time,
                    "signal": result.signal,
                    "price": bar["close"],
                    "qty": result.signal_qty or config.t0_qty,
                    "entry_price": result.entry_price,
                    "daily_pnl": result.daily_pnl,
                    "state": result.state,
                    "reason": result.reason,
                }
                all_trades.append(trade_record)

                if config.use_paper_db:
                    _safe_record(config, result, bar["close"], day_fills)

        # 日终状态快照
        day_net_pnl = machine._daily_pnl
        daily_pnls.append(day_net_pnl)
        daily_summary.append({
            "date": date,
            "signals": day_signals,
            "fills": day_fills,
            "net_pnl": round(day_net_pnl, 2),
            "stop_count": machine._daily_stop_count,
            "final_state": machine._state.value,
        })

    # 5. 汇总统计
    win_days = [p for p in daily_pnls if p > 0]
    loss_days = [p for p in daily_pnls if p <= 0]
    net_pnl = sum(daily_pnls)

    # 最大回撤
    max_drawdown = _max_drawdown(daily_pnls)

    # Sharpe（日收益序列，假设无风险利率 0）
    sharpe = None
    if len(daily_pnls) >= 5:
        try:
            mean_pnl = statistics.mean(daily_pnls)
            std_pnl = statistics.stdev(daily_pnls)
            if std_pnl > 0:
                sharpe = round(mean_pnl / std_pnl * math.sqrt(252), 2)
        except Exception:
            pass

    # 总信号数和成交数（计算 SELL/BUY 完整笔数）
    buy_signals = [t for t in all_trades if "BUY" in t["signal"]]
    sell_signals = [t for t in all_trades if "SELL" in t["signal"] or "STOP" in t["signal"] or t["signal"] == "SWEEP"]

    return BacktestResult(
        symbol=config.symbol,
        trading_days=len(trading_days),
        total_signals=len(all_trades),
        total_fills=len(all_trades),
        win_count=len(win_days),
        loss_count=len(loss_days),
        gross_pnl=round(net_pnl + sum(t.get("fees", 0) for t in all_trades), 2),
        total_fees=0.0,  # 费用已含在 daily_pnl 中
        net_pnl=round(net_pnl, 2),
        max_drawdown=round(max_drawdown, 2),
        sharpe_daily=sharpe,
        trades=all_trades,
        daily_summary=daily_summary,
    )


def print_backtest_report(result: BacktestResult) -> str:
    """打印可读的回测报告。"""
    lines = [
        f"{'='*60}",
        f"T+0 做T教练回测报告 — {result.symbol}",
        f"{'='*60}",
        f"交易日数:     {result.trading_days}",
        f"总信号数:     {result.total_signals}",
        f"盈利天数:     {result.win_count}",
        f"亏损天数:     {result.loss_count}",
        f"日胜率:       {result.win_count / max(result.trading_days, 1) * 100:.1f}%",
        f"净 PnL:       ¥{result.net_pnl:,.2f}",
        f"最大回撤:     ¥{result.max_drawdown:,.2f}",
        f"日 Sharpe:    {result.sharpe_daily if result.sharpe_daily else 'N/A'}",
        f"{'='*60}",
        "每日汇总:",
    ]
    for day in result.daily_summary:
        pnl_str = f"+{day['net_pnl']:.0f}" if day["net_pnl"] >= 0 else f"{day['net_pnl']:.0f}"
        lines.append(
            f"  {day['date']}  {pnl_str:>8}元  "
            f"{day['signals']}信号  止损{day['stop_count']}次  最终:{day['final_state']}"
        )
    report = "\n".join(lines)
    print(report)
    return report


# ------------------------------------------------------------------ #
# 内部辅助
# ------------------------------------------------------------------ #

def _estimate_atr_5m(bars_5m: list[dict], period: int = 14) -> float:
    """简单估算 5M ATR。"""
    if len(bars_5m) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars_5m)):
        prev_close = bars_5m[i - 1]["close"]
        curr = bars_5m[i]
        tr = max(
            curr["high"] - curr["low"],
            abs(curr["high"] - prev_close),
            abs(curr["low"] - prev_close),
        )
        trs.append(tr)
    if not trs:
        return 0.0
    use_trs = trs[-period * 2:]
    if len(use_trs) < period:
        return sum(use_trs) / len(use_trs)
    atr = sum(use_trs[:period]) / period
    for tr in use_trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def _max_drawdown(daily_pnls: list[float]) -> float:
    """计算最大单日亏损（绝对值）。"""
    if not daily_pnls:
        return 0.0
    return abs(min(daily_pnls, default=0.0))


def _safe_record(config: BacktestConfig, result, price: float, trade_num: int):
    """安全调用 paper service，异常时记录日志不中断回测。"""
    try:
        from server.engines.t0.t0_paper_service import record_t0_signal
        if result.signal:
            record_t0_signal(
                user_id=1,
                symbol=config.symbol,
                signal=result.signal,
                signal_price=price,
                t0_qty=result.signal_qty or config.t0_qty,
                tick_result=result,
                run_id=f"backtest_{config.symbol}_{config.start_date}",
            )
    except Exception as exc:
        logger.warning("[T0 Backtest] paper记录失败: %s", exc)
