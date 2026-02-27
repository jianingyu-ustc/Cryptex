"""
Backtesting Module for Crypto Predictions
Analyzes historical prediction accuracy based on Polymarket settled markets
"""

import asyncio
import subprocess
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import time

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn


class BacktestResult(Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    UNKNOWN = "UNKNOWN"


@dataclass
class PredictionResult:
    """Single prediction result for backtesting"""
    market_id: str
    question: str
    crypto: str
    time_frame: str
    predicted_direction: str  # UP or DOWN
    predicted_probability: float
    actual_outcome: str  # UP or DOWN or UNKNOWN
    result: BacktestResult
    start_time: datetime
    end_time: datetime
    volume: float = 0.0
    
    @property
    def was_correct(self) -> bool:
        return self.result == BacktestResult.WIN


@dataclass
class BacktestStats:
    """Aggregated backtest statistics"""
    total_predictions: int = 0
    correct_predictions: int = 0
    incorrect_predictions: int = 0
    unknown_predictions: int = 0
    
    # By confidence level
    high_conf_total: int = 0
    high_conf_correct: int = 0
    
    # By probability deviation
    strong_signal_total: int = 0
    strong_signal_correct: int = 0
    
    # ROI calculation (assuming $100 per trade)
    total_invested: float = 0.0
    total_return: float = 0.0
    
    @property
    def accuracy(self) -> float:
        if self.total_predictions - self.unknown_predictions == 0:
            return 0.0
        return self.correct_predictions / (self.total_predictions - self.unknown_predictions)
    
    @property
    def high_conf_accuracy(self) -> float:
        if self.high_conf_total == 0:
            return 0.0
        return self.high_conf_correct / self.high_conf_total
    
    @property
    def strong_signal_accuracy(self) -> float:
        if self.strong_signal_total == 0:
            return 0.0
        return self.strong_signal_correct / self.strong_signal_total
    
    @property
    def roi(self) -> float:
        if self.total_invested == 0:
            return 0.0
        return (self.total_return - self.total_invested) / self.total_invested


class Backtester:
    """Backtest crypto predictions against historical data"""
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    def __init__(self):
        self.console = Console()
        self.results: List[PredictionResult] = []
        self.stats = BacktestStats()
    
    def _curl_get(self, url: str) -> Optional[Dict]:
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
            pass
        return None
    
    async def get_historical_events(self, crypto: str, hours_back: int = 24) -> List[Dict]:
        """Get historical settled events for a crypto"""
        now_ts = int(time.time())
        events = []
        
        # Calculate start timestamp (hours back)
        start_ts = now_ts - (hours_back * 3600)
        
        # Round to 5-minute slots
        start_slot = (start_ts // 300) * 300
        end_slot = (now_ts // 300) * 300
        
        self.console.print(f"[cyan]Fetching {crypto.upper()} events from last {hours_back} hours...[/cyan]")
        
        # Sample every 5 minutes
        slot = start_slot
        checked = 0
        found = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            task = progress.add_task(f"Scanning {crypto.upper()} markets...", total=None)
            
            while slot < end_slot:
                slug = f"{crypto.lower()}-updown-5m-{slot}"
                url = f"{self.GAMMA_API}/events?slug={slug}"
                
                data = self._curl_get(url)
                if data and len(data) > 0:
                    event = data[0]
                    # Only include closed/settled events
                    if event.get("closed") == True:
                        events.append(event)
                        found += 1
                
                checked += 1
                progress.update(task, description=f"Checked {checked} slots, found {found} settled events")
                
                # Move to next slot (every 5 minutes)
                slot += 300
                
                # Rate limiting
                await asyncio.sleep(0.05)
        
        return events
    
    def parse_outcome(self, event: Dict) -> str:
        """Parse the actual outcome from a settled event"""
        markets = event.get("markets", [])
        if not markets:
            return "UNKNOWN"
        
        market = markets[0]
        
        # Check outcome prices - in settled markets, one should be 1.0 and other 0.0
        outcome_prices = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except:
                return "UNKNOWN"
        
        outcomes = market.get("outcomes", "[]")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except:
                return "UNKNOWN"
        
        if len(outcome_prices) >= 2 and len(outcomes) >= 2:
            # Find which outcome won (price = 1.0)
            for i, price in enumerate(outcome_prices):
                if float(price) >= 0.99:  # Winner
                    return outcomes[i].upper()
        
        return "UNKNOWN"
    
    def simulate_prediction(self, event: Dict) -> Optional[PredictionResult]:
        """Simulate what our predictor would have predicted for this event"""
        markets = event.get("markets", [])
        if not markets:
            return None
        
        market = markets[0]
        question = market.get("question", "")
        
        # Identify crypto from question
        crypto = "UNKNOWN"
        if "bitcoin" in question.lower() or "btc" in question.lower():
            crypto = "BTC"
        elif "ethereum" in question.lower() or "eth" in question.lower():
            crypto = "ETH"
        elif "solana" in question.lower() or "sol" in question.lower():
            crypto = "SOL"
        
        # Get historical probability (we'd use the opening probability)
        # For now, simulate with the current/final state
        outcome_prices = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except:
                outcome_prices = []
        
        outcomes = market.get("outcomes", "[]")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except:
                outcomes = []
        
        # Find UP probability from historical data
        up_prob = 0.5
        if len(outcome_prices) >= 2 and len(outcomes) >= 2:
            for i, outcome in enumerate(outcomes):
                if outcome.upper() == "UP":
                    # For settled markets, use lastTradePrice instead
                    last_price = market.get("lastTradePrice", 0.5)
                    if outcomes[0].upper() == "UP":
                        up_prob = float(last_price) if last_price else 0.5
                    else:
                        up_prob = 1 - float(last_price) if last_price else 0.5
                    break
        
        # Our prediction: UP if probability > 50%
        predicted_direction = "UP" if up_prob >= 0.5 else "DOWN"
        
        # Get actual outcome
        actual_outcome = self.parse_outcome(event)
        
        # Determine result
        if actual_outcome == "UNKNOWN":
            result = BacktestResult.UNKNOWN
        elif predicted_direction == actual_outcome:
            result = BacktestResult.WIN
        else:
            result = BacktestResult.LOSS
        
        # Parse times
        end_date = market.get("endDate", "")
        start_date = market.get("startDate", "")
        
        try:
            from dateutil import parser
            end_time = parser.parse(end_date) if end_date else datetime.now(timezone.utc)
            start_time = parser.parse(start_date) if start_date else end_time - timedelta(minutes=5)
        except:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=5)
        
        return PredictionResult(
            market_id=market.get("id", ""),
            question=question,
            crypto=crypto,
            time_frame="5min",
            predicted_direction=predicted_direction,
            predicted_probability=up_prob,
            actual_outcome=actual_outcome,
            result=result,
            start_time=start_time,
            end_time=end_time,
            volume=float(market.get("volume", 0) or 0)
        )
    
    def calculate_stats(self, results: List[PredictionResult]) -> BacktestStats:
        """Calculate aggregated statistics"""
        stats = BacktestStats()
        
        for r in results:
            stats.total_predictions += 1
            
            if r.result == BacktestResult.WIN:
                stats.correct_predictions += 1
                # Simulate profit (win pays 2x minus house edge)
                stats.total_invested += 100
                stats.total_return += 195  # ~95% payout
            elif r.result == BacktestResult.LOSS:
                stats.incorrect_predictions += 1
                stats.total_invested += 100
                stats.total_return += 0  # Lost the bet
            else:
                stats.unknown_predictions += 1
            
            # Track high confidence predictions (probability > 55% or < 45%)
            if abs(r.predicted_probability - 0.5) > 0.05:
                stats.strong_signal_total += 1
                if r.result == BacktestResult.WIN:
                    stats.strong_signal_correct += 1
        
        return stats
    
    async def run_backtest(
        self, 
        cryptos: List[str] = ["btc", "eth", "sol"],
        hours_back: int = 24
    ) -> BacktestStats:
        """Run full backtest for specified cryptos"""
        all_events = []
        
        for crypto in cryptos:
            events = await self.get_historical_events(crypto, hours_back)
            all_events.extend(events)
            self.console.print(f"[green]Found {len(events)} settled {crypto.upper()} events[/green]")
        
        self.console.print(f"\n[cyan]Processing {len(all_events)} total events...[/cyan]")
        
        self.results = []
        for event in all_events:
            result = self.simulate_prediction(event)
            if result:
                self.results.append(result)
        
        self.stats = self.calculate_stats(self.results)
        return self.stats
    
    def display_results(self):
        """Display backtest results in a nice format"""
        self.console.print()
        
        # Summary Panel
        summary = f"""
[bold cyan]?? Backtest Summary[/bold cyan]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Total Predictions: {self.stats.total_predictions}
├─ ✅ Correct: {self.stats.correct_predictions}
├─ ❌ Incorrect: {self.stats.incorrect_predictions}
└─ ❓ Unknown: {self.stats.unknown_predictions}

[bold]Accuracy: {self.stats.accuracy * 100:.1f}%[/bold]
Strong Signal Accuracy: {self.stats.strong_signal_accuracy * 100:.1f}% ({self.stats.strong_signal_total} trades)

[bold yellow]💰 Simulated ROI[/bold yellow]
Invested: ${self.stats.total_invested:,.0f}
Returned: ${self.stats.total_return:,.0f}
[bold {'green' if self.stats.roi >= 0 else 'red'}]ROI: {self.stats.roi * 100:+.1f}%[/bold {'green' if self.stats.roi >= 0 else 'red'}]
"""
        self.console.print(Panel(summary, title="📈 Backtest Results", border_style="cyan"))
        
        # Recent predictions table
        if self.results:
            table = Table(title="Recent Predictions Sample", show_header=True)
            table.add_column("Time (ET)", style="dim")
            table.add_column("Crypto")
            table.add_column("Predicted")
            table.add_column("Actual")
            table.add_column("Result")
            
            for r in self.results[-10:]:  # Last 10
                result_str = "✅" if r.result == BacktestResult.WIN else "❌" if r.result == BacktestResult.LOSS else "❓"
                pred_color = "green" if r.predicted_direction == "UP" else "red"
                actual_color = "green" if r.actual_outcome == "UP" else "red" if r.actual_outcome == "DOWN" else "yellow"
                
                table.add_row(
                    r.end_time.strftime("%H:%M"),
                    r.crypto,
                    f"[{pred_color}]{r.predicted_direction}[/{pred_color}] ({r.predicted_probability*100:.0f}%)",
                    f"[{actual_color}]{r.actual_outcome}[/{actual_color}]",
                    result_str
                )
            
            self.console.print(table)
        
        # Strategy recommendations
        if self.stats.accuracy > 0.55:
            self.console.print("\n[bold green]✅ Strategy shows positive edge! Consider paper trading.[/bold green]")
        elif self.stats.accuracy > 0.50:
            self.console.print("\n[bold yellow]⚠️ Strategy is near breakeven. May need refinement.[/bold yellow]")
        else:
            self.console.print("\n[bold red]❌ Strategy underperforms. Review prediction logic.[/bold red]")
    
    def generate_demo_results(self, num_predictions: int = 50) -> List[PredictionResult]:
        """Generate simulated backtest results for demo purposes"""
        import random
        
        results = []
        base_time = datetime.now(timezone.utc) - timedelta(hours=6)
        
        cryptos = ["BTC", "ETH", "SOL"]
        
        for i in range(num_predictions):
            crypto = random.choice(cryptos)
            
            # Simulate market prediction (slightly better than random)
            up_prob = random.uniform(0.35, 0.65)
            predicted_direction = "UP" if up_prob >= 0.5 else "DOWN"
            
            # Simulate actual outcome with ~52% edge for our predictions
            if random.random() < 0.52:
                actual_outcome = predicted_direction
                result = BacktestResult.WIN
            else:
                actual_outcome = "DOWN" if predicted_direction == "UP" else "UP"
                result = BacktestResult.LOSS
            
            end_time = base_time + timedelta(minutes=i * 5)
            start_time = end_time - timedelta(minutes=5)
            
            results.append(PredictionResult(
                market_id=f"demo_{i}",
                question=f"{crypto} Up or Down - {end_time.strftime('%H:%M')}",
                crypto=crypto,
                time_frame="5min",
                predicted_direction=predicted_direction,
                predicted_probability=up_prob,
                actual_outcome=actual_outcome,
                result=result,
                start_time=start_time,
                end_time=end_time,
                volume=random.uniform(1000, 50000)
            ))
        
        return results
    
    async def run_demo_backtest(self, num_predictions: int = 50) -> BacktestStats:
        """Run demo backtest with simulated data"""
        self.console.print("[yellow]⚠️ Running in DEMO mode (API unavailable)[/yellow]")
        self.console.print("[dim]Using simulated historical data for demonstration[/dim]\n")
        
        self.results = self.generate_demo_results(num_predictions)
        self.stats = self.calculate_stats(self.results)
        return self.stats


async def main():
    """Main function for running backtest"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Backtest crypto predictions")
    parser.add_argument("--hours", type=int, default=6, help="Hours of history to backtest")
    parser.add_argument("--crypto", type=str, default="btc,eth,sol", help="Cryptos to test (comma-separated)")
    parser.add_argument("--demo", action="store_true", help="Run with simulated demo data")
    args = parser.parse_args()
    
    cryptos = [c.strip() for c in args.crypto.split(",")]
    
    console = Console()
    console.print(Panel.fit(
        "[bold cyan]🔄 CRYPTO PREDICTION BACKTESTER[/bold cyan]\n"
        "Testing prediction accuracy against historical data",
        border_style="cyan"
    ))
    
    backtester = Backtester()
    
    if args.demo:
        await backtester.run_demo_backtest(num_predictions=args.hours * 12)
    else:
        await backtester.run_backtest(cryptos=cryptos, hours_back=args.hours)
        
        # If no results, fallback to demo mode
        if not backtester.results:
            console.print("\n[yellow]No historical data found. Running demo backtest instead...[/yellow]\n")
            await backtester.run_demo_backtest(num_predictions=args.hours * 12)
    
    backtester.display_results()


if __name__ == "__main__":
    asyncio.run(main())