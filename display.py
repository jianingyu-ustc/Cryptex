"""
Display module for Crypto Predictor - Rich terminal output
"""

from typing import List, Dict, Any
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich import box

from predictor import CryptoPrediction, PredictionDirection, TimeFrame


console = Console()


class PredictionDisplay:
    """Display predictions in rich terminal format"""
    
    # Color schemes
    COLORS = {
        "up": "green",
        "down": "red",
        "neutral": "yellow",
        "header": "cyan",
        "highlight": "magenta",
        "muted": "dim white"
    }
    
    @staticmethod
    def direction_color(direction: PredictionDirection) -> str:
        """Get color for direction"""
        if direction == PredictionDirection.UP:
            return PredictionDisplay.COLORS["up"]
        elif direction == PredictionDirection.DOWN:
            return PredictionDisplay.COLORS["down"]
        return PredictionDisplay.COLORS["neutral"]
    
    @staticmethod
    def direction_symbol(direction: PredictionDirection) -> str:
        """Get symbol for direction"""
        if direction == PredictionDirection.UP:
            return "🟢 ↑"
        elif direction == PredictionDirection.DOWN:
            return "🔴 ↓"
        return "🟡 ↔"
    
    @staticmethod
    def confidence_bar(confidence: float, width: int = 10) -> str:
        """Create a visual confidence bar"""
        filled = int(confidence * width)
        empty = width - filled
        return "█" * filled + "░" * empty
    
    @staticmethod
    def format_volume(volume: float) -> str:
        """Format volume for display"""
        if volume >= 1_000_000:
            return f"${volume/1_000_000:.2f}M"
        elif volume >= 1_000:
            return f"${volume/1_000:.2f}K"
        return f"${volume:.2f}"
    
    def create_prediction_table(self, predictions: List[CryptoPrediction], title: str = "Crypto Predictions") -> Table:
        """Create a rich table for predictions"""
        table = Table(
            title=title,
            box=box.ROUNDED,
            header_style="bold cyan",
            border_style="blue",
            show_lines=True
        )
        
        table.add_column("Crypto", style="bold", width=8)
        table.add_column("Time", width=8)
        table.add_column("Direction", width=10)
        table.add_column("Probability", width=12)
        table.add_column("Confidence", width=15)
        table.add_column("Vol 24h", width=12)
        table.add_column("Sentiment", width=10)
        table.add_column("Question", width=50, overflow="fold")
        
        for pred in predictions:
            direction_color = self.direction_color(pred.direction)
            direction_text = self.direction_symbol(pred.direction) + " " + pred.direction.value
            
            prob_text = f"{pred.probability * 100:.1f}%"
            if pred.probability > 0.65:
                prob_style = "bold green"
            elif pred.probability < 0.35:
                prob_style = "bold red"
            else:
                prob_style = "yellow"
            
            conf_bar = self.confidence_bar(pred.confidence)
            conf_text = f"{conf_bar} {pred.confidence * 100:.0f}%"
            
            sentiment_color = "green" if "Bull" in pred.sentiment else "red" if "Bear" in pred.sentiment else "yellow"
            
            table.add_row(
                f"[bold]{pred.crypto}[/bold]",
                pred.time_frame.value,
                f"[{direction_color}]{direction_text}[/]",
                f"[{prob_style}]{prob_text}[/]",
                conf_text,
                self.format_volume(pred.volume_24h),
                f"[{sentiment_color}]{pred.sentiment}[/]",
                pred.market_question[:80] + "..." if len(pred.market_question) > 80 else pred.market_question
            )
        
        return table
    
    def create_summary_panel(self, summary: Dict[str, Any]) -> Panel:
        """Create a summary panel"""
        content = Text()
        
        content.append("📊 Market Overview\n\n", style="bold cyan")
        content.append(f"Total Markets: ", style="bold")
        content.append(f"{summary['total_markets']}\n", style="green")
        
        content.append(f"24h Volume: ", style="bold")
        content.append(f"{self.format_volume(summary['total_volume_24h'])}\n", style="yellow")
        
        content.append(f"Total Liquidity: ", style="bold")
        content.append(f"{self.format_volume(summary['total_liquidity'])}\n\n", style="cyan")
        
        content.append("Markets by Crypto:\n", style="bold")
        for crypto, count in sorted(summary.get('markets_by_crypto', {}).items(), key=lambda x: x[1], reverse=True):
            content.append(f"  {crypto}: ", style="dim")
            content.append(f"{count}\n", style="white")
        
        return Panel(content, title="📈 Polymarket Crypto Summary", border_style="blue")
    
    def create_opportunity_panel(self, predictions: List[CryptoPrediction], top_n: int = 5) -> Panel:
        """Create a panel highlighting top opportunities"""
        content = Text()
        
        content.append("🎯 Top Trading Opportunities\n\n", style="bold magenta")
        
        if not predictions:
            content.append("No high-confidence opportunities found.\n", style="dim")
        else:
            for i, pred in enumerate(predictions[:top_n], 1):
                direction_symbol = self.direction_symbol(pred.direction)
                prob_pct = pred.probability * 100
                conf_pct = pred.confidence * 100
                
                color = self.direction_color(pred.direction)
                
                content.append(f"{i}. ", style="bold")
                content.append(f"{pred.crypto} ", style=f"bold {color}")
                content.append(f"({pred.time_frame.value}) ", style="dim")
                content.append(f"{direction_symbol} ", style=color)
                content.append(f"[{prob_pct:.0f}%]", style=f"bold {color}")
                content.append(f" Confidence: {conf_pct:.0f}%\n", style="cyan")
                
                # Truncate question for display
                q_short = pred.market_question[:60] + "..." if len(pred.market_question) > 60 else pred.market_question
                content.append(f"   └─ {q_short}\n\n", style="dim")
        
        return Panel(content, title="🔥 Best Opportunities", border_style="magenta")
    
    def create_consensus_panel(self, consensus_data: Dict[str, Dict]) -> Panel:
        """Create a panel showing consensus by crypto"""
        content = Text()
        
        content.append("🎲 Market Consensus\n\n", style="bold yellow")
        
        for crypto, data in sorted(consensus_data.items()):
            direction = data.get("direction", "NEUTRAL")
            confidence = data.get("confidence", 0) * 100
            agreement = data.get("agreement", 0) * 100
            
            if direction == "UP":
                symbol = "🟢 ↑"
                color = "green"
            elif direction == "DOWN":
                symbol = "🔴 ↓"
                color = "red"
            else:
                symbol = "🟡 ↔"
                color = "yellow"
            
            content.append(f"{crypto}: ", style="bold")
            content.append(f"{symbol} ", style=color)
            content.append(f"{direction}", style=f"bold {color}")
            content.append(f" | Conf: {confidence:.0f}%", style="cyan")
            content.append(f" | Agreement: {agreement:.0f}%\n", style="dim")
        
        return Panel(content, title="📊 Consensus View", border_style="yellow")
    
    def print_header(self):
        """Print application header"""
        header = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║    🚀 POLYMARKET CRYPTO PREDICTOR 🚀                                          ║
║    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                             ║
║    Real-time cryptocurrency price predictions based on Polymarket data        ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
        console.print(header, style="bold cyan")
    
    def print_predictions(self, predictions: List[CryptoPrediction], title: str = "Predictions"):
        """Print predictions table"""
        if not predictions:
            console.print(Panel("No predictions available.", title=title, border_style="yellow"))
            return
        
        table = self.create_prediction_table(predictions, title)
        console.print(table)
    
    def print_summary(self, summary: Dict[str, Any]):
        """Print market summary"""
        panel = self.create_summary_panel(summary)
        console.print(panel)
    
    def print_opportunities(self, predictions: List[CryptoPrediction], top_n: int = 5):
        """Print top opportunities"""
        panel = self.create_opportunity_panel(predictions, top_n)
        console.print(panel)
    
    def print_loading(self, message: str = "Loading..."):
        """Print loading message"""
        console.print(f"⏳ {message}", style="dim italic")
    
    def print_error(self, message: str):
        """Print error message"""
        console.print(f"❌ Error: {message}", style="bold red")
    
    def print_success(self, message: str):
        """Print success message"""
        console.print(f"✅ {message}", style="bold green")
    
    def print_timestamp(self):
        """Print current timestamp"""
        now = datetime.now()
        console.print(f"\n📅 Last updated: {now.strftime('%Y-%m-%d %H:%M:%S')}", style="dim")


def print_welcome():
    """Print welcome message"""
    display = PredictionDisplay()
    display.print_header()
    console.print("Welcome to the Polymarket Crypto Predictor!", style="bold")
    console.print("This tool analyzes Polymarket prediction markets to provide", style="dim")
    console.print("insights on cryptocurrency price movements.\n", style="dim")