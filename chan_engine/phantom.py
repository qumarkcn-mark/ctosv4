import random
from typing import List, Dict

def generate_phantom_klines(
    current_close: float,
    current_timestamp: int,
    period_ms: int, # e.g. 5M = 300000ms
    scenarios: List[Dict],
    atr: float = 0.0
) -> List[Dict]:
    """
    Takes exactly 3 scenarios logically output by the Commander and generates
    K-line geometry arrays [timestamp, open, high, low, close, volume] for each path.
    """
    
    # If no ATR provided, fallback to 1% of price as proxy
    if atr <= 0:
        atr = current_close * 0.01

    results = []

    for scenario in scenarios:
        periods = scenario.get("periods", 20)
        target_upper = scenario.get("price_target_upper", current_close)
        target_lower = scenario.get("price_target_lower", current_close)
        
        # Center of the target
        target_mid = (target_upper + target_lower) / 2.0
        
        # Volatility multiplier based on scenario type
        volatility = atr * 0.5
        if scenario.get("type") == "right_side_major_wave":
            volatility = atr * 1.5 # Violent breakout
        elif scenario.get("type") == "zhongshu_oscillation":
            volatility = atr * 0.8
        elif scenario.get("type") == "structural_breakdown":
            volatility = atr * 2.0 # Panic selling

        phantom_bars = []
        prev_close = current_close
        ts = current_timestamp

        for i in range(1, periods + 1):
            ts += period_ms
            
            # Linear interpolation step towards the target
            progress = i / periods
            expected_price = current_close + (target_mid - current_close) * progress
            
            # Add noise
            noise = random.uniform(-volatility, volatility)
            sim_close = expected_price + noise
            sim_open = prev_close
            
            # For pure structural blocks, wicks could just be equal to open/close
            # But we might want some minor wicks
            sim_high = max(sim_open, sim_close) + abs(random.uniform(0, volatility * 0.2))
            sim_low = min(sim_open, sim_close) - abs(random.uniform(0, volatility * 0.2))
            
            phantom_bars.append({
                "timestamp": ts,
                "open": round(sim_open, 3),
                "high": round(sim_high, 3),
                "low": round(sim_low, 3),
                "close": round(sim_close, 3),
                "volume": 0
            })
            
            prev_close = sim_close
            
        # Attach the simulated geometry to the scenario response
        scenario["phantom_geometry"] = phantom_bars
        results.append(scenario)

    return results
