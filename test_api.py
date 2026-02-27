#!/usr/bin/env python3
"""Test script to find active Bitcoin up/down markets"""

import subprocess
import json
from datetime import datetime, timezone

def curl_get(url):
    """Use curl to fetch data (works with system proxy)"""
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
    print("=" * 60)
    print("Testing Polymarket API for Bitcoin Up/Down Markets")
    print("=" * 60)
    
    now = datetime.now(timezone.utc)
    print(f"Current UTC time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test 1: Direct slug search for recent BTC 5m event
    print("\n--- Test 1: Search events by seriesSlug ---")
    url = "https://gamma-api.polymarket.com/events?seriesSlug=btc-up-or-down-5m&active=true&limit=5"
    data = curl_get(url)
    if data:
        print(f"Found {len(data)} events")
        btc_events = [e for e in data if 'bitcoin' in e.get('title', '').lower()]
        print(f"BTC events: {len(btc_events)}")
        for e in btc_events[:3]:
            print(f"  Title: {e.get('title', 'N/A')[:60]}")
            print(f"  Closed: {e.get('closed')}, EndDate: {e.get('endDate', 'N/A')[:19]}")
    else:
        print("No data returned")
    
    # Test 2: Search by text query
    print("\n--- Test 2: Search events by _q ---")
    url = "https://gamma-api.polymarket.com/events?_q=bitcoin+up+down&active=true&limit=20"
    data = curl_get(url)
    if data:
        print(f"Found {len(data)} events total")
        # Filter for BTC up/down markets
        btc_markets = []
        for e in data:
            title = e.get('title', '')
            if 'Bitcoin Up or Down' in title:
                btc_markets.append(e)
        print(f"BTC Up or Down events: {len(btc_markets)}")
        for e in btc_markets[:3]:
            print(f"  Title: {e.get('title', 'N/A')}")
            print(f"  Closed: {e.get('closed')}, EndDate: {e.get('endDate', 'N/A')[:19]}")
    else:
        print("No data returned")
    
    # Test 3: Get markets directly
    print("\n--- Test 3: Search markets directly ---")
    url = "https://gamma-api.polymarket.com/markets?_q=bitcoin+up+down&active=true&closed=false&limit=30"
    data = curl_get(url)
    if data:
        print(f"Found {len(data)} markets total")
        btc_markets = [m for m in data if 'bitcoin' in m.get('question', '').lower() and 'up' in m.get('question', '').lower()]
        print(f"BTC Up/Down markets: {len(btc_markets)}")
        for m in btc_markets[:5]:
            q = m.get('question', 'N/A')
            end = m.get('endDate', 'N/A')
            closed = m.get('closed')
            active = m.get('active')
            print(f"  Q: {q[:60]}")
            print(f"    active={active}, closed={closed}, end={end[:19] if end else 'N/A'}")
    else:
        print("No data returned")

if __name__ == "__main__":
    main()