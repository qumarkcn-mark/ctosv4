"""推送规则层：把提醒触发条件从 worker 剥离出来。

这里不读写数据库、不发送消息，只产出“候选提醒”和文案。worker 负责调度、
去重入库和真实推送，这样雷达/缠论算法换代时不会牵动外围基础设施。
"""

from dataclasses import dataclass, field
from typing import Optional

from server.engines.decision.strategy_definitions import build_strategy_contract


@dataclass(frozen=True)
class PushAlertCandidate:
    """一次可推送的提醒候选。"""

    alert_type: str
    trigger_price: float = 0.0
    dedupe_node: str = ""
    cooldown_hours: int = 24
    extra: dict = field(default_factory=dict)


def build_alert_strategy_contract(alert_type: str, strategy_type: str = "") -> Optional[dict]:
    """把提醒触发点映射到 Strategy Contract，便于复盘时锁定版本。"""
    if alert_type == "CHAN_ENTRY_SIGNAL":
        if strategy_type == "战法二":
            return build_strategy_contract("war2_trend_step", "TRIGGERED")
        if strategy_type in ("战法一", "双战法"):
            conditions = []
            if strategy_type == "双战法":
                conditions.append({"condition_id": "war2_trend_step_also_triggered", "status": "PASS"})
            return build_strategy_contract("war1_third_buy", "TRIGGERED", conditions)

    if alert_type == "SCANNER_TOP_CANDIDATE":
        if strategy_type in ("war2", "战法二", "war2_trend_step"):
            return build_strategy_contract("war2_trend_step", "TRIGGERED")
        return build_strategy_contract("war1_third_buy", "TRIGGERED")

    if alert_type in ("CHAN_THIRD_BUY", "CHAN_30M_BOT_DIV"):
        return build_strategy_contract("war1_third_buy", "TRIGGERED")

    if alert_type in (
        "CHAN_30M_TOP_DIV",
        "STAGE_VALIDATION_FAIL",
        "STAGE_TIME_EXPIRED",
        "HOLDING_STAGE4",
        "HOLDING_STAGE5",
        "M5_STRUCTURE_BROKEN",
        "TRAILING_STOP_BROKEN",
        "STOP_LOSS_BROKEN",
        "STOP_LOSS_WARNING",
    ):
        return build_strategy_contract("holding_stage_manager", "TRIGGERED")

    return None


def append_risk_disclaimer(message: str) -> str:
    """所有交易相关提醒都必须带风险声明。"""
    if not message or "仅供参考" in message:
        return message
    return f"{message} 仅供参考，不构成交易建议。"


def evaluate_price_alerts(position: dict, current_price: float) -> list[PushAlertCandidate]:
    """评估持仓价位类提醒：止损、台阶止损、5分结构失效。"""
    if current_price <= 0:
        return []

    stop_loss = float(position.get("stop_loss_price") or 0)
    trailing_stop = float(position.get("trailing_stop_price") or 0)
    m5_entry_zg = float(position.get("m5_entry_zg") or 0)
    if stop_loss <= 0 and trailing_stop <= 0 and m5_entry_zg <= 0:
        return []

    effective_stop = max(stop_loss, trailing_stop)
    candidates: list[PushAlertCandidate] = []

    if effective_stop > 0 and current_price <= effective_stop:
        alert_type = "TRAILING_STOP_BROKEN" if trailing_stop > stop_loss else "STOP_LOSS_BROKEN"
        candidates.append(
            PushAlertCandidate(
                alert_type=alert_type,
                trigger_price=current_price,
                dedupe_node=f"price:{alert_type}:{effective_stop:.3f}",
                extra={"trailing_stop": trailing_stop, "effective_stop": effective_stop},
            )
        )
        return candidates

    if effective_stop > 0 and current_price <= effective_stop * 1.03:
        candidates.append(
            PushAlertCandidate(
                alert_type="STOP_LOSS_WARNING",
                trigger_price=current_price,
                dedupe_node=f"price:STOP_LOSS_WARNING:{effective_stop:.3f}",
                extra={"trailing_stop": trailing_stop, "effective_stop": effective_stop},
            )
        )

    # 结构失效比止损更早出现，只在尚未跌破防守线时发出。
    if m5_entry_zg > 0 and current_price < m5_entry_zg:
        candidates.append(
            PushAlertCandidate(
                alert_type="M5_STRUCTURE_BROKEN",
                trigger_price=current_price,
                dedupe_node=f"structure:M5_ZG:{m5_entry_zg:.3f}",
                extra={"m5_entry_zg": m5_entry_zg},
            )
        )

    return candidates


def evaluate_scanner_candidate_alert(result: dict, min_score: float = 80.0) -> Optional[PushAlertCandidate]:
    """扫描器重点候选推送：只输出高分 ready 候选，具体发送由 worker/API 执行。"""
    if (result.get("status") or "ready") != "ready":
        return None

    score = float(result.get("score") or 0)
    if score < min_score:
        return None

    strategy = result.get("strategy_code") or result.get("strategy") or "war1"
    return PushAlertCandidate(
        alert_type="SCANNER_TOP_CANDIDATE",
        trigger_price=float(result.get("close") or 0),
        dedupe_node=f"scanner:{result.get('symbol', '')}:{strategy}:{score:.1f}",
        extra={"score": score, "strategy": strategy},
    )


def build_alert_message(
    alert_type: str,
    *,
    name: str,
    current_price: float = 0.0,
    stop_loss_price: float = 0.0,
    strategy_type: str = "",
    beichi_type: str = "",
    trailing_stop: float = 0.0,
    m5_entry_zg: float = 0.0,
    score: float = 0.0,
) -> str:
    """生成符合交易教练语气的提醒文案。"""
    if alert_type == "STOP_LOSS_BROKEN":
        msg = f"【止损击穿】{name} 现价 {current_price:.2f} 已跌破防守价 {stop_loss_price:.2f}，请检查原止损预案。"
    elif alert_type == "STOP_LOSS_WARNING":
        msg = f"【接近止损】{name} 现价 {current_price:.2f} 已逼近防守价 {stop_loss_price:.2f}，请提前检查风险线。"
    elif alert_type == "TRAILING_STOP_BROKEN":
        stop_str = f"{trailing_stop:.2f}" if trailing_stop else "台阶线"
        msg = f"【台阶止损触发】{name} 现价 {current_price:.2f} 跌破台阶止损 {stop_str}，请复核利润保护预案。"
    elif alert_type == "M5_STRUCTURE_BROKEN":
        msg = (
            f"【结构失效】{name} 现价 {current_price:.2f} 跌回5分入场中枢ZG {m5_entry_zg:.2f} 下方，"
            "入场假设需要复核。"
        )
    elif alert_type == "CHAN_THIRD_BUY":
        msg = f"{name} 日线三买确认，关注结构确立后的入场条件。"
    elif alert_type == "CHAN_30M_TOP_DIV":
        if beichi_type == "转折":
            msg = f"{name} 30分顶背驰转折确认，请检查日线结构和持仓防守线。"
        elif beichi_type == "中继":
            msg = f"{name} 30分顶背驰偏中继，可能进入震荡，请继续观察结构变化。"
        else:
            msg = f"{name} 30分顶背驰出现，请确认是否转折并检查雷达。"
    elif alert_type == "CHAN_30M_BOT_DIV":
        msg = f"{name} 30分底背驰出现，入场条件有进展，请检查雷达。"
    elif alert_type == "CHAN_ENTRY_SIGNAL":
        if strategy_type == "战法一":
            label = "战法一（三级别共振）"
        elif strategy_type == "战法二":
            label = "战法二（中枢上沿突破）"
        elif strategy_type == "双战法":
            label = "战法一+战法二（双确认）"
        else:
            label = "入场条件"
        msg = f"{name} {label}信号触发，五条件已满足，请检查雷达确认。"
    elif alert_type == "STAGE_VALIDATION_FAIL":
        msg = f"{name} 预案失效，30分未走出预期上涨笔，入场假设不成立。"
    elif alert_type == "STAGE_TIME_EXPIRED":
        msg = f"{name} 走势验证超时，结构迟滞，请复核原入场假设。"
    elif alert_type == "HOLDING_STAGE4":
        stop_str = f"，台阶止损 {trailing_stop:.2f}" if trailing_stop else ""
        msg = f"{name} 30分顶背驰已确认为转折型{stop_str}，请检查分批降风险预案。"
    elif alert_type == "HOLDING_STAGE5":
        stop_str = f"，台阶止损 {trailing_stop:.2f}" if trailing_stop else ""
        msg = f"{name} 日线顶背驰确认{stop_str}，请检查退出预案和风险暴露。"
    elif alert_type == "SCANNER_TOP_CANDIDATE":
        score_str = f"评分 {score:.0f}" if score else "重点候选"
        msg = f"{name} 进入扫描器重点候选（{score_str}），请打开雷达复核结构、止损和赔率。"
    else:
        msg = f"{name} 触发 {alert_type} 提醒，请检查交易计划。"

    return append_risk_disclaimer(msg)
