"""投资行为缺陷评测引擎 — 纯算法内核

从 trades 表中提取全部已完成的交易对 (BUY→SELL)，
计算 8 大纪律指标 + 1 个综合评分。
"""

import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TradePair:
    """一次完整的买入→卖出交易对"""
    symbol: str
    name: Optional[str]
    buy_price: float
    sell_price: float
    quantity: int
    buy_date: str
    sell_date: str
    reason_category: Optional[str]
    trend_direction: Optional[str]
    stop_loss_price: Optional[float]
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0

    def __post_init__(self):
        self.pnl = (self.sell_price - self.buy_price) * self.quantity
        self.pnl_pct = (self.sell_price - self.buy_price) / self.buy_price * 100 if self.buy_price > 0 else 0
        try:
            bd = datetime.fromisoformat(self.buy_date)
            sd = datetime.fromisoformat(self.sell_date)
            self.hold_days = max((sd - bd).days, 0)
        except (ValueError, TypeError):
            self.hold_days = 0


@dataclass
class BehaviorReport:
    """行为体检报告"""
    total_pairs: int = 0
    win_count: int = 0
    lose_count: int = 0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    avg_hold_days: float = 0.0
    max_drawdown: float = 0.0
    stop_loss_execution_rate: float = 0.0
    counter_trend_rate: float = 0.0
    early_exit_count: int = 0
    impulse_trade_rate: float = 0.0
    discipline_score: int = 0
    pairs: list = field(default_factory=list)


def pair_trades(rows: list[dict]) -> list[TradePair]:
    """
    将原始交易记录配对为 BUY→SELL 对。
    使用 FIFO 先进先出法：最先买入的股，最先与卖出配对。
    只计算已完成的交易对（未卖出的持仓不纳入）。
    """
    # 按股票分组
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(r)

    pairs = []
    for sym, trades in by_symbol.items():
        buy_queue = []  # FIFO 买入队列
        for t in sorted(trades, key=lambda x: x["traded_at"]):
            if t["direction"] == "BUY":
                buy_queue.append(t)
            elif t["direction"] == "SELL" and buy_queue:
                sell_remaining = t["quantity"]
                while sell_remaining > 0 and buy_queue:
                    buy = buy_queue[0]
                    match_qty = min(buy["quantity"], sell_remaining)

                    pairs.append(TradePair(
                        symbol=sym,
                        name=t.get("name"),
                        buy_price=buy["price"],
                        sell_price=t["price"],
                        quantity=match_qty,
                        buy_date=buy["traded_at"],
                        sell_date=t["traded_at"],
                        reason_category=buy.get("reason_category"),
                        trend_direction=buy.get("trend_direction"),
                        stop_loss_price=buy.get("stop_loss_price"),
                    ))

                    buy["quantity"] -= match_qty
                    sell_remaining -= match_qty
                    if buy["quantity"] <= 0:
                        buy_queue.pop(0)

    return pairs


def analyze(rows: list[dict], alert_count: int = 0) -> BehaviorReport:
    """
    核心分析函数。

    Args:
        rows: trades 表的全部记录 (dict 列表)
        alert_count: 该用户历史上触发的止损预警总次数 (从 alerts 表查)
    """
    pairs = pair_trades(rows)
    report = BehaviorReport(pairs=pairs)

    if not pairs:
        report.discipline_score = 50  # 无数据时给中性分
        return report

    report.total_pairs = len(pairs)

    # ── 1. 胜率 ──
    wins = [p for p in pairs if p.pnl > 0]
    losses = [p for p in pairs if p.pnl <= 0]
    report.win_count = len(wins)
    report.lose_count = len(losses)
    report.win_rate = round(len(wins) / len(pairs) * 100, 1)

    # ── 2. 盈亏比 ──
    avg_win = sum(p.pnl for p in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(p.pnl for p in losses) / len(losses)) if losses else 1
    report.profit_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    # ── 3. 平均持仓天数 ──
    report.avg_hold_days = round(sum(p.hold_days for p in pairs) / len(pairs), 1)

    # ── 4. 最大回撤 ──
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in sorted(pairs, key=lambda x: x.sell_date):
        cumulative += p.pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    report.max_drawdown = round(max_dd, 2)

    # ── 5. 止损执行率 ──
    # 触发了止损预警后，是否真的在止损价附近卖出了？
    stop_loss_sells = sum(1 for p in pairs if p.stop_loss_price and p.sell_price <= p.stop_loss_price * 1.02)
    report.stop_loss_execution_rate = round(
        stop_loss_sells / alert_count * 100, 1
    ) if alert_count > 0 else 100.0  # 从未触发预警视为满分

    # ── 6. 逆势交易占比 ──
    counter_trend = sum(1 for p in pairs if p.trend_direction and p.trend_direction.upper() in ("DOWN", "下跌"))
    report.counter_trend_rate = round(counter_trend / len(pairs) * 100, 1)

    # ── 7. 过早离场 ──
    report.early_exit_count = sum(1 for p in pairs if p.hold_days < 3 and p.pnl > 0)

    # ── 8. 冲动交易占比 ──
    impulse = sum(1 for p in pairs if p.reason_category == "FEELING")
    report.impulse_trade_rate = round(impulse / len(pairs) * 100, 1)

    # ── 综合纪律评分 (0-100) ──
    score = 100
    # 胜率维度 (权重 25)
    if report.win_rate < 30:
        score -= 25
    elif report.win_rate < 45:
        score -= 15
    elif report.win_rate < 55:
        score -= 5

    # 盈亏比维度 (权重 25)
    if report.profit_loss_ratio < 0.5:
        score -= 25
    elif report.profit_loss_ratio < 1.0:
        score -= 15
    elif report.profit_loss_ratio < 1.5:
        score -= 5

    # 止损纪律 (权重 20)
    if report.stop_loss_execution_rate < 30:
        score -= 20
    elif report.stop_loss_execution_rate < 60:
        score -= 10

    # 逆势交易 (权重 15)
    if report.counter_trend_rate > 50:
        score -= 15
    elif report.counter_trend_rate > 30:
        score -= 8

    # 冲动交易 (权重 15)
    if report.impulse_trade_rate > 50:
        score -= 15
    elif report.impulse_trade_rate > 25:
        score -= 8

    report.discipline_score = max(score, 0)
    return report
