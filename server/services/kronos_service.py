import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from server.services.price_service import get_daily_klines, get_minute_klines
import asyncio

logger = logging.getLogger(__name__)


class KronosUnavailable(RuntimeError):
    """Kronos 是可选重模型，缺依赖或权重不可用时用明确异常表达。"""


class KronosService:
    _instance = None
    _model = None
    _tokenizer = None
    _predictor = None
    _last_error = None
    _last_error_at = None
    device = "unknown"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(KronosService, cls).__new__(cls)
        return cls._instance

    def _init_model(self):
        """延迟初始化模型以节省启动内存"""
        if self._model is not None:
            return

        model_name, tokenizer_name = self._model_names()

        logger.info(f"Initializing Kronos model: {model_name}")
        try:
            import torch
            from server.libs.kronos.kronos import Kronos, KronosTokenizer, KronosPredictor

            self._tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
            self._model = Kronos.from_pretrained(model_name)

            # 自动选择设备 (MPS > CUDA > CPU)
            device_str = "cpu"
            if torch.backends.mps.is_available():
                device_str = "mps"
            elif torch.cuda.is_available():
                device_str = "cuda"

            self.device = torch.device(device_str)
            self._model.to(self.device)
            self._predictor = KronosPredictor(self._model, self._tokenizer, max_context=2048)
            self._last_error = None
            self._last_error_at = None
            logger.info(f"Kronos model loaded on {self.device}")
        except Exception as e:
            self._mark_unavailable(e)
            logger.error(f"Failed to load Kronos model: {self._last_error}")
            raise KronosUnavailable(self._last_error) from e

    def _model_names(self) -> tuple[str, str]:
        model_name = os.getenv("KRONOS_MODEL_NAME", "NeoQuasar/Kronos-mini")
        tokenizer_name = "NeoQuasar/Kronos-Tokenizer-2k" if "mini" in model_name else "NeoQuasar/Kronos-Tokenizer-base"
        return model_name, tokenizer_name

    def _mark_unavailable(self, exc: Exception) -> None:
        self._last_error = f"{exc.__class__.__name__}: {exc}"
        self._last_error_at = datetime.now(timezone.utc).isoformat()

    def status(self) -> Dict[str, Any]:
        """返回 Kronos 可用性状态，避免 API 直接窥探私有模型字段。"""
        model_name, tokenizer_name = self._model_names()
        loaded = self._model is not None and self._predictor is not None
        return {
            "status": "online" if loaded else "offline",
            "loaded": loaded,
            "available": loaded and self._last_error is None,
            "device": str(getattr(self, "device", "unknown")),
            "model_name": model_name,
            "tokenizer_name": tokenizer_name,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    async def get_forecast(self, symbol: str, interval: str = "day", lookback: int = 400, pred_len: int = 10) -> Optional[Dict[str, Any]]:
        """获取指定股票在特定级别的动力学预测和合力分"""
        try:
            self._init_model()
            try:
                import pandas as pd
            except Exception as e:
                self._mark_unavailable(e)
                raise KronosUnavailable(self._last_error) from e

            # 1. 获取真实历史数据 (根据级别选择接口)
            if interval == "day":
                klines = await get_daily_klines(symbol, count=lookback + 50)
            elif interval == "week":
                # 获取周线数据
                from server.services.price_service import get_weekly_klines
                klines = await get_weekly_klines(symbol, count=lookback + 50)
            else:
                # 转换 interval 格式 (FastAPI 习惯使用 "30" 或 "m30")
                freq = interval if interval.startswith("m") else f"m{interval}"
                klines = await get_minute_klines(symbol, interval=freq, count=lookback + 50)

            if not klines or len(klines) < 10:
                logger.warning(f"Insufficient klines for {symbol} at {interval}")
                return None

            df = pd.DataFrame(klines)
            df['date'] = pd.to_datetime(df['date'])

            # 2. 准备预测输入
            x_df = df.tail(lookback).copy()
            x_timestamp = x_df['date']

            # 生成未来时间戳 (根据级别计算步长)
            last_ts = x_timestamp.iloc[-1]
            if interval == "day":
                delta = timedelta(days=1)
            elif interval == "week":
                delta = timedelta(days=7)
            else:
                minutes = int(interval.replace("m", ""))
                delta = timedelta(minutes=minutes)

            y_timestamp = pd.Series([last_ts + delta * i for i in range(1, pred_len + 1)])

            # 3. 执行预测
            pred_df = self._predictor.predict(
                df=x_df[['open', 'high', 'low', 'close', 'volume']],
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=pred_len,
                T=1.0,
                top_p=0.9,
                sample_count=3,
                verbose=False
            )

            # 4. 计算合力分 (Force Score) + 序列形态特征
            start_price = x_df['close'].iloc[-1]
            end_price = pred_df['close'].iloc[-1]
            change_pct = (end_price - start_price) / start_price * 100

            # 序列形态分析——提取 shape_features 供 LLM 消费
            pred_closes = pred_df['close'].tolist()
            shape_features = self._compute_shape_features(start_price, pred_closes, interval)

            # 升级版 force_score：endpoint + shape 综合评分
            force_score = self._compute_force_score(change_pct, shape_features, interval)

            return {
                "symbol": symbol,
                "interval": interval,
                "current_price": start_price,
                "predicted_price": end_price,
                "change_pct": round(change_pct, 2),
                "force_score": round(force_score, 1),
                "shape_features": shape_features,
                "verdict": self._get_verdict(force_score),
                "last_date": str(last_ts),
                "forecast_data": pred_df.to_dict(orient="records")
            }

        except KronosUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error in Kronos forecasting for {symbol} at {interval}: {e}")
            return None

    async def get_multi_level_analysis(self, symbol: str) -> Optional[Dict[str, Any]]:
        """执行多级别联动分析 (周线 + 日线 + 30分 + 5分)"""
        intervals = ["week", "day", "30", "5"]
        tasks = [self.get_forecast(symbol, interval=it) for it in intervals]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        unavailable = [item for item in results if isinstance(item, KronosUnavailable)]
        if unavailable and len(unavailable) == len(results):
            raise unavailable[0]

        # 组装结果
        analysis = {}
        resonance_score = 0
        valid_count = 0

        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning(f"Kronos level {intervals[i]} failed for {symbol}: {res}")
                continue
            if res:
                analysis[intervals[i]] = res
                resonance_score += res["force_score"]
                valid_count += 1

        if valid_count == 0:
            return None

        avg_resonance = resonance_score / valid_count

        # 判断共振类型与小转大潜力
        week_score = analysis.get("week", {}).get("force_score", 0)
        day_score = analysis.get("day", {}).get("force_score", 0)
        m30_score = analysis.get("30", {}).get("force_score", 0)
        m5_score  = analysis.get("5", {}).get("force_score", 0)

        resonance_type = "Neutral (震荡蓄势)"
        s2l_potential = 0 # 小转大潜力分 (0-100)

        # 1. 大背景锚定 (Weekly Anchor)
        background = "Neutral"
        if week_score > 30: background = "Bullish Background (周线走强)"
        elif week_score < -30: background = "Bearish Background (周线走弱)"

        # 2. 标准共振 (加周线权重)
        if week_score > 20 and day_score > 20 and m30_score > 20:
            resonance_type = "Global Bullish Resonance (全局多头共振)"
        elif week_score < -20 and day_score < -20 and m30_score < -20:
            resonance_type = "Global Bearish Resonance (全局空头共振)"

        # 3. 小转大 (Small-to-Large) 逻辑挖掘
        if day_score <= 10:
            if m5_score > 60 and m30_score > 30:
                resonance_type = "Potential Small-to-Large (底向小转大)"
                s2l_potential = abs(m5_score - day_score)
            elif m5_score < -60 and m30_score < -30:
                resonance_type = "Potential Small-to-Large (顶向小转大)"
                s2l_potential = abs(m5_score - day_score)

        return {
            "symbol": symbol,
            "background": background,
            "resonance_score": round(avg_resonance, 1),
            "resonance_type": resonance_type,
            "s2l_potential": round(s2l_potential, 1),
            "levels": analysis
        }

    def _compute_shape_features(self, start_price: float, pred_closes: list, interval: str) -> dict:
        """从预测序列提取形态特征，解决 force_score 信息损失问题。

        解决核心问题：-2% 的 change_pct 可能是"单边下行"也可能是
        "先跌5%再回升3%"——形态完全不同但旧 force_score 看不出差异。
        """
        if not pred_closes or start_price <= 0:
            return {"pattern": "UNKNOWN", "confidence": "LOW"}

        n = len(pred_closes)
        full_series = [start_price] + pred_closes  # 包含起始价

        # 1. 逐步收益率序列
        returns = [(full_series[i] - full_series[i - 1]) / full_series[i - 1] * 100
                   for i in range(1, len(full_series))]

        # 2. 最大回撤 (从起点算)
        running_max = start_price
        max_drawdown_pct = 0.0
        max_drawdown_step = 0
        for i, price in enumerate(pred_closes, 1):
            running_max = max(running_max, price)
            dd = (running_max - price) / running_max * 100
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd
                max_drawdown_step = i

        # 3. 最大涨幅 (从最低点算)
        running_min = start_price
        max_rally_pct = 0.0
        max_rally_step = 0
        for i, price in enumerate(pred_closes, 1):
            running_min = min(running_min, price)
            rally = (price - running_min) / running_min * 100 if running_min > 0 else 0
            if rally > max_rally_pct:
                max_rally_pct = rally
                max_rally_step = i

        # 4. 单调性 (方向一致性: +1=全涨 -1=全跌 0=震荡)
        up_count = sum(1 for r in returns if r > 0)
        down_count = sum(1 for r in returns if r < 0)
        monotonicity = (up_count - down_count) / max(n, 1)

        # 5. 转折点数量
        turning_count = 0
        for i in range(1, len(returns) - 1):
            if returns[i - 1] * returns[i] < 0:  # 方向变化
                turning_count += 1

        # 6. 路径形态分类
        end_change = (pred_closes[-1] - start_price) / start_price * 100
        pattern = self._classify_pattern(
            end_change, max_drawdown_pct, max_rally_pct,
            monotonicity, turning_count, n, max_drawdown_step, max_rally_step,
        )

        # 7. 前半/后半动量对比
        mid = n // 2
        first_half_change = (pred_closes[mid - 1] - start_price) / start_price * 100 if mid > 0 else 0
        second_half_change = (pred_closes[-1] - pred_closes[mid - 1]) / pred_closes[mid - 1] * 100 if mid > 0 and pred_closes[mid - 1] > 0 else 0

        return {
            "pattern": pattern,
            "end_change_pct": round(end_change, 3),
            "max_drawdown_pct": round(max_drawdown_pct, 3),
            "max_drawdown_step": max_drawdown_step,
            "max_rally_pct": round(max_rally_pct, 3),
            "max_rally_step": max_rally_step,
            "monotonicity": round(monotonicity, 3),
            "turning_count": turning_count,
            "first_half_change_pct": round(first_half_change, 3),
            "second_half_change_pct": round(second_half_change, 3),
            "bar_count": n,
            "confidence": "MEDIUM" if n >= 5 else "LOW",
        }

    def _classify_pattern(
        self,
        end_change: float,
        max_dd: float,
        max_rally: float,
        monotonicity: float,
        turning_count: int,
        n: int,
        dd_step: int,
        rally_step: int,
    ) -> str:
        """将序列形态归类为人类可读的模式标签。"""
        # 强单边
        if monotonicity > 0.6 and end_change > 1.0:
            return "STEADY_UP"
        if monotonicity < -0.6 and end_change < -1.0:
            return "STEADY_DOWN"

        # 震荡收敛（优先于 V 形判断，因为频繁转向是震荡的核心特征）
        if turning_count >= 3 and abs(end_change) < 1.0:
            return "OSCILLATING"

        # V 形反转：先跌后涨（需要方向性恢复，非震荡）
        if max_dd > 1.5 and end_change > -0.3 and dd_step <= n * 0.6 and turning_count <= 2:
            return "V_RECOVERY"

        # 倒 V 形：先涨后跌
        if max_rally > 1.5 and end_change < 0.3 and rally_step <= n * 0.6 and turning_count <= 2:
            return "INVERTED_V"

        # 先跌企稳
        if max_dd > 1.0 and abs(end_change) < max_dd * 0.5 and dd_step <= n * 0.5:
            return "DROP_THEN_STABILIZE"

        # 先涨回落
        if max_rally > 1.0 and abs(end_change) < max_rally * 0.5 and rally_step <= n * 0.5:
            return "RALLY_THEN_PULLBACK"

        # 温和趋势
        if end_change > 0.5:
            return "MILD_UP"
        if end_change < -0.5:
            return "MILD_DOWN"

        return "SIDEWAYS"

    def _compute_force_score(self, change_pct: float, shape_features: dict, interval: str) -> float:
        """升级版 force_score：结合终点变化 + 形态质量。

        核心改进：同样的 -2% endpoint，如果是 V_RECOVERY 则
        force_score 会比 STEADY_DOWN 弱很多（因为有反弹动能）。
        """
        # 基础分：沿用旧逻辑 (endpoint direction)
        sensitivity = 10 if interval in ("day", "week") else 50
        base_score = change_pct * sensitivity

        # 形态修正因子
        pattern = shape_features.get("pattern", "UNKNOWN")
        monotonicity = shape_features.get("monotonicity", 0)

        # 单调性加权：方向越一致，基础分权重越高
        mono_weight = 0.5 + 0.5 * abs(monotonicity)  # 0.5 ~ 1.0

        # 形态修正：某些形态削弱或翻转基础分的含义
        pattern_modifier = {
            "STEADY_UP": 1.0,
            "STEADY_DOWN": 1.0,
            "V_RECOVERY": 0.3,         # 先跌后涨，endpoint 的空头含义大幅削弱
            "INVERTED_V": 0.3,          # 先涨后跌，endpoint 的多头含义大幅削弱
            "DROP_THEN_STABILIZE": 0.5, # 跌后企稳，空头含义减半
            "RALLY_THEN_PULLBACK": 0.5, # 涨后回落，多头含义减半
            "OSCILLATING": 0.4,         # 震荡，任何方向性都要削弱
            "MILD_UP": 0.8,
            "MILD_DOWN": 0.8,
            "SIDEWAYS": 0.3,
        }.get(pattern, 0.7)

        # 最终 force_score = base * mono_weight * pattern_modifier
        adjusted = base_score * mono_weight * pattern_modifier

        return max(min(adjusted, 100), -100)

    def _get_verdict(self, score: float) -> str:
        if score > 20: return "Bullish Force (强力向上)"
        if score > 5: return "Positive Force (偏向多头)"
        if score < -20: return "Bearish Force (强力向下)"
        if score < -5: return "Negative Force (偏向空头)"
        return "Neutral Force (震荡蓄势)"

# 创建全局单例
kronos_service = KronosService()
