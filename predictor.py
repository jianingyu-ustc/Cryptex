"""
Crypto Price Prediction System based on Polymarket Data
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from api_client import PolymarketClient, MarketAnalyzer


class PredictionDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class TimeFrame(Enum):
    FIVE_MIN = "5min"
    FIFTEEN_MIN = "15min"
    ONE_HOUR = "1hour"


@dataclass
class CryptoPrediction:
    """Data class for crypto price prediction"""
    crypto: str
    time_frame: TimeFrame
    direction: PredictionDirection
    probability: float
    confidence: float
    market_question: str
    market_id: str
    volume_24h: float
    liquidity: float
    sentiment: str
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "crypto": self.crypto,
            "time_frame": self.time_frame.value,
            "direction": self.direction.value,
            "probability": round(self.probability * 100, 2),
            "confidence": round(self.confidence * 100, 2),
            "market_question": self.market_question,
            "market_id": self.market_id,
            "volume_24h": self.volume_24h,
            "liquidity": self.liquidity,
            "sentiment": self.sentiment,
            "timestamp": self.timestamp.isoformat()
        }


class CryptoPredictor:
    """Main crypto prediction engine using Polymarket data"""
    
    # Map common crypto names to search terms
    CRYPTO_MAPPING = {
        "BTC": ["bitcoin", "btc"],
        "ETH": ["ethereum", "eth"],
        "SOL": ["solana", "sol"],
        "DOGE": ["dogecoin", "doge"],
        "XRP": ["xrp", "ripple"],
        "BNB": ["bnb", "binance"],
        "ADA": ["cardano", "ada"],
        "AVAX": ["avalanche", "avax"],
        "MATIC": ["polygon", "matic"],
        "DOT": ["polkadot", "dot"]
    }
    
    # Keywords for time frames
    TIME_FRAME_KEYWORDS = {
        TimeFrame.FIVE_MIN: ["5 min", "5min", "five min", "5-min", "5 minute"],
        TimeFrame.FIFTEEN_MIN: ["15 min", "15min", "fifteen min", "15-min", "15 minute"],
        TimeFrame.ONE_HOUR: ["1 hour", "1hour", "one hour", "hourly", "60 min", "up or down"]
    }
    
    # Keywords for price direction
    UP_KEYWORDS = ["up", "above", "higher", "rise", "increase", "bull", "gain", "up or down"]
    DOWN_KEYWORDS = ["down", "below", "lower", "fall", "decrease", "bear", "drop"]
    
    # Keywords that indicate this is a PRICE prediction market (not tweets, etc.)
    PRICE_MARKET_KEYWORDS = ["price", "up or down", "above", "below", "rise", "fall", "reach", "hit"]
    
    # Keywords to EXCLUDE (these are not price prediction markets)
    EXCLUDE_KEYWORDS = ["tweet", "post", "musk", "elon", "election", "vote", "president", "congress"]
    
    def __init__(self):
        self.client = PolymarketClient()
        self.analyzer = MarketAnalyzer(self.client)
        self._market_cache: Dict[str, List[Dict]] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_duration = 60  # Cache for 60 seconds
    
    async def _get_all_crypto_markets(self, force_refresh: bool = False) -> List[Dict]:
        """Get all crypto markets with caching"""
        now = datetime.now(timezone.utc)
        
        if (not force_refresh and 
            self._cache_timestamp and 
            (now - self._cache_timestamp).seconds < self._cache_duration and
            self._market_cache):
            return self._market_cache.get("all", [])
        
        markets = await self.client.get_crypto_markets()
        self._market_cache["all"] = markets
        self._cache_timestamp = now
        return markets
    
    def _identify_crypto(self, text: str) -> Optional[str]:
        """Identify which cryptocurrency a market is about"""
        text_lower = text.lower()
        
        for crypto, keywords in self.CRYPTO_MAPPING.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return crypto
        return None
    
    def _identify_time_frame(self, text: str, market: Optional[Dict] = None) -> Optional[TimeFrame]:
        """Identify the time frame of a market"""
        # Check for demo data timeframe marker
        if market and market.get("_demo_timeframe"):
            tf_str = market.get("_demo_timeframe")
            tf_map = {"5min": TimeFrame.FIVE_MIN, "15min": TimeFrame.FIFTEEN_MIN, "1hour": TimeFrame.ONE_HOUR}
            return tf_map.get(tf_str)
        
        text_lower = text.lower()
        
        for time_frame, keywords in self.TIME_FRAME_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return time_frame
        return None
    
    def _identify_direction(self, text: str) -> PredictionDirection:
        """Identify if market is about price going up or down"""
        text_lower = text.lower()
        
        # Check for up keywords
        up_score = sum(1 for kw in self.UP_KEYWORDS if kw in text_lower)
        down_score = sum(1 for kw in self.DOWN_KEYWORDS if kw in text_lower)
        
        if up_score > down_score:
            return PredictionDirection.UP
        elif down_score > up_score:
            return PredictionDirection.DOWN
        return PredictionDirection.NEUTRAL
    
    def _calculate_confidence(self, probability: float, health_score: int, trade_count: int) -> float:
        """Calculate confidence score for a prediction"""
        # Base confidence from probability certainty
        prob_certainty = abs(probability - 0.5) * 2  # 0 at 50%, 1 at 0% or 100%
        
        # Health factor (0-1)
        health_factor = health_score / 100
        
        # Trade activity factor (0-1)
        trade_factor = min(trade_count / 100, 1.0)
        
        # Combined confidence
        confidence = (prob_certainty * 0.4) + (health_factor * 0.4) + (trade_factor * 0.2)
        return min(confidence, 1.0)
    
    def _is_price_prediction_market(self, text: str, market: Optional[Dict] = None) -> bool:
        """Check if this is actually a price prediction market"""
        # Demo data is always valid
        if market and market.get("_demo_crypto"):
            return True
        
        text_lower = text.lower()
        
        # First check if it contains any exclude keywords
        for exclude_kw in self.EXCLUDE_KEYWORDS:
            if exclude_kw in text_lower:
                return False
        
        # Then check if it contains price-related keywords
        for price_kw in self.PRICE_MARKET_KEYWORDS:
            if price_kw in text_lower:
                return True
        
        return False
    
    async def get_predictions_for_crypto(
        self, 
        crypto: str,
        time_frame: Optional[TimeFrame] = None
    ) -> List[CryptoPrediction]:
        """
        Get predictions for a specific cryptocurrency
        
        Args:
            crypto: Cryptocurrency symbol (BTC, ETH, etc.)
            time_frame: Optional time frame filter
        
        Returns:
            List of CryptoPrediction objects
        """
        markets = await self._get_all_crypto_markets()
        predictions = []
        
        for market in markets:
            question = market.get("question", "")
            description = market.get("description", "")
            combined_text = f"{question} {description}"
            
            # Check if market is about the requested crypto
            market_crypto = self._identify_crypto(combined_text)
            if market_crypto != crypto.upper():
                continue
            
            # Filter out non-price prediction markets (like tweets, elections, etc.)
            if not self._is_price_prediction_market(combined_text, market):
                continue
            
            # Check time frame
            market_time_frame = self._identify_time_frame(combined_text, market)
            if time_frame and market_time_frame != time_frame:
                continue
            
            # Skip if no identifiable time frame
            if not market_time_frame:
                continue
            
            # Analyze the market
            analysis = await self.analyzer.analyze_market(market)
            
            # Determine direction and probability
            direction = self._identify_direction(question)
            probabilities = analysis["probabilities"]
            
            # Get the probability for "yes" outcome
            yes_prob = probabilities.get("yes", 0.5)
            
            # Calculate confidence
            confidence = self._calculate_confidence(
                yes_prob,
                analysis["health"]["health_score"],
                analysis["recent_trades"]
            )
            
            prediction = CryptoPrediction(
                crypto=crypto.upper(),
                time_frame=market_time_frame,
                direction=direction,
                probability=yes_prob,
                confidence=confidence,
                market_question=question,
                market_id=analysis["market_id"] or "",
                volume_24h=analysis["health"]["volume_24h"],
                liquidity=analysis["health"]["liquidity"],
                sentiment=analysis["sentiment"],
                timestamp=datetime.now(timezone.utc)
            )
            predictions.append(prediction)
        
        return predictions
    
    async def get_all_short_term_predictions(self) -> Dict[str, List[CryptoPrediction]]:
        """
        Get all short-term (5min, 15min, 1hour) predictions for all cryptos
        
        Returns:
            Dict mapping crypto symbols to lists of predictions
        """
        all_predictions: Dict[str, List[CryptoPrediction]] = {}
        
        for crypto in self.CRYPTO_MAPPING.keys():
            predictions = await self.get_predictions_for_crypto(crypto)
            if predictions:
                all_predictions[crypto] = predictions
        
        return all_predictions
    
    async def get_best_opportunities(
        self, 
        min_confidence: float = 0.5,
        min_probability_deviation: float = 0.15
    ) -> List[CryptoPrediction]:
        """
        Find the best trading opportunities based on market probabilities
        
        Args:
            min_confidence: Minimum confidence threshold (0-1)
            min_probability_deviation: Minimum deviation from 50% (0-0.5)
        
        Returns:
            List of high-confidence predictions sorted by opportunity score
        """
        all_predictions = await self.get_all_short_term_predictions()
        opportunities = []
        
        for crypto, predictions in all_predictions.items():
            for pred in predictions:
                # Calculate probability deviation from 50%
                prob_deviation = abs(pred.probability - 0.5)
                
                if pred.confidence >= min_confidence and prob_deviation >= min_probability_deviation:
                    opportunities.append(pred)
        
        # Sort by opportunity score (confidence * probability deviation)
        opportunities.sort(
            key=lambda p: p.confidence * abs(p.probability - 0.5),
            reverse=True
        )
        
        return opportunities
    
    async def get_market_summary(self) -> Dict[str, Any]:
        """Get overall crypto market summary from Polymarket"""
        markets = await self._get_all_crypto_markets()
        
        total_volume = sum(float(m.get("volume24hr", 0) or 0) for m in markets)
        total_liquidity = sum(float(m.get("liquidity", 0) or 0) for m in markets)
        
        # Count by crypto
        crypto_counts: Dict[str, int] = {}
        for market in markets:
            question = market.get("question", "") + " " + market.get("description", "")
            for crypto in self.CRYPTO_MAPPING.keys():
                if self._identify_crypto(question) == crypto:
                    crypto_counts[crypto] = crypto_counts.get(crypto, 0) + 1
                    break
        
        return {
            "total_markets": len(markets),
            "total_volume_24h": total_volume,
            "total_liquidity": total_liquidity,
            "markets_by_crypto": crypto_counts,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


class PredictionAggregator:
    """Aggregate and analyze predictions"""
    
    @staticmethod
    def aggregate_by_crypto(predictions: List[CryptoPrediction]) -> Dict[str, Dict]:
        """
        Aggregate predictions by cryptocurrency
        
        Returns summary statistics for each crypto
        """
        aggregated: Dict[str, Dict] = {}
        
        for pred in predictions:
            crypto = pred.crypto
            if crypto not in aggregated:
                aggregated[crypto] = {
                    "predictions": [],
                    "avg_up_prob": 0,
                    "avg_down_prob": 0,
                    "total_volume": 0,
                    "overall_sentiment": "Neutral"
                }
            
            aggregated[crypto]["predictions"].append(pred)
            aggregated[crypto]["total_volume"] += pred.volume_24h
        
        # Calculate averages
        for crypto, data in aggregated.items():
            predictions_list = data["predictions"]
            up_probs = [p.probability for p in predictions_list if p.direction == PredictionDirection.UP]
            down_probs = [p.probability for p in predictions_list if p.direction == PredictionDirection.DOWN]
            
            data["avg_up_prob"] = sum(up_probs) / len(up_probs) if up_probs else 0.5
            data["avg_down_prob"] = sum(down_probs) / len(down_probs) if down_probs else 0.5
            data["prediction_count"] = len(predictions_list)
            
            # Determine overall sentiment
            if data["avg_up_prob"] > 0.6:
                data["overall_sentiment"] = "Bullish"
            elif data["avg_down_prob"] > 0.6:
                data["overall_sentiment"] = "Bearish"
            else:
                data["overall_sentiment"] = "Neutral"
        
        return aggregated
    
    @staticmethod
    def aggregate_by_timeframe(predictions: List[CryptoPrediction]) -> Dict[str, List[CryptoPrediction]]:
        """Group predictions by time frame"""
        grouped: Dict[str, List[CryptoPrediction]] = {}
        
        for pred in predictions:
            tf = pred.time_frame.value
            if tf not in grouped:
                grouped[tf] = []
            grouped[tf].append(pred)
        
        return grouped
    
    @staticmethod
    def get_consensus(predictions: List[CryptoPrediction]) -> Dict[str, Any]:
        """
        Calculate market consensus from multiple predictions
        
        Returns overall direction and confidence
        """
        if not predictions:
            return {
                "direction": PredictionDirection.NEUTRAL.value,
                "confidence": 0,
                "agreement": 0
            }
        
        up_count = sum(1 for p in predictions if p.direction == PredictionDirection.UP and p.probability > 0.5)
        down_count = sum(1 for p in predictions if p.direction == PredictionDirection.DOWN and p.probability > 0.5)
        total = len(predictions)
        
        if up_count > down_count:
            direction = PredictionDirection.UP
            agreement = up_count / total
        elif down_count > up_count:
            direction = PredictionDirection.DOWN
            agreement = down_count / total
        else:
            direction = PredictionDirection.NEUTRAL
            agreement = 0.5
        
        avg_confidence = sum(p.confidence for p in predictions) / total
        
        return {
            "direction": direction.value,
            "confidence": round(avg_confidence, 3),
            "agreement": round(agreement, 3),
            "sample_size": total
        }