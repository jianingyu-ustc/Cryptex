#!/usr/bin/env python3
"""Debug script to test API connectivity"""

import asyncio
import httpx

async def test():
    async with httpx.AsyncClient(timeout=30) as client:
        print("Testing Polymarket API...")
        
        # Search active bitcoin events
        resp = await client.get("https://gamma-api.polymarket.com/events", params={
            "_q": "bitcoin up or down",
            "active": "true",
            "closed": "false",
            "limit": 5
        })
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"Found {len(data)} events")
            
            for e in data[:3]:
                title = e.get("title", "N/A")
                end_date = e.get("endDate", "N/A")
                closed = e.get("closed", "N/A")
                markets = e.get("markets", [])
                
                print(f"\n  Title: {title}")
                print(f"  endDate: {end_date}, closed: {closed}")
                print(f"  Markets: {len(markets)}")
                
                if markets:
                    m = markets[0]
                    print(f"    Q: {m.get('question', 'N/A')[:60]}")
                    print(f"    active: {m.get('active')}, closed: {m.get('closed')}")
                    print(f"    endDate: {m.get('endDate')}")
        else:
            print(f"Error: {resp.status_code}")

if __name__ == "__main__":
    asyncio.run(test())