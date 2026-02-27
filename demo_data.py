"""
Demo Data Generator for Polymarket Crypto Predictor
Generates realistic simulated data when API is unavailable
"""

import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any


class DemoDataGenerator:
    """Generate realistic demo data for crypto prediction markets"""
    
    # Realistic crypto market templates
    MARKET_TEMPLATES = [
        # BTC Markets
        {
            "crypto": "BTC",
            "timeframe": "5min",
            "question": "Will Bitcoin price be above ${price} in 5 minutes?",
            "base_price": 95000,
            "volatility": 0.02
        },
        {
            "crypto": "BTC",
            "timeframe": "15min",
            "question": "Will Bitcoin rise more than 0.5% in the next 15 minutes?",
            "base_price": 95000,
            "volatility": 0.03
        },
        {
            "crypto": "BTC",
            "timeframe": "1hour",
            "question": "Will Bitcoin be above ${price} in 1 hour?",
            "base_price": 95000,
            "volatility": 0.05
        },
        # ETH Markets
        {
            "crypto": "ETH",
            "timeframe": "5min",
            "question": "Will Ethereum price exceed ${price} in 5 minutes?",
            "base_price": 3200,
            "volatility": 0.025
        },
        {
            "crypto": "ETH",
            "timeframe": "15min",
            "question": "Will ETH gain more than 1% in 15 minutes?",
            "base_price": 3200,
            "volatility": 0.035
        },
        {
            "crypto": "ETH",
            "timeframe": "1hour",
            "question": "Will Ethereum be above ${price} in 1 hour?",
            "base_price": 3200,
            "volatility": 0.06
        },
        # SOL Markets
        {
            "crypto": "SOL",
            "timeframe": "5min",
            "question": "Will Solana price be above ${price} in 5 minutes?",
            "base_price": 180,
            "volatility": 0.03
        },
        {
            "crypto": "SOL",
            "timeframe": "1hour",
            "question": "Will SOL rise more than 2% in the next hour?",
            "base_price": 180,
            "volatility": 0.07
        },
        # DOGE Markets
        {
            "crypto": "DOGE",
            "timeframe": "5min",
            "question": "Will Dogecoin be above ${price} in 5 minutes?",
            "base_price": 0.25,
            "volatility": 0.04
        },
        {
            "crypto": "DOGE",
            "timeframe": "15min",
            "question": "Will DOGE gain more than 1.5% in 15 minutes?",
            "base_price": 0.25,
            "volatility": 0.05
        },
        # XRP Markets
        {
            "crypto": "XRP",
            "timeframe": "5min",
            "question": "Will XRP price exceed ${price} in 5 minutes?",
            "base_price": 2.50,
            "volatility": 0.03
        },
        {
            "crypto": "XRP",
            "timeframe": "1hour",
            "question": "Will Ripple be above ${price} in 1 hour?",
            "base_price": 2.50,
            "volatility": 0.06
        },
    ]
    
    @classmethod
    def generate_market(cls, template: Dict) -> Dict[str, Any]:
        """Generate a single market from template"""
        # Generate price target
        price_variation = random.uniform(-template["volatility"], template["volatility"])
        target_price = template["base_price"] * (1 + price_variation)
        
        # Format price based on magnitude
        if target_price >= 1000:
            price_str = f"{target_price:,.0f}"
        elif target_price >= 1:
            price_str = f"{target_price:.2f}"
        else:
            price_str = f"{target_price:.4f}"
        
        question = template["question"].replace("${price}", price_str)
        
        # Generate realistic probabilities
        # Add some randomness but keep it realistic
        base_prob = random.uniform(0.35, 0.65)
        sentiment_bias = random.uniform(-0.15, 0.15)
        yes_prob = max(0.05, min(0.95, base_prob + sentiment_bias))
        
        # Generate volume and liquidity
        volume_24h = random.uniform(5000, 500000)
        liquidity = random.uniform(10000, 200000)
        
        # Generate market ID
        market_id = f"0x{random.randint(0, 2**64):016x}"
        
        return {
            "id": market_id,
            "conditionId": market_id,
            "question": question,
            "description": f"Prediction market for {template['crypto']} price movement",
            "active": True,
            "closed": False,
            "volume24hr": volume_24h,
            "liquidity": liquidity,
            "volume": volume_24h * random.uniform(5, 20),
            "outcomePrices": [str(yes_prob), str(1 - yes_prob)],
            "outcomes": ["Yes", "No"],
            "endDate": (datetime.now(timezone.utc) + timedelta(
                minutes={"5min": 5, "15min": 15, "1hour": 60}.get(template["timeframe"], 60)
            )).isoformat(),
            "createdAt": (datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 48))).isoformat(),
            "tags": ["crypto", template["crypto"].lower()],
            "_demo_crypto": template["crypto"],
            "_demo_timeframe": template["timeframe"]
        }
    
    @classmethod
    def generate_all_markets(cls) -> List[Dict[str, Any]]:
        """Generate all demo markets"""
        markets = []
        for template in cls.MARKET_TEMPLATES:
            market = cls.generate_market(template)
            markets.append(market)
        return markets
    
    @classmethod
    def generate_trades(cls, market_id: str, count: int = 50) -> List[Dict[str, Any]]:
        """Generate demo trades for a market"""
        trades = []
        base_time = datetime.now(timezone.utc)
        
        for i in range(count):
            trade_time = base_time - timedelta(minutes=random.randint(0, 120))
            side = random.choice(["BUY", "SELL"])
            price = random.uniform(0.3, 0.7)
            size = random.uniform(10, 1000)
            
            trades.append({
                "id": f"trade_{i}_{market_id[:8]}",
                "market": market_id,
                "side": side,
                "price": price,
                "size": size,
                "timestamp": trade_time.isoformat()
            })
        
        return sorted(trades, key=lambda x: x["timestamp"], reverse=True)
    
    @classmethod
    def get_market_summary(cls, markets: List[Dict]) -> Dict[str, Any]:
        """Generate market summary from demo markets"""
        total_volume = sum(float(m.get("volume24hr", 0)) for m in markets)
        total_liquidity = sum(float(m.get("liquidity", 0)) for m in markets)
        
        crypto_counts = {}
        for m in markets:
            crypto = m.get("_demo_crypto", "Unknown")
            crypto_counts[crypto] = crypto_counts.get(crypto, 0) + 1
        
        return {
            "total_markets": len(markets),
            "total_volume_24h": total_volume,
            "total_liquidity": total_liquidity,
            "markets_by_crypto": crypto_counts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_demo_mode": True
        }


def get_demo_crypto_markets() -> List[Dict[str, Any]]:
    """Get demo crypto markets"""
    return DemoDataGenerator.generate_all_markets()


def get_demo_trades(market_id: str, count: int = 50) -> List[Dict[str, Any]]:
    """Get demo trades for a market"""
    return DemoDataGenerator.generate_trades(market_id, count)


def get_demo_market_summary(markets: List[Dict]) -> Dict[str, Any]:
    """Get demo market summary"""
    return DemoDataGenerator.get_market_summary(markets)