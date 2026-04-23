import asyncio
from server.services.chan_detail_service import get_chan_detail
from server.vendor.chan_py.Config.CChanConfig import CChanConfig

async def main():
    config = CChanConfig(bi_fx_check="loss")
    result = await get_chan_detail("sh.688008", "30m", config)
    bis = result.get("bis", [])
    print(f"Total bis: {len(bis)}")
    if len(bis) >= 4:
        for i, bi in enumerate(bis[-4:]):
            mom = bi.get("momentum", {})
            print(f"Bi {len(bis)-4+i}: dir={bi.get('dir')} y0={bi.get('y0')} y1={bi.get('y1')} date0={bi.get('date0')} date1={bi.get('date1')}")
            print(f"   Momentum: {mom}")
    else:
        print("Not enough bis.")

asyncio.run(main())
