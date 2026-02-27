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
        TimeFrame.FIVE_MIN: ["5 min", "5min", "five min", "5-min", "5 minute", "5m"],
        TimeFrame.FIFTEEN_MIN: ["15 min", "15min", "fifteen min", "15-min", "15 minute", "15m"],
        TimeFrame.ONE_HOUR: ["1 hour", "1hour", "one hour", "hourly", "60 min"]
    }
    
    # Pattern for detecting 5-minute time range in question (e.g., "7:10AM-7:15AM")
    FIVE_MIN_TIME_RANGE_PATTERN = r'\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M'
    
    # Keywords for price direction
    UP_KEYWORDS = ["up", "above", "higher", "rise", "increase", "bull", "gain", "up or down"]
    DOWN_KEYWORDS = ["down", "below", "lower", "fall", "decrease", "bear", "drop"]
    
    # Keywords that indicate this is a PRICE prediction market (not tweets, etc.)
    PRICE_MARKET_KEYWORDS = ["price", "up or down", "above", "below", "rise", "fall", "reach", "hit"]
    
    # Keywords to EXCLUDE (these are not price prediction markets)
    EXCLUDE_KEYWORDS = ["tweet", "post", "musk", "elon", "election", "vote", "president", "congress"]
    
    def __init__(self, include_settled: bool = False):
        self.client = PolymarketClient()
        self.analyzer = MarketAnalyzer(self.client)
        self._market_cache: Dict[str, List[Dict]] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_duration = 60  # Cache for 60 seconds
        self.include_settled = include_settled
    
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
        import re
        
        # Check for demo data timeframe marker
        if market and market.get("_demo_timeframe"):
            tf_str = market.get("_demo_timeframe")
            tf_map = {"5min": TimeFrame.FIVE_MIN, "15min": TimeFrame.FIFTEEN_MIN, "1hour": TimeFrame.ONE_HOUR}
            return tf_map.get(tf_str)
        
        text_lower = text.lower()
        
        # Check for 5-minute time range pattern (e.g., "7:10AM-7:15AM")
        if re.search(self.FIVE_MIN_TIME_RANGE_PATTERN, text, re.IGNORECASE):
            return TimeFrame.FIVE_MIN
        
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
    
    def _calculate_time_remaining(self, end_date_str: str) -> float:
        """Calculate time remaining in minutes until market ends"""
        if not end_date_str:
            return 5.0  # Default to 5 minutes
        
        try:
            from dateutil import parser
            end_date = parser.parse(end_date_str)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            remaining = (end_date - now).total_seconds() / 60
            return max(0, remaining)
        except:
            return 5.0
    
    def _apply_advanced_strategy(
        self, 
        probability: float, 
        base_confidence: float,
        time_remaining: float,
        liquidity: float,
        price_momentum: Optional[Dict] = None,
        technical_indicators: Optional[Dict] = None
    ) -> tuple:
        """
        Apply advanced multi-factor strategy to improve prediction accuracy.
        
        Args:
            probability: Market probability for UP
            base_confidence: Base confidence score
            time_remaining: Minutes until market ends
            liquidity: Market liquidity in USD
            price_momentum: Real-time price momentum data
            technical_indicators: Technical analysis indicators
        
        Returns:
            tuple: (adjusted_direction, adjusted_confidence, strategy_notes)
        """
        notes = []
        confidence_multiplier = 1.0
        
        # ============ Factor 1: Time-based Entry Filter ============
        if time_remaining > 3.0:
            confidence_multiplier *= 1.1
            notes.append("early_signal")
        elif time_remaining < 1.0:
            confidence_multiplier *= 0.7
            notes.append("late_entry_penalty")
        
        # ============ Factor 2: Signal Strength Filter ============
        prob_deviation = abs(probability - 0.5)
        if prob_deviation < 0.05:
            confidence_multiplier *= 0.5
            notes.append("weak_signal")
        elif prob_deviation > 0.15:
            confidence_multiplier *= 1.2
            notes.append("strong_signal")
        
        # ============ Factor 3: Extreme Probability Analysis ============
        contrarian_signal = False
        if probability > 0.70:
            confidence_multiplier *= 0.85
            notes.append("extreme_bullish_caution")
            contrarian_signal = True
        elif probability < 0.30:
            confidence_multiplier *= 0.85
            notes.append("extreme_bearish_caution")
            contrarian_signal = True
        
        # ============ Factor 4: Liquidity Filter ============
        if liquidity < 10000:
            confidence_multiplier *= 0.6
            notes.append("low_liquidity_risk")
        elif liquidity > 100000:
            confidence_multiplier *= 1.1
            notes.append("high_liquidity_boost")
        
        # ============ Factor 5: Real-time Price Momentum ============
        momentum_direction = None
        if price_momentum:
            momentum_5m = price_momentum.get("momentum_5m", 0)
            volatility = price_momentum.get("volatility_5m", 0)
            
            # High volatility = lower confidence
            if volatility > 0.5:
                confidence_multiplier *= 0.8
                notes.append("high_volatility")
            
            # Momentum alignment check
            if momentum_5m > 0.1:
                momentum_direction = "UP"
                notes.append("momentum_up")
            elif momentum_5m < -0.1:
                momentum_direction = "DOWN"
                notes.append("momentum_down")
            
            # Momentum confirms market probability = boost
            if momentum_direction:
                market_direction = "UP" if probability > 0.5 else "DOWN"
                if momentum_direction == market_direction:
                    confidence_multiplier *= 1.15
                    notes.append("momentum_confirms")
                else:
                    # Divergence - market says UP but price going DOWN
                    confidence_multiplier *= 0.75
                    notes.append("momentum_divergence")
        
        # ============ Factor 6: Technical Indicators ============
        if technical_indicators:
            rsi = technical_indicators.get("rsi", 50)
            trend = technical_indicators.get("trend", "NEUTRAL")
            
            # RSI overbought/oversold
            if rsi > 70:
                notes.append("rsi_overbought")
                # If market expects UP but RSI overbought, be cautious
                if probability > 0.5:
                    confidence_multiplier *= 0.85
            elif rsi < 30:
                notes.append("rsi_oversold")
                # If market expects DOWN but RSI oversold, be cautious
                if probability < 0.5:
                    confidence_multiplier *= 0.85
            
            # Technical trend alignment
            if trend == "BULLISH" and probability > 0.5:
                confidence_multiplier *= 1.1
                notes.append("tech_confirms_up")
            elif trend == "BEARISH" and probability < 0.5:
                confidence_multiplier *= 1.1
                notes.append("tech_confirms_down")
            elif trend != "NEUTRAL":
                # Technical disagrees with market
                confidence_multiplier *= 0.9
                notes.append("tech_divergence")
        
        # ============ Final Direction Determination ============
        # Use contrarian logic only when signal is extreme AND late in market
        if contrarian_signal and time_remaining < 2.0 and prob_deviation > 0.20:
            if probability > 0.70:
                adjusted_direction = PredictionDirection.DOWN
                notes.append("contrarian_down")
            else:
                adjusted_direction = PredictionDirection.UP
                notes.append("contrarian_up")
        else:
            # Standard direction based on probability
            if probability > 0.5:
                adjusted_direction = PredictionDirection.UP
            elif probability < 0.5:
                adjusted_direction = PredictionDirection.DOWN
            else:
                adjusted_direction = PredictionDirection.NEUTRAL
        
        # Apply confidence multiplier with bounds
        adjusted_confidence = base_confidence * confidence_multiplier
        adjusted_confidence = max(0.05, min(0.95, adjusted_confidence))
        
        return adjusted_direction, adjusted_confidence, notes
    
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
        now = datetime.now(timezone.utc)
        
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
            
            # Filter out closed/settled markets (unless include_settled is True)
            if not self.include_settled:
                if market.get("closed", False):
                    continue
                
                # Filter out markets that have already ended
                end_date_str = market.get("endDate")
                if end_date_str:
                    try:
                        from dateutil import parser
                        end_date = parser.parse(end_date_str)
                        if end_date.tzinfo is None:
                            end_date = end_date.replace(tzinfo=timezone.utc)
                        if end_date < now:
                            continue
                    except:
                        pass
            
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
            probabilities = analysis["probabilities"]
            
            # Get the probability for "yes" outcome (which represents UP)
            yes_prob = probabilities.get("yes", 0.5)
            
            # Calculate base confidence
            base_confidence = self._calculate_confidence(
                yes_prob,
                analysis["health"]["health_score"],
                analysis["recent_trades"]
            )
            
            # Calculate time remaining for this market
            end_date_str = market.get("endDate", "")
            time_remaining = self._calculate_time_remaining(end_date_str)
            
            # Apply advanced multi-factor strategy
            direction, adjusted_confidence, strategy_notes = self._apply_advanced_strategy(
                probability=yes_prob,
                base_confidence=base_confidence,
                time_remaining=time_remaining,
                liquidity=analysis["health"]["liquidity"]
            )
            
            prediction = CryptoPrediction(
                crypto=crypto.upper(),
                time_frame=market_time_frame,
                direction=direction,
                probability=yes_prob,
                confidence=adjusted_confidence,
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