"""行为引擎单元测试 — 不依赖数据库，纯内存构造 fixture"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.behavior_engine import pair_trades, analyze, TradePair


def make_trade(symbol, direction, price, qty, traded_at,
               reason_category=None, trend_direction=None, stop_loss_price=None):
    return {
        "symbol": symbol, "name": "测试股", "direction": direction,
        "price": price, "quantity": qty, "traded_at": traded_at,
        "reason_category": reason_category, "trend_direction": trend_direction,
        "stop_loss_price": stop_loss_price,
    }


def test_basic_pair_matching():
    """FIFO 配对：1买1卖"""
    rows = [
        make_trade("sh600519", "BUY", 100.0, 100, "2025-01-01"),
        make_trade("sh600519", "SELL", 120.0, 100, "2025-01-15"),
    ]
    pairs = pair_trades(rows)
    assert len(pairs) == 1
    assert pairs[0].pnl == 2000.0  # (120-100)*100
    assert pairs[0].hold_days == 14
    print(f"✅ 基础配对: pnl={pairs[0].pnl}, hold={pairs[0].hold_days}d")


def test_partial_sell():
    """分批卖出配对"""
    rows = [
        make_trade("sz000001", "BUY", 10.0, 500, "2025-03-01"),
        make_trade("sz000001", "SELL", 12.0, 200, "2025-03-10"),
        make_trade("sz000001", "SELL", 11.0, 300, "2025-03-20"),
    ]
    pairs = pair_trades(rows)
    assert len(pairs) == 2
    assert pairs[0].quantity == 200
    assert pairs[1].quantity == 300
    print(f"✅ 分批卖出: {len(pairs)} 对, qty=[{pairs[0].quantity}, {pairs[1].quantity}]")


def test_full_report():
    """完整报告计算: 混合盈亏场景"""
    rows = [
        # 盈利交易
        make_trade("sh600519", "BUY", 100.0, 100, "2025-01-01", reason_category="CHAN_SIGNAL", trend_direction="UP"),
        make_trade("sh600519", "SELL", 130.0, 100, "2025-02-01"),
        # 亏损交易 (冲动 + 逆势)
        make_trade("sz000002", "BUY", 50.0, 200, "2025-02-05", reason_category="FEELING", trend_direction="DOWN"),
        make_trade("sz000002", "SELL", 40.0, 200, "2025-02-06"),  # 1天就跑了
        # 盈利交易
        make_trade("sh601318", "BUY", 80.0, 150, "2025-03-01", reason_category="CHAN_SIGNAL", trend_direction="UP"),
        make_trade("sh601318", "SELL", 95.0, 150, "2025-03-20"),
    ]
    report = analyze(rows, alert_count=2)

    assert report.total_pairs == 3
    assert report.win_count == 2
    assert report.lose_count == 1
    assert report.win_rate > 60  # 2/3 = 66.7%
    assert report.profit_loss_ratio > 1.0  # 赚多亏少
    assert report.counter_trend_rate > 0  # 有1笔逆势
    assert report.impulse_trade_rate > 0   # 有1笔冲动
    assert report.discipline_score > 0

    print(f"✅ 完整报告:")
    print(f"   胜率: {report.win_rate}%")
    print(f"   盈亏比: {report.profit_loss_ratio}")
    print(f"   持仓天数: {report.avg_hold_days}")
    print(f"   逆势占比: {report.counter_trend_rate}%")
    print(f"   冲动占比: {report.impulse_trade_rate}%")
    print(f"   纪律评分: {report.discipline_score}")


def test_empty_trades():
    """无交易记录场景"""
    report = analyze([], alert_count=0)
    assert report.discipline_score == 50
    print("✅ 空数据降级: score=50")


def test_coach_diagnosis():
    """教练诊断生成"""
    from server.services.behavior_coach import generate_diagnosis
    from server.services.behavior_engine import BehaviorReport

    bad_report = BehaviorReport(
        total_pairs=10, win_count=2, lose_count=8,
        win_rate=20.0, profit_loss_ratio=0.3,
        stop_loss_execution_rate=25.0,
        counter_trend_rate=60.0,
        impulse_trade_rate=50.0,
        early_exit_count=4,
        avg_hold_days=2.0,
        discipline_score=10,
    )
    diagnosis = generate_diagnosis(bad_report)

    critical_count = sum(1 for d in diagnosis if d["level"] == "critical")
    assert critical_count >= 3, f"严重问题应至少 3 个，实际 {critical_count}"

    print(f"✅ 教练诊断: {len(diagnosis)} 条建议, {critical_count} 条严重警告")
    for d in diagnosis:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵", "success": "🟢"}
        print(f"   {icon.get(d['level'], '⚪')} [{d['level']}] {d['title']}")


if __name__ == "__main__":
    test_basic_pair_matching()
    test_partial_sell()
    test_full_report()
    test_empty_trades()
    test_coach_diagnosis()
    print("\n🎉 全部测试通过！")
