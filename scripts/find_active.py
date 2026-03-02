#!/usr/bin/env python3
"""Find active BTC Up/Down markets using timestamp-based slugs"""
import subprocess
import json
from datetime import datetime, timezone

def curl_get(url):
    try:
        result = subprocess.run(["curl", "-s", "-m", "30", url], capture_output=True, text=True, timeout=35)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except:
        pass
    return None

now = datetime.now(timezone.utc)
ts = int(now.timestamp())
print(f"Current UTC: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Current Unix timestamp: {ts}")

# The 5-minute market timestamp is rounded to 5-minute intervals
current_slot = (ts // 300) * 300
print(f"Current 5min slot timestamp: {current_slot}")

# Try several slots around current time
for offset in [0, 300, 600, 900, -300]:
    slot_ts = current_slot + offset
    slug = f"btc-updown-5m-{slot_ts}"
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    print(f"\nTrying slug: {slug}")
    data = curl_get(url)
    if data and len(data) > 0:
        e = data[0]
        print(f"  Found: {e.get('title', 'N/A')}")
        print(f"  Closed: {e.get('closed')}, Active: {e.get('active')}")
        print(f"  EndDate: {e.get('endDate', 'N/A')[:19]}")
    else:
        print("  No events found")