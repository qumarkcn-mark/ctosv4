import json
from server.db.kline_lake import query_klines
from chan_engine.models import KLine, Direction, FenXingType
from chan_engine.parser import ChanParser

# Get some data
rows = query_klines('sh.600519', 'day', limit=150)
kline_objs = [KLine(date=r['date'],open=r['open'],close=r['close'],high=r['high'],low=r['low'],volume=r['volume']) for r in rows]
merged = ChanParser.merge_klines(kline_objs)
mk_idx = {id(mk): idx for idx, mk in enumerate(merged)}

fxs_valid = ChanParser.find_fenxings(merged, validate_bottom=True)

def my_build_bis(fenxings):
    if not fenxings: return []
    candidate_fx = fenxings[0]
    valid_bis = []
    print(f"Start with candidate: {candidate_fx.date} {candidate_fx.fx_type.name} {candidate_fx.high}/{candidate_fx.low}")
    for i in range(1, len(fenxings)):
        curr = fenxings[i]
        # same type
        if curr.fx_type == candidate_fx.fx_type:
            updated = False
            if curr.fx_type == FenXingType.TOP and curr.high > candidate_fx.high:
                print(f"  [REPLACE TOP] {candidate_fx.date}->{curr.date} (new high {curr.high} > {candidate_fx.high})")
                candidate_fx = curr
                updated = True
            elif curr.fx_type == FenXingType.BOTTOM and curr.low < candidate_fx.low:
                print(f"  [REPLACE BOT] {candidate_fx.date}->{curr.date} (new low {curr.low} < {candidate_fx.low})")
                candidate_fx = curr
                updated = True
                
            if updated and len(valid_bis) > 0 and valid_bis[-1][1].fx_type == candidate_fx.fx_type:
                # the candidate was the end of the last bi, so we must extend the last bi
                print(f"    -> [BI EXTENDED] updating last bi end to {candidate_fx.date}")
                valid_bis[-1] = (valid_bis[-1][0], candidate_fx)
            continue
            
        span = mk_idx[id(curr.k2)] - mk_idx[id(candidate_fx.k2)]
        if span < 4:
            print(f"  [SKIP SPAN < 4] {candidate_fx.date}({candidate_fx.fx_type.name}) -> {curr.date}({curr.fx_type.name}), span={span}")
            continue
            
        is_up = True if curr.fx_type == FenXingType.TOP else False
        if is_up and curr.high <= candidate_fx.low:
            print(f"  [SKIP PRICE LOW] UP bi {candidate_fx.date} -> {curr.date}, curr_high {curr.high} <= cand_low {candidate_fx.low}")
            continue
        elif not is_up and curr.low >= candidate_fx.high:
            print(f"  [SKIP PRICE HIGH] DN bi {candidate_fx.date} -> {curr.date}, curr_low {curr.low} >= cand_high {candidate_fx.high}")
            continue
            
        print(f"  [BI FORMED] {candidate_fx.date}({candidate_fx.fx_type.name}) -> {curr.date}({curr.fx_type.name})")
        valid_bis.append((candidate_fx, curr))
        candidate_fx = curr
    return valid_bis

print("\n--- TRACE ---")
bis = my_build_bis(fxs_valid)

print("\n--- FINAL BIS ---")
for s, e in bis:
    print(f"{s.date}({s.fx_type.name}) -> {e.date}({e.fx_type.name})")
