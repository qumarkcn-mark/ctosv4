import asyncio
import json
import sys
import os

sys.path.append(os.getcwd())
from server.services.price_service import get_daily_klines

async def fetch():
    print("Fetching sz000001 daily limit=200...")
    klines = await get_daily_klines("sz000001", count=200)
    if klines:
        os.makedirs("tests/fixtures", exist_ok=True)
        with open("tests/fixtures/sz000001_daily.json", "w") as f:
            json.dump(klines, f, indent=2)
        print(f"Saved {len(klines)} klines")
    else:
        print("Failed to fetch")

asyncio.run(fetch())
