#!/usr/bin/env python3
"""Debug script for backtest data retrieval"""

import subprocess
import json
import time

GAMMA_API = "https://gamma-api.polymarket.com"

def curl_get(url):
    """Make HTTP request using curl"""
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "30", url],
            capture_output=True,
            text=True,
            timeout=35
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception as e:
        print(f"Error: {e}")
    return None

def main():
    now_ts = int(time.time())
    
    print(f"Current timestamp: {now_ts}")
    print("=" * 60)
    
    # Test a few different time slots
    test_offsets = [
        -600,   # 10 min ago
        -1800,  # 30 min ago
        -3600,  # 1 hour ago
        -7200,  # 2 hours ago
    ]
    
    for offset in test_offsets:
        target_ts = now_ts + offset
        slot_ts = (target_ts // 300) * 300
        slug = f"btc-updown-5m-{slot_ts}"
        
        print(f"\nChecking {offset//60} minutes ago: {slug}")
        
        url = f"{GAMMA_API}/events?slug={slug}"
        data = curl_get(url)
        
        if data and len(data) > 0:
            event = data[0]
            print(f"  Title: {event.get('title')}")
            print(f"  Event closed: {event.get('closed')}")
            print(f"  Event active: {event.get('active')}")
            
            markets = event.get("markets", [])
            if markets:
                m = markets[0]
                print(f"  Market closed: {m.get('closed')}")
                print(f"  outcomePrices: {m.get('outcomePrices')}")
        else:
            print(f"  No data found")

if __name__ == "__main__":
    main()