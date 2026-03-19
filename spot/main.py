#!/usr/bin/env python3
"""
Spot Auto-Trading Subsystem - Main Entry Point.
"""

import asyncio
import argparse
import gzip
import json
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from common.binance_client import BinanceClient
from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
from .optimizer import FitnessWeights, GASettings, ParameterSpace, SpotGAOptimizer
from .strategy import SpotStrategyEngine
from .execution import SpotExecutionEngine
from .models import SpotSignal, SpotTrade, SpotPosition

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("spot.log"),
    ],
)
logger = logging.getLogger(__name__)
console = Console()


class _EventTimeFilter(logging.Filter):
    """日志时间过滤器：若记录中有 `event_time`，则用它覆盖默认输出时间。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """改写 logging 的 created/msecs，让日志前缀显示事件时间。"""
        event_time = getattr(record, "event_time", None)
        if isinstance(event_time, datetime):
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_time = event_time.astimezone(timezone.utc)
            created = event_time.timestamp()
            record.created = created
            record.msecs = (created - int(created)) * 1000
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_EventTimeFilter())


def _interval_to_seconds(interval: str) -> int:
    """将 Binance K 线周期字符串转换为秒数。"""
    if not interval or len(interval) < 2:
        return 900
    unit = interval[-1].lower()
    try:
        value = int(interval[:-1])
    except ValueError:
        return 900

    unit_map = {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 7 * 86400,
    }
    if interval[-1] == "M":
        return value * 30 * 86400
    if unit not in unit_map:
        return 900
    return max(60, value * unit_map[unit])


def _parse_utc_datetime(value: str) -> Optional[datetime]:
    """解析 ISO 时间并标准化为 UTC。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _open_json_text(path: Path, mode: str):
    """
    打开 JSON/JSON.GZ 文件。

    - `.json` 使用普通文本读写
    - `.json.gz` 使用 gzip 文本读写
    """
    if path.suffix.lower() == ".gz":
        return gzip.open(path, mode=f"{mode}t", encoding="utf-8")
    return open(path, mode=mode, encoding="utf-8")


class SpotBacktestDataClient:
    """回测数据客户端：基于内存 K 线切片模拟行情接口。"""

    def __init__(
        self,
        symbol_klines: Dict[str, List[Dict]],
        interval_seconds: int,
        symbol_mark_klines: Optional[Dict[str, List[Dict]]] = None,
        symbol_premium_klines: Optional[Dict[str, List[Dict]]] = None,
        symbol_funding_rates: Optional[Dict[str, List[Dict]]] = None,
    ):
        self.symbol_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in symbol_klines.items()
        }
        self.symbol_mark_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in (symbol_mark_klines or {}).items()
        }
        self.symbol_premium_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in (symbol_premium_klines or {}).items()
        }
        self.symbol_funding_rates = {
            symbol: sorted(rows, key=lambda x: x["funding_time"])
            for symbol, rows in (symbol_funding_rates or {}).items()
        }
        self.interval_seconds = max(60, interval_seconds)
        self.current_index = 0
        self._bars_24h = max(1, int(86400 / self.interval_seconds))

    def set_index(self, index: int):
        self.current_index = max(0, index)

    def _slice_rows(self, symbol: str) -> List[Dict]:
        rows = self.symbol_klines.get(symbol, [])
        if not rows:
            return []
        end = min(len(rows), self.current_index + 1)
        return rows[:end]

    def _current_symbol_time(self, symbol: str) -> Optional[datetime]:
        rows = self.symbol_klines.get(symbol, [])
        if not rows:
            return None
        idx = min(len(rows) - 1, self.current_index)
        return rows[idx].get("close_time") or rows[idx].get("open_time")

    def _slice_aux_klines(self, source: Dict[str, List[Dict]], symbol: str) -> List[Dict]:
        rows = source.get(symbol, [])
        if not rows:
            return []
        current_time = self._current_symbol_time(symbol)
        if not current_time:
            return []
        return [r for r in rows if (r.get("close_time") or r.get("open_time")) <= current_time]

    async def get_spot_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._slice_rows(symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        if limit > 0:
            return rows[-limit:]
        return rows

    async def get_spot_ticker(self, symbol: str):
        rows = self._slice_rows(symbol)
        if not rows:
            return None
        recent = rows[-self._bars_24h:]
        quote_volume_24h = sum(float(k["volume"]) * float(k["close"]) for k in recent)
        last_price = float(rows[-1]["close"])
        return SimpleNamespace(
            symbol=symbol,
            price=last_price,
            bid_price=last_price,
            ask_price=last_price,
            volume_24h=quote_volume_24h,
        )

    async def get_spot_price(self, symbol: str) -> Optional[float]:
        rows = self._slice_rows(symbol)
        if not rows:
            return None
        return float(rows[-1]["close"])

    async def get_mark_price_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._slice_aux_klines(self.symbol_mark_klines, symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows

    async def get_premium_index_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._slice_aux_klines(self.symbol_premium_klines, symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows

    async def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self.symbol_funding_rates.get(symbol, [])
        if not rows:
            return []
        current_time = self._current_symbol_time(symbol)
        if current_time:
            rows = [r for r in rows if r["funding_time"] <= current_time]
        if start_time:
            rows = [r for r in rows if r["funding_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["funding_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows


class SpotDisplay:
    """终端展示组件：封装信号、持仓、成交和统计表格渲染。"""

    @staticmethod
    def print_header():
        header = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║    📈 CRYPTO SPOT AUTO TRADING SYSTEM 📈                                     ║
║    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                     ║
║    Trend Following | Risk Managed | Dry-Run by Default                       ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
        console.print(header, style="bold green")

    @staticmethod
    def signals_table(signals: List[SpotSignal]) -> Table:
        table = Table(title="Spot Strategy Signals", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Symbol", width=10)
        table.add_column("Action", width=8)
        table.add_column("Price", width=12, justify="right")
        table.add_column("Confidence", width=10, justify="right")
        table.add_column("RSI", width=8, justify="right")
        table.add_column("ATR/ADX", width=14, justify="right")
        table.add_column("MA(FAST/SLOW)", width=20, justify="right")
        table.add_column("Reasons", width=46, overflow="fold")

        for s in signals:
            if s.action == "BUY":
                action = "[green]BUY[/]"
            elif s.action == "SELL":
                action = "[red]SELL[/]"
            else:
                action = "[yellow]HOLD[/]"
            reasons = s.reasons if s.reasons else ([s.reason] if s.reason else [])
            reasons_text = " | ".join(reasons[:3])
            if len(reasons) > 3:
                reasons_text += " | ..."
            table.add_row(
                s.symbol,
                action,
                f"{s.price:,.4f}" if s.price > 0 else "-",
                f"{s.confidence:.0%}",
                f"{s.rsi:.1f}",
                f"{s.atr:.4f}/{s.adx:.1f}",
                f"{s.fast_ma:.3f}/{s.slow_ma:.3f}",
                reasons_text or "-",
            )
        return table

    @staticmethod
    def positions_table(positions: List[SpotPosition]) -> Table:
        table = Table(title="Open Spot Positions", box=box.ROUNDED, header_style="bold yellow")
        table.add_column("Symbol", width=10)
        table.add_column("Qty", width=12, justify="right")
        table.add_column("Entry", width=12, justify="right")
        table.add_column("Last", width=12, justify="right")
        table.add_column("Stop", width=12, justify="right")
        table.add_column("Max", width=12, justify="right")
        table.add_column("PnL %", width=10, justify="right")
        table.add_column("Value", width=12, justify="right")

        for p in positions:
            pnl = p.unrealized_pnl_pct()
            pnl_str = f"[green]{pnl:+.2f}%[/]" if pnl >= 0 else f"[red]{pnl:+.2f}%[/]"
            table.add_row(
                p.symbol,
                f"{p.quantity:.6f}",
                f"{p.entry_price:,.4f}",
                f"{p.last_price:,.4f}",
                f"{p.stop_price:,.4f}",
                f"{p.max_price:,.4f}",
                pnl_str,
                f"${p.market_value():,.2f}",
            )
        return table

    @staticmethod
    def trade_table(trades: List[SpotTrade], title: str = "Recent Trades") -> Table:
        table = Table(title=title, box=box.MINIMAL_DOUBLE_HEAD, header_style="bold magenta")
        table.add_column("Time", width=16)
        table.add_column("Symbol", width=10)
        table.add_column("Side", width=8)
        table.add_column("Qty", width=12, justify="right")
        table.add_column("Price", width=12, justify="right")
        table.add_column("Fee", width=10, justify="right")
        table.add_column("PnL", width=10, justify="right")
        table.add_column("CumPnL", width=12, justify="right")
        table.add_column("Return", width=9, justify="right")
        table.add_column("Mode", width=7)

        for t in trades:
            pnl = t.realized_pnl_usdt
            pnl_str = f"${pnl:+.2f}"
            if t.side == "SELL":
                pnl_str = f"[green]{pnl_str}[/]" if pnl >= 0 else f"[red]{pnl_str}[/]"
            cum_pnl = f"${t.cumulative_pnl_usdt:+.2f}"
            ret = f"{t.cumulative_return_pct:+.2f}%"
            cum_pnl = f"[green]{cum_pnl}[/]" if t.cumulative_pnl_usdt >= 0 else f"[red]{cum_pnl}[/]"
            ret = f"[green]{ret}[/]" if t.cumulative_return_pct >= 0 else f"[red]{ret}[/]"
            table.add_row(
                t.timestamp.strftime("%m-%d %H:%M:%S"),
                t.symbol,
                t.side,
                f"{t.quantity:.6f}",
                f"{t.price:,.4f}",
                f"${t.fee_paid:.2f}",
                pnl_str if t.side == "SELL" else "-",
                cum_pnl,
                ret,
                "SIM" if t.dry_run else "LIVE",
            )
        return table


class SpotTradingSystem:
    """现货子系统主控制器：串联行情、策略、执行与模式流程。"""

    def __init__(self, config: SpotTradingConfig = None):
        self.config = config or DEFAULT_SPOT_CONFIG
        self.client: Optional[BinanceClient] = None
        self.strategy: Optional[SpotStrategyEngine] = None
        self.execution: Optional[SpotExecutionEngine] = None
        self._running = False
        # 记录每个 symbol 最近一次参与决策的 bar close_time，用于去重。
        self._last_decision_bar_close: Dict[str, datetime] = {}

    @staticmethod
    def _normalize_utc(ts: Optional[datetime]) -> Optional[datetime]:
        if ts is None:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    @staticmethod
    def _serialize_kline_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 kline 行中的 datetime 序列化为 ISO 字符串。"""
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("open_time", "close_time"):
                dt = item.get(key)
                if isinstance(dt, datetime):
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    item[key] = dt.astimezone(timezone.utc).isoformat()
            out.append(item)
        return out

    @staticmethod
    def _deserialize_kline_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 kline 行中的 ISO 字符串反序列化为 datetime。"""
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("open_time", "close_time"):
                raw = item.get(key)
                if isinstance(raw, str):
                    dt = _parse_utc_datetime(raw)
                    if dt is not None:
                        item[key] = dt
            out.append(item)
        out.sort(key=lambda x: x.get("open_time") or datetime.min.replace(tzinfo=timezone.utc))
        return out

    @staticmethod
    def _serialize_funding_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 funding 行中的 datetime 序列化为 ISO 字符串。"""
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            dt = item.get("funding_time")
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                item["funding_time"] = dt.astimezone(timezone.utc).isoformat()
            out.append(item)
        return out

    @staticmethod
    def _deserialize_funding_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 funding 行中的 ISO 字符串反序列化为 datetime。"""
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.get("funding_time")
            if isinstance(raw, str):
                dt = _parse_utc_datetime(raw)
                if dt is not None:
                    item["funding_time"] = dt
            out.append(item)
        out.sort(key=lambda x: x.get("funding_time") or datetime.min.replace(tzinfo=timezone.utc))
        return out

    @staticmethod
    def _trim_tail(rows: List[Dict[str, Any]], max_rows: int) -> List[Dict[str, Any]]:
        """按时间顺序保留末尾 max_rows 条；max_rows<=0 表示不裁剪。"""
        if max_rows <= 0:
            return rows
        return rows[-max_rows:]

    def _history_fetch_concurrency(self) -> int:
        """历史分页拉取并发上限（symbol 级）。"""
        return max(1, int(getattr(self.config, "history_fetch_concurrency", 1) or 1))

    def _history_page_sleep_sec(self) -> float:
        """历史分页拉取每页之间暂停秒数。"""
        return max(0.0, float(getattr(self.config, "history_page_sleep_sec", 0.0) or 0.0))

    async def _gather_symbol_tasks_limited(
        self,
        symbols: List[str],
        worker: Callable[[str], Awaitable[Any]],
    ) -> List[Any]:
        """按并发上限执行 symbol 任务，降低批量历史下载突发请求。"""
        sem = asyncio.Semaphore(self._history_fetch_concurrency())

        async def _run_one(symbol: str) -> Any:
            async with sem:
                return await worker(symbol)

        return await asyncio.gather(*[_run_one(s) for s in symbols], return_exceptions=True)

    async def _fetch_full_history_bundle(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime,
        max_rows_per_symbol: int = 0,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """
        拉取回测/GA 需要的全量历史数据并组装为 bundle。

        bundle 结构：
        - metadata
        - spot / mark / premium / funding
        """
        if not self.client:
            raise ValueError("Spot client is not initialized.")

        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            symbols = self.config.symbols[:]

        if verbose:
            console.print(
                "[cyan]Preparing backtest history bundle from realtime API...[/cyan]"
            )
            console.print(
                (
                    f"[dim]Window={start_time.date()} -> {end_time.date()} | "
                    f"interval={self.config.kline_interval} | symbols={','.join(symbols)} | "
                    f"concurrency={self._history_fetch_concurrency()} | "
                    f"page_sleep={self._history_page_sleep_sec():.2f}s[/dim]"
                )
            )
        t_all = time.perf_counter()

        if verbose:
            console.print("[bold]Stage 1/4[/bold] Fetching spot klines ...")
        t0 = time.perf_counter()
        spot_results = await self._gather_symbol_tasks_limited(
            symbols,
            lambda symbol: self._fetch_symbol_history(symbol, start_time, end_time, emit_progress=verbose),
        )

        spot_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        valid_symbols: List[str] = []
        for symbol, rows in zip(symbols, spot_results):
            if isinstance(rows, Exception):
                logger.error("History fetch failed for %s: %s", symbol, rows)
                if verbose:
                    console.print(f"[red][spot:{symbol}] failed: {rows}[/red]")
                continue
            rows = self._trim_tail(rows or [], max_rows_per_symbol)
            if rows:
                spot_by_symbol[symbol] = rows
                valid_symbols.append(symbol)
            if verbose:
                console.print(f"[dim][spot:{symbol}] rows={len(rows or [])}[/dim]")

        if not valid_symbols:
            raise ValueError("No history data fetched for any symbol.")
        if verbose:
            console.print(
                f"[green]Stage 1/4 done[/green] in {time.perf_counter() - t0:.1f}s "
                f"| valid_symbols={len(valid_symbols)}"
            )

        if verbose:
            console.print("[bold]Stage 2/4[/bold] Fetching mark-price klines ...")
        t1 = time.perf_counter()
        mark_results = await self._gather_symbol_tasks_limited(
            valid_symbols,
            lambda symbol: self._fetch_symbol_aux_klines(
                symbol,
                start_time,
                end_time,
                "get_mark_price_klines",
                emit_progress=verbose,
            ),
        )
        if verbose:
            console.print(f"[green]Stage 2/4 done[/green] in {time.perf_counter() - t1:.1f}s")

        if verbose:
            console.print("[bold]Stage 3/4[/bold] Fetching premium-index klines ...")
        t2 = time.perf_counter()
        premium_results = await self._gather_symbol_tasks_limited(
            valid_symbols,
            lambda symbol: self._fetch_symbol_aux_klines(
                symbol,
                start_time,
                end_time,
                "get_premium_index_klines",
                emit_progress=verbose,
            ),
        )
        if verbose:
            console.print(f"[green]Stage 3/4 done[/green] in {time.perf_counter() - t2:.1f}s")

        if verbose:
            console.print("[bold]Stage 4/4[/bold] Fetching funding history ...")
        t3 = time.perf_counter()
        funding_results = await self._gather_symbol_tasks_limited(
            valid_symbols,
            lambda symbol: self._fetch_symbol_funding_history(
                symbol,
                start_time,
                end_time,
                emit_progress=verbose,
            ),
        )
        if verbose:
            console.print(f"[green]Stage 4/4 done[/green] in {time.perf_counter() - t3:.1f}s")

        mark_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        premium_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        funding_by_symbol: Dict[str, List[Dict[str, Any]]] = {}

        for symbol, rows in zip(valid_symbols, mark_results):
            if isinstance(rows, list):
                mark_by_symbol[symbol] = self._trim_tail(rows, max_rows_per_symbol)
            elif verbose:
                console.print(f"[red][mark:{symbol}] failed: {rows}[/red]")
        for symbol, rows in zip(valid_symbols, premium_results):
            if isinstance(rows, list):
                premium_by_symbol[symbol] = self._trim_tail(rows, max_rows_per_symbol)
            elif verbose:
                console.print(f"[red][premium:{symbol}] failed: {rows}[/red]")
        for symbol, rows in zip(valid_symbols, funding_results):
            if isinstance(rows, list):
                funding_by_symbol[symbol] = self._trim_tail(rows, max_rows_per_symbol)
            elif verbose:
                console.print(f"[red][funding:{symbol}] failed: {rows}[/red]")

        if verbose:
            console.print(
                f"[cyan]History bundle prepared in {time.perf_counter() - t_all:.1f}s[/cyan]"
            )

        return {
            "metadata": {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "kline_interval": self.config.kline_interval,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "symbols": valid_symbols,
                "max_rows_per_symbol": max_rows_per_symbol,
            },
            "spot": spot_by_symbol,
            "mark": mark_by_symbol,
            "premium": premium_by_symbol,
            "funding": funding_by_symbol,
        }

    @classmethod
    def save_history_bundle(cls, path: str, bundle: Dict[str, Any]):
        """保存历史 bundle 到 JSON 或 JSON.GZ。"""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": dict(bundle.get("metadata", {})),
            "spot": {
                symbol: cls._serialize_kline_rows(rows)
                for symbol, rows in (bundle.get("spot", {}) or {}).items()
            },
            "mark": {
                symbol: cls._serialize_kline_rows(rows)
                for symbol, rows in (bundle.get("mark", {}) or {}).items()
            },
            "premium": {
                symbol: cls._serialize_kline_rows(rows)
                for symbol, rows in (bundle.get("premium", {}) or {}).items()
            },
            "funding": {
                symbol: cls._serialize_funding_rows(rows)
                for symbol, rows in (bundle.get("funding", {}) or {}).items()
            },
        }
        with _open_json_text(out_path, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_history_bundle(cls, path: str) -> Dict[str, Any]:
        """从 JSON 或 JSON.GZ 加载历史 bundle。"""
        in_path = Path(path)
        if not in_path.exists():
            raise FileNotFoundError(f"History data file not found: {in_path}")
        with _open_json_text(in_path, "r") as f:
            payload = json.load(f)

        spot = {
            symbol.upper(): cls._deserialize_kline_rows(rows or [])
            for symbol, rows in (payload.get("spot", {}) or {}).items()
        }
        mark = {
            symbol.upper(): cls._deserialize_kline_rows(rows or [])
            for symbol, rows in (payload.get("mark", {}) or {}).items()
        }
        premium = {
            symbol.upper(): cls._deserialize_kline_rows(rows or [])
            for symbol, rows in (payload.get("premium", {}) or {}).items()
        }
        funding = {
            symbol.upper(): cls._deserialize_funding_rows(rows or [])
            for symbol, rows in (payload.get("funding", {}) or {}).items()
        }
        metadata = dict(payload.get("metadata", {}))
        symbols_meta = metadata.get("symbols")
        if isinstance(symbols_meta, list):
            metadata["symbols"] = [str(s).upper() for s in symbols_meta]
        return {
            "metadata": metadata,
            "spot": spot,
            "mark": mark,
            "premium": premium,
            "funding": funding,
        }

    @staticmethod
    def _filter_bundle_by_window(
        bundle: Dict[str, Any],
        symbols: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """按 symbol 与时间窗过滤历史 bundle，供回测/GA 使用。"""
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            symbols = [
                s for s in (bundle.get("metadata", {}).get("symbols", []) or [])
                if isinstance(s, str)
            ]
        source_spot = bundle.get("spot", {}) or {}
        source_mark = bundle.get("mark", {}) or {}
        source_premium = bundle.get("premium", {}) or {}
        source_funding = bundle.get("funding", {}) or {}

        out_spot: Dict[str, List[Dict[str, Any]]] = {}
        out_mark: Dict[str, List[Dict[str, Any]]] = {}
        out_premium: Dict[str, List[Dict[str, Any]]] = {}
        out_funding: Dict[str, List[Dict[str, Any]]] = {}
        for symbol in symbols:
            spot_rows = [
                r for r in (source_spot.get(symbol, []) or [])
                if start_time <= (r.get("open_time") or start_time) <= end_time
            ]
            if not spot_rows:
                continue
            out_spot[symbol] = spot_rows
            out_mark[symbol] = [
                r for r in (source_mark.get(symbol, []) or [])
                if start_time <= (r.get("open_time") or start_time) <= end_time
            ]
            out_premium[symbol] = [
                r for r in (source_premium.get(symbol, []) or [])
                if start_time <= (r.get("open_time") or start_time) <= end_time
            ]
            out_funding[symbol] = [
                r for r in (source_funding.get(symbol, []) or [])
                if start_time <= (r.get("funding_time") or start_time) <= end_time
            ]

        return {
            "spot": out_spot,
            "mark": out_mark,
            "premium": out_premium,
            "funding": out_funding,
        }

    async def _latest_closed_bars(self, symbols: List[str]) -> Dict[str, datetime]:
        # 只取最新一根 K 线的 close_time，作为“是否出现新闭合 bar”的判定依据。
        if not self.client:
            return {}
        tasks = [
            self.client.get_spot_klines(
                symbol=symbol,
                interval=self.config.kline_interval,
                limit=1,
            )
            for symbol in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        latest: Dict[str, datetime] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception) or not isinstance(result, list) or not result:
                continue
            row = result[-1]
            close_time = self._normalize_utc(row.get("close_time") or row.get("open_time"))
            if close_time is not None:
                latest[symbol] = close_time
        return latest

    async def initialize(self, require_connectivity: bool = True) -> bool:
        """初始化 API 客户端、策略引擎和执行引擎。"""
        console.print("🔄 Initializing spot trading system...", style="dim")
        self.client = BinanceClient(self.config)
        if require_connectivity and not await self.client.test_connectivity():
            console.print("❌ Failed to connect Binance Spot API", style="bold red")
            return False

        self.strategy = SpotStrategyEngine(self.client, self.config)
        self.execution = SpotExecutionEngine(self.client, self.config)
        mode = "DRY-RUN" if self.config.dry_run else "LIVE"
        console.print(
            f"✅ Spot system ready [{mode}] | Initial Capital: ${self.config.initial_capital:,.2f}",
            style="green",
        )
        return True

    async def shutdown(self):
        """关闭系统并释放客户端资源。"""
        self._running = False
        if self.client:
            await self.client.close()
        console.print("✅ Spot system stopped", style="green")

    async def _scan(self) -> List[SpotSignal]:
        """执行一轮扫描：先更新持仓估值，再并行生成信号。"""
        if not self.execution or not self.strategy:
            return []
        latest_bars = await self._latest_closed_bars(self.config.symbols)
        decision_symbols: List[str] = []
        for symbol in self.config.symbols:
            latest_close = latest_bars.get(symbol)
            if latest_close is None:
                continue
            prev_close = self._last_decision_bar_close.get(symbol)
            # 仅当 close_time 前进时，才允许该 symbol 进入本轮决策。
            if prev_close is None or latest_close > prev_close:
                decision_symbols.append(symbol)
        if not decision_symbols:
            # 没有新闭合 bar 时只刷新持仓估值，不推进 bar 计数，不触发策略决策。
            await self.execution.mark_positions(advance_bar=False)
            return []

        # 有新闭合 bar 时推进一次 bar 计数，并对本轮候选 symbol 统一决策。
        await self.execution.mark_positions(advance_bar=True)
        signals = await self.strategy.analyze_symbols(
            decision_symbols,
            self.execution.positions,
            portfolio_state=self.execution.get_portfolio_state(),
        )
        for symbol in decision_symbols:
            # 决策完成后更新“最近已决策 bar”水位，避免同一 bar 重复决策。
            self._last_decision_bar_close[symbol] = latest_bars[symbol]
        return signals

    async def run_once(self, auto_execute: bool = False):
        """单次运行：扫描、展示、可选执行，并输出账户统计。"""
        if not self.execution:
            console.print("❌ Spot execution engine not initialized", style="red")
            return
        signals = await self._scan()
        if signals:
            console.print(SpotDisplay.signals_table(signals))
        else:
            console.print("[dim]No new closed bar yet; decision cycle skipped.[/dim]")

        actionable = [s for s in signals if s.is_actionable()]
        if auto_execute and actionable:
            for signal in actionable:
                trade = await self.execution.execute_signal(signal)
                if trade:
                    side_color = "green" if trade.side == "BUY" else "red"
                    reasons_text = " | ".join((trade.reasons or [trade.reason])[:3])
                    console.print(
                        f"[{side_color}]Executed {trade.side} {trade.symbol} "
                        f"qty={trade.quantity:.6f} @ {trade.price:.4f} "
                        f"({('SIM' if trade.dry_run else 'LIVE')}) | "
                        f"Fee=${trade.fee_paid:.2f} | "
                        f"Equity=${trade.account_value_after:,.2f} | "
                        f"Return={trade.cumulative_return_pct:+.2f}% "
                        f"(${trade.cumulative_pnl_usdt:+.2f}) | "
                        f"Reason: {reasons_text}[/]"
                    )

        positions = list(self.execution.positions.values())
        if positions:
            console.print(SpotDisplay.positions_table(positions))
        else:
            console.print("[yellow]No open spot positions[/yellow]")

        if self.execution.trades:
            console.print(SpotDisplay.trade_table(self.execution.trades[-10:], "Latest 10 Trades"))

        stats = self.execution.get_stats()
        summary = "\n".join([
            (
                f"Initial: ${stats['initial_capital_usdt']:,.2f} | Cash: ${stats['cash_balance_usdt']:,.2f} | "
                f"Position Value: ${stats['market_value_usdt']:,.2f} | Equity: ${stats['account_value_usdt']:,.2f}"
            ),
            (
                f"Total Return: {stats['total_return_pct']:+.2f}% (${stats['total_pnl_usdt']:+.2f}) | "
                f"Realized: ${stats['realized_pnl_usdt']:+.2f} | Unrealized: ${stats['unrealized_pnl_usdt']:+.2f}"
            ),
            (
                f"Trades: {stats['total_trades']} | Closed: {stats['closed_trades']} | "
                f"Win Rate: {stats['win_rate']:.1f}% | Open Positions: {stats['open_positions']}"
            ),
            (
                f"Fees: ${stats['fees_paid_usdt']:.2f} | Slippage: ${stats['slippage_cost_usdt']:.2f} | "
                f"Exposure: {stats['exposure_pct']:.1f}% | Daily Return: {stats['daily_return_pct']:+.2f}% "
                f"| DailyLimitHit: {stats['daily_loss_limited']}"
            ),
        ])
        console.print(Panel(summary, title="Spot Stats", border_style="cyan"))

    async def _fetch_symbol_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        emit_progress: bool = False,
    ) -> List[Dict]:
        """分页拉取单个交易对历史 K 线。"""
        if not self.client:
            return []
        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        cursor = start_time
        all_klines: List[Dict] = []
        page = 0

        while cursor < end_time:
            batch = await self.client.get_spot_klines(
                symbol=symbol,
                interval=self.config.kline_interval,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break
            page += 1

            for row in batch:
                if not all_klines or row["open_time"] > all_klines[-1]["open_time"]:
                    all_klines.append(row)

            if emit_progress and (page == 1 or page % 20 == 0):
                last_open = batch[-1].get("open_time")
                last_text = (
                    last_open.strftime("%Y-%m-%d %H:%M")
                    if isinstance(last_open, datetime)
                    else "-"
                )
                console.print(
                    f"[dim][spot:{symbol}] page={page} rows={len(all_klines)} last_open={last_text}[/dim]"
                )

            next_cursor = batch[-1]["open_time"] + timedelta(seconds=interval_seconds)
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            # Keep request rate stable during long-history download.
            pause = self._history_page_sleep_sec()
            if pause > 0:
                await asyncio.sleep(pause)

            if len(batch) < 1000:
                break

        if emit_progress:
            console.print(
                f"[blue][spot:{symbol}] done[/blue] pages={page} rows={len(all_klines)}"
            )
        return all_klines

    async def _fetch_symbol_aux_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        method_name: str,
        emit_progress: bool = False,
    ) -> List[Dict]:
        """分页拉取单个交易对的衍生 K 线（mark/premium）。"""
        if not self.client or not hasattr(self.client, method_name):
            return []
        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        cursor = start_time
        all_klines: List[Dict] = []
        getter = getattr(self.client, method_name)
        page = 0
        series_name = "mark" if "mark" in method_name else "premium"

        while cursor < end_time:
            batch = await getter(
                symbol=symbol,
                interval=self.config.kline_interval,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break
            page += 1
            for row in batch:
                if not all_klines or row["open_time"] > all_klines[-1]["open_time"]:
                    all_klines.append(row)

            if emit_progress and (page == 1 or page % 20 == 0):
                last_open = batch[-1].get("open_time")
                last_text = (
                    last_open.strftime("%Y-%m-%d %H:%M")
                    if isinstance(last_open, datetime)
                    else "-"
                )
                console.print(
                    f"[dim][{series_name}:{symbol}] page={page} rows={len(all_klines)} last_open={last_text}[/dim]"
                )

            next_cursor = batch[-1]["open_time"] + timedelta(seconds=interval_seconds)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            pause = self._history_page_sleep_sec()
            if pause > 0:
                await asyncio.sleep(pause)
            if len(batch) < 1000:
                break

        if emit_progress:
            console.print(
                f"[blue][{series_name}:{symbol}] done[/blue] pages={page} rows={len(all_klines)}"
            )
        return all_klines

    async def _fetch_symbol_funding_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        emit_progress: bool = False,
    ) -> List[Dict]:
        """分页拉取单个交易对 funding 序列。"""
        if not self.client or not hasattr(self.client, "get_funding_rate_history"):
            return []
        cursor = start_time
        all_rows: List[Dict] = []
        page = 0

        while cursor < end_time:
            batch = await self.client.get_funding_rate_history(
                symbol=symbol,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break
            page += 1
            for row in batch:
                if not all_rows or row["funding_time"] > all_rows[-1]["funding_time"]:
                    all_rows.append(row)

            if emit_progress and (page == 1 or page % 20 == 0):
                last_ft = batch[-1].get("funding_time")
                last_text = (
                    last_ft.strftime("%Y-%m-%d %H:%M")
                    if isinstance(last_ft, datetime)
                    else "-"
                )
                console.print(
                    f"[dim][funding:{symbol}] page={page} rows={len(all_rows)} last_time={last_text}[/dim]"
                )

            next_cursor = batch[-1]["funding_time"] + timedelta(seconds=1)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            pause = self._history_page_sleep_sec()
            if pause > 0:
                await asyncio.sleep(pause)
            if len(batch) < 1000:
                break

        if emit_progress:
            console.print(
                f"[blue][funding:{symbol}] done[/blue] pages={page} rows={len(all_rows)}"
            )
        return all_rows

    async def run_backtest(
        self,
        years: int = 3,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        sleep_seconds: float = 0.0,
        history_bundle: Optional[Dict[str, Any]] = None,
        save_fetched_history_file: Optional[str] = None,
        max_history_rows_per_symbol: int = 0,
    ) -> Optional[Dict]:
        """运行历史回测：复用同一策略与执行逻辑并输出结果统计。"""
        if not self.client and history_bundle is None:
            console.print("❌ Spot client not initialized", style="red")
            return None

        years = max(3, int(years))
        end_time = end_time or datetime.now(timezone.utc)
        start_time = start_time or (end_time - timedelta(days=365 * years))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        start_time = start_time.astimezone(timezone.utc)
        end_time = end_time.astimezone(timezone.utc)

        min_window = timedelta(days=365 * 3)
        if end_time - start_time < min_window:
            start_time = end_time - min_window
            console.print(
                "[yellow]Backtest window adjusted to minimum 3 years.[/yellow]",
            )

        if start_time >= end_time:
            console.print("❌ Backtest time window invalid", style="red")
            return None

        console.print(
            f"⏪ Running spot backtest | Window: {start_time.date()} -> {end_time.date()} | "
            f"Interval: {self.config.kline_interval}",
            style="bold cyan",
        )

        symbols = list(self.config.symbols)
        history_by_symbol: Dict[str, List[Dict]] = {}
        mark_history_by_symbol: Dict[str, List[Dict]] = {}
        premium_history_by_symbol: Dict[str, List[Dict]] = {}
        funding_history_by_symbol: Dict[str, List[Dict]] = {}
        skipped: List[str] = []

        if history_bundle is not None:
            # 本地数据模式：按 symbol + 时间窗过滤，不再拉取实时 API。
            local = self._filter_bundle_by_window(
                bundle=history_bundle,
                symbols=symbols,
                start_time=start_time,
                end_time=end_time,
            )
            history_by_symbol = local["spot"]
            mark_history_by_symbol = local["mark"]
            premium_history_by_symbol = local["premium"]
            funding_history_by_symbol = local["funding"]
            for symbol in symbols:
                if len(history_by_symbol.get(symbol, [])) < self.config.min_klines_required + 10:
                    skipped.append(symbol)
        else:
            # 实时拉取模式：先下载全量历史，再在内存回测；可选择落盘复用。
            fetched_bundle = await self._fetch_full_history_bundle(
                symbols=symbols,
                start_time=start_time,
                end_time=end_time,
                max_rows_per_symbol=max(0, int(max_history_rows_per_symbol)),
            )
            if save_fetched_history_file:
                self.save_history_bundle(save_fetched_history_file, fetched_bundle)
                console.print(
                    f"✅ Saved fetched backtest history to: {save_fetched_history_file}",
                    style="green",
                )

            history_by_symbol = fetched_bundle.get("spot", {}) or {}
            mark_history_by_symbol = fetched_bundle.get("mark", {}) or {}
            premium_history_by_symbol = fetched_bundle.get("premium", {}) or {}
            funding_history_by_symbol = fetched_bundle.get("funding", {}) or {}
            for symbol in symbols:
                if len(history_by_symbol.get(symbol, [])) < self.config.min_klines_required + 10:
                    skipped.append(symbol)

        active_symbols = [
            symbol
            for symbol, rows in history_by_symbol.items()
            if rows and symbol not in skipped
        ]
        if not active_symbols:
            console.print("❌ No symbols have enough history for backtest.", style="red")
            return None

        common_len = min(len(history_by_symbol[s]) for s in active_symbols)
        if common_len < self.config.min_klines_required + 2:
            console.print("❌ Backtest bars are insufficient after alignment.", style="red")
            return None

        # Align bars by using the same trailing window length across symbols.
        for symbol in active_symbols:
            history_by_symbol[symbol] = history_by_symbol[symbol][-common_len:]

        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        bt_client = SpotBacktestDataClient(
            history_by_symbol,
            interval_seconds,
            symbol_mark_klines=mark_history_by_symbol,
            symbol_premium_klines=premium_history_by_symbol,
            symbol_funding_rates=funding_history_by_symbol,
        )
        bt_config = replace(self.config, dry_run=True)
        strategy = SpotStrategyEngine(bt_client, bt_config)
        execution = SpotExecutionEngine(bt_client, bt_config)

        start_idx = bt_config.min_klines_required - 1
        total_steps = common_len - start_idx
        if total_steps <= 0:
            console.print("❌ Backtest bars are insufficient for indicator warm-up.", style="red")
            return None

        progress_step = max(1, total_steps // 20)
        for idx in range(start_idx, common_len):
            bar_time = max(history_by_symbol[s][idx]["close_time"] for s in active_symbols)
            execution.set_simulation_time(bar_time)
            bt_client.set_index(idx)
            await execution.mark_positions()
            signals = await strategy.analyze_symbols(
                active_symbols,
                execution.positions,
                portfolio_state=execution.get_portfolio_state(),
            )
            actionable = [s for s in signals if s.is_actionable()]
            for signal in actionable:
                await execution.execute_signal(signal)

            done = idx - start_idx + 1
            if done % progress_step == 0 or idx == common_len - 1:
                eq = execution.get_stats()["account_value_usdt"]
                console.print(
                    f"[dim]Backtest progress: {done}/{total_steps} bars | Equity=${eq:,.2f}[/dim]"
                )
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

        # Force close at end of backtest to lock realized stats.
        bt_client.set_index(common_len - 1)
        execution.set_simulation_time(max(history_by_symbol[s][-1]["close_time"] for s in active_symbols))
        for symbol, pos in list(execution.positions.items()):
            last_price = await bt_client.get_spot_price(symbol)
            if not last_price:
                continue
            exit_signal = SpotSignal(
                symbol=symbol,
                action="SELL",
                price=last_price,
                confidence=1.0,
                reason="end_of_backtest",
                reasons=["end_of_backtest"],
            )
            await execution.execute_signal(exit_signal)

        execution.set_simulation_time(None)
        self.strategy = strategy
        self.execution = execution

        period_start = max(
            history_by_symbol[s][start_idx]["open_time"] for s in active_symbols
        )
        period_end = min(
            history_by_symbol[s][-1]["close_time"] for s in active_symbols
        )
        meta_lines = [
            f"Symbols: {', '.join(active_symbols)}",
            f"Bars Used: {total_steps} ({self.config.kline_interval})",
            f"Aligned Window: {period_start.date()} -> {period_end.date()}",
        ]
        if skipped:
            meta_lines.append(f"Skipped (insufficient history): {', '.join(skipped)}")
        console.print(Panel("\n".join(meta_lines), title="Spot Backtest Meta", border_style="blue"))

        if execution.trades:
            console.print(SpotDisplay.trade_table(execution.trades[-20:], "Backtest Last 20 Trades"))
        else:
            console.print("[yellow]No trades generated in this backtest window.[/yellow]")

        stats = execution.get_stats()
        summary = "\n".join([
            (
                f"Initial: ${stats['initial_capital_usdt']:,.2f} | Cash: ${stats['cash_balance_usdt']:,.2f} | "
                f"Position Value: ${stats['market_value_usdt']:,.2f} | Equity: ${stats['account_value_usdt']:,.2f}"
            ),
            (
                f"Total Return: {stats['total_return_pct']:+.2f}% (${stats['total_pnl_usdt']:+.2f}) | "
                f"Realized: ${stats['realized_pnl_usdt']:+.2f} | Unrealized: ${stats['unrealized_pnl_usdt']:+.2f}"
            ),
            (
                f"Trades: {stats['total_trades']} | Closed: {stats['closed_trades']} | "
                f"Win Rate: {stats['win_rate']:.1f}% | Open Positions: {stats['open_positions']}"
            ),
            (
                f"Fees: ${stats['fees_paid_usdt']:.2f} | Slippage: ${stats['slippage_cost_usdt']:.2f} | "
                f"Exposure: {stats['exposure_pct']:.1f}% | Daily Return: {stats['daily_return_pct']:+.2f}% "
                f"| DailyLimitHit: {stats['daily_loss_limited']}"
            ),
        ])
        console.print(Panel(summary, title="Spot Backtest Stats", border_style="cyan"))
        return stats

    async def run_optimize_ga(
        self,
        backtest_start: datetime,
        backtest_end: datetime,
        ga_settings: GASettings,
        fitness_weights: FitnessWeights,
        output_dir: str,
        walkforward_train_days: int = 730,
        walkforward_test_days: int = 90,
        walkforward_step_days: Optional[int] = None,
        search_timeframe: bool = False,
        search_risk: bool = False,
        search_cost: bool = False,
        max_search_dims: int = 14,
        final_validation_days: int = 120,
        export_best_params_path: Optional[str] = None,
        history_bundle: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """运行 GA 参数优化并导出最优参数及元信息。"""
        if not self.client and history_bundle is None:
            console.print("❌ Spot client not initialized", style="red")
            return None
        if backtest_start >= backtest_end:
            console.print("❌ GA optimization time window invalid", style="red")
            return None

        parameter_space = ParameterSpace(
            base_config=self.config,
            search_timeframe=search_timeframe,
            search_risk=search_risk,
            search_cost=search_cost,
            max_search_dims=max_search_dims,
        )
        optimizer = SpotGAOptimizer(
            client=self.client,
            base_config=self.config,
            output_dir=output_dir,
            parameter_space=parameter_space,
            settings=ga_settings,
            weights=fitness_weights,
        )

        run_meta = "\n".join([
            f"Symbols: {', '.join(self.config.symbols)}",
            f"Window: {backtest_start.date()} -> {backtest_end.date()}",
            f"History Source: {'local_file' if history_bundle is not None else 'realtime_api'}",
            f"Population: {ga_settings.population_size} | Generations: {ga_settings.generations} | Workers: {ga_settings.workers}",
            f"Mutation: {ga_settings.mutation_rate:.2f} | Crossover: {ga_settings.crossover_rate:.2f} | Elitism: {ga_settings.elitism_k}",
            f"Walk-forward: train={walkforward_train_days}d test={walkforward_test_days}d step={walkforward_step_days or walkforward_test_days}d",
            f"Final Validation (sealed): {max(30, int(final_validation_days))}d",
            f"Search Dims ({len(parameter_space.dimensions)}): {', '.join(parameter_space.dimensions.keys())}",
        ])
        console.print(Panel(run_meta, title="Spot GA Optimization", border_style="magenta"))

        preloaded_spot = None
        preloaded_mark = None
        preloaded_premium = None
        preloaded_funding = None
        if history_bundle is not None:
            preloaded = self._filter_bundle_by_window(
                bundle=history_bundle,
                symbols=self.config.symbols,
                start_time=backtest_start,
                end_time=backtest_end,
            )
            preloaded_spot = preloaded["spot"]
            preloaded_mark = preloaded["mark"]
            preloaded_premium = preloaded["premium"]
            preloaded_funding = preloaded["funding"]

        result = await optimizer.run(
            symbols=self.config.symbols,
            backtest_start=backtest_start,
            backtest_end=backtest_end,
            walkforward_train_days=walkforward_train_days,
            walkforward_test_days=walkforward_test_days,
            walkforward_step_days=walkforward_step_days,
            final_validation_days=max(30, int(final_validation_days)),
            preloaded_history_by_symbol=preloaded_spot,
            preloaded_mark_history_by_symbol=preloaded_mark,
            preloaded_premium_history_by_symbol=preloaded_premium,
            preloaded_funding_history_by_symbol=preloaded_funding,
        )

        metrics = result.get("best_metrics", {})
        summary = "\n".join([
            f"Best Fitness: {result.get('best_fitness', 0.0):.4f}",
            f"Avg Annual Return: {metrics.get('avg_annual_return_pct', 0.0):+.2f}%",
            f"Avg Sharpe: {metrics.get('avg_sharpe', 0.0):.3f}",
            f"Avg Max Drawdown: {metrics.get('avg_max_drawdown_pct', 0.0):.2f}%",
            f"Worst OOS Window: {metrics.get('worst_window_return_pct', 0.0):+.2f}%",
            f"DSR Proxy: {metrics.get('dsr_proxy', 0.0):.3f}",
            f"Final Validation: {result.get('final_validation_status', 'UNKNOWN')}",
        ])
        files_text = "\n".join([
            f"best_params.json: {result.get('best_params_path')}",
            f"run_meta.json: {result.get('run_meta_path')}",
            f"generation_topk.csv: {result.get('generation_csv_path')}",
            f"cost_sensitivity_curve.csv: {result.get('cost_sensitivity_curve_path')}",
            f"worst_window_report.json: {result.get('worst_window_report_path')}",
            f"final_validation_report.json: {result.get('final_validation_report_path')}",
        ])
        console.print(Panel(summary, title="GA Best Candidate", border_style="green"))
        console.print(Panel(files_text, title="GA Output Files", border_style="blue"))

        if export_best_params_path:
            export_target = Path(export_best_params_path)
            export_target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(result.get("best_params_path"), str(export_target))
            console.print(f"✅ Exported best params to: {export_target}", style="green")

        return result

    async def monitor(self, auto_execute: bool = False):
        """持续监控模式：按固定间隔循环扫描并可选执行。"""
        self._running = True
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        while self._running:
            try:
                console.clear()
                SpotDisplay.print_header()
                await self.run_once(auto_execute=auto_execute)
                console.print(f"\n[dim]Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print(f"[dim]Next refresh in {self.config.check_interval}s...[/dim]")
                await asyncio.sleep(self.config.check_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Spot monitor error: %s", e)
                await asyncio.sleep(5)


async def main():
    defaults = SpotTradingConfig()
    parser = argparse.ArgumentParser(description="现货自动交易系统")
    parser.add_argument("--symbols", type=str, default="", help="交易对列表，逗号分隔（如 BTCUSDT,ETHUSDT）")
    parser.add_argument("--scan", action="store_true", help="单次扫描模式")
    parser.add_argument("--monitor", "-m", action="store_true", help="持续监控模式")
    parser.add_argument("--backtest", "-b", action="store_true", help="历史回测模式")
    parser.add_argument("--optimize-ga", action="store_true", help="遗传算法参数优化模式")
    parser.add_argument("--backtest-years", type=int, default=3, help="回测年数（最少 3 年）")
    parser.add_argument("--backtest-start", type=str, default="", help="回测开始 UTC 时间（ISO 格式）")
    parser.add_argument("--backtest-end", type=str, default="", help="回测结束 UTC 时间（ISO 格式）")
    parser.add_argument("--backtest-sleep", type=float, default=0.0, help="回测每根 bar 暂停秒数（0 表示尽快运行）")
    parser.add_argument("--prepare-backtest-data", action="store_true", help="仅拉取回测所需历史数据并保存到文件")
    parser.add_argument("--backtest-data-source", type=str, choices=["realtime", "local"], default="realtime", help="回测/GA 历史数据来源：实时 API 或本地文件")
    parser.add_argument("--backtest-data-file", type=str, default="", help="本地历史数据文件路径（.json 或 .json.gz）")
    parser.add_argument("--history-max-rows-per-symbol", type=int, default=0, help="拉取/保存历史时每个 symbol 最大保留条数（0 表示不限制）")
    parser.add_argument("--history-days", type=int, default=0, help="按最近 N 天拉取历史（>0 时覆盖 backtest-start）")
    parser.add_argument("--auto-execute", action="store_true", help="自动执行 BUY/SELL 信号")
    parser.add_argument("--live", action="store_true", help="开启实盘交易（默认 dry-run）")
    parser.add_argument("--interval", type=int, default=defaults.check_interval, help="监控刷新间隔（秒）")
    parser.add_argument("--api-max-requests-per-minute", type=int, default=defaults.max_requests_per_minute, help="Binance API 每分钟最大请求数（0=不限制）")
    parser.add_argument("--api-rate-limit-retries", type=int, default=defaults.rate_limit_max_retries, help="命中限流（-1003）后的最大重试次数")
    parser.add_argument("--api-rate-limit-backoff-sec", type=float, default=defaults.rate_limit_retry_backoff_sec, help="限流重试基础退避秒数")
    parser.add_argument("--api-rate-limit-backoff-max-sec", type=float, default=defaults.rate_limit_retry_max_backoff_sec, help="限流重试最大退避秒数")
    parser.add_argument("--history-fetch-concurrency", type=int, default=defaults.history_fetch_concurrency, help="历史分页拉取并发数（symbol 级，建议 1~2）")
    parser.add_argument("--history-page-sleep-sec", type=float, default=defaults.history_page_sleep_sec, help="历史分页拉取每页之间暂停秒数（限频）")
    parser.add_argument("--initial-capital", type=float, default=defaults.initial_capital, help="初始资金（USDT）")
    parser.add_argument("--usdt-per-trade", type=float, default=defaults.usdt_per_trade, help="单笔交易名义金额上限（USDT）")
    parser.add_argument("--max-positions", type=int, default=defaults.max_open_positions, help="最大同时持仓数量")
    parser.add_argument("--kline-interval", type=str, default=defaults.kline_interval, help="信号计算使用的 K 线周期")
    parser.add_argument("--decision-timing", type=str, choices=["on_close", "intrabar"], default=defaults.decision_timing, help="决策时点（收盘/盘中）")
    parser.add_argument("--fast-ma-len", type=int, default=defaults.fast_ma_period, help="快均线窗口长度")
    parser.add_argument("--slow-ma-len", type=int, default=defaults.slow_ma_period, help="慢均线窗口长度")
    parser.add_argument("--rsi-len", type=int, default=defaults.rsi_period, help="RSI 窗口长度")
    parser.add_argument("--atr-len", type=int, default=defaults.atr_period, help="ATR 窗口长度")
    parser.add_argument("--adx-len", type=int, default=defaults.adx_period, help="ADX 窗口长度")
    parser.add_argument("--pullback-tol", type=float, default=defaults.pullback_tol, help="回踩快均线容忍阈值")
    parser.add_argument("--confirm-breakout", type=float, default=defaults.confirm_breakout, help="兼容参数：百分比突破带宽（等价 ma_breakout_band）")
    parser.add_argument("--ma-breakout-band", type=float, default=defaults.ma_breakout_band, help="入场百分比带宽：close >= fast_ma*(1+ma_breakout_band)")
    parser.add_argument("--band-atr-k", type=float, default=defaults.band_atr_k, help="入场 ATR 带宽：close >= fast_ma + band_atr_k*ATR")
    parser.add_argument("--min-edge-over-cost", type=float, default=defaults.min_edge_over_cost, help="成本门槛额外边际（小数，如 0.001=0.1%%）")
    parser.add_argument("--cost-buffer-k", type=float, default=defaults.cost_buffer_k, help="双边成本缓冲倍数")
    parser.add_argument("--min-atr-pct", type=float, default=defaults.min_atr_pct, help="入场最小 ATR 波动率门槛（ATR/close）")
    parser.add_argument("--max-mark-spot-gap-pct", type=float, default=defaults.max_mark_spot_gap_pct, help="买入时允许的最大 mark/spot 偏离")
    parser.add_argument("--premium-abs-entry-max", type=float, default=defaults.premium_abs_entry_max, help="买入 premium 绝对值门槛（方案a）")
    parser.add_argument("--premium-z-entry-min", type=float, default=defaults.premium_z_entry_min, help="买入 premium zscore 下限（方案b）")
    parser.add_argument("--premium-z-entry-max", type=float, default=defaults.premium_z_entry_max, help="买入 premium zscore 上限（方案b）")
    parser.add_argument("--max-mark-spot-gap-exit", type=float, default=defaults.max_mark_spot_gap_exit, help="紧急离场 mark/spot 偏离阈值")
    parser.add_argument("--disable-overheat-derisk-exit", action="store_true", help="关闭盈利状态下 funding+premium 过热减仓离场")
    parser.add_argument("--overheat-exit-min-pnl-pct", type=float, default=defaults.overheat_exit_min_pnl_pct, help="触发过热减仓离场的最小盈利阈值")
    parser.add_argument("--overheat-exit-funding-min", type=float, default=defaults.overheat_exit_funding_min, help="触发过热减仓离场的 funding 下限")
    parser.add_argument("--overheat-exit-premium-abs-min", type=float, default=defaults.overheat_exit_premium_abs_min, help="触发过热减仓离场的 premium 绝对值下限")
    parser.add_argument("--max-mark-spot-diverge", type=float, default=defaults.max_mark_spot_diverge, help="GA/策略约束：mark 与 spot 最大偏离")
    parser.add_argument("--premium-abs-max", type=float, default=defaults.premium_abs_max, help="GA/策略约束：premium 绝对值上限")
    parser.add_argument("--funding-long-max", type=float, default=defaults.funding_long_max, help="GA/策略约束：多头 funding 上限")
    parser.add_argument("--funding-cost-buffer-k", type=float, default=defaults.funding_cost_buffer_k, help="funding 成本缓冲系数")
    parser.add_argument("--rsi-sell-min", type=float, default=defaults.rsi_sell_min, help="趋势转弱卖出 RSI 阈值")
    parser.add_argument("--min-24h-quote-volume", type=float, default=defaults.min_24h_quote_volume, help="允许开仓的最小 24h 成交额")
    parser.add_argument("--stop-loss", type=float, default=defaults.stop_loss_pct, help="兼容参数：兜底止损百分比")
    parser.add_argument("--take-profit", type=float, default=defaults.take_profit_pct, help="兼容参数：旧版止盈百分比")
    parser.add_argument("--rsi-buy-min", type=float, default=defaults.rsi_buy_min, help="BUY 的 RSI 下限")
    parser.add_argument("--rsi-buy-max", type=float, default=defaults.rsi_buy_max, help="BUY 的 RSI 上限")
    parser.add_argument("--atr-k", type=float, default=defaults.atr_k, help="初始 ATR 止损倍数")
    parser.add_argument("--trail-atr-k", type=float, default=defaults.trail_atr_k, help="ATR 追踪止盈倍数")
    parser.add_argument("--adx-min", type=float, default=defaults.adx_min, help="允许开仓的最小 ADX")
    parser.add_argument("--trend-strength-min", type=float, default=defaults.trend_strength_min, help="趋势强度代理阈值")
    parser.add_argument("--risk-per-trade-pct", type=float, default=defaults.risk_per_trade_pct, help="单笔风险占净值比例")
    parser.add_argument("--max-daily-trades", type=int, default=defaults.max_daily_trades, help="单日最大成交笔数")
    parser.add_argument("--fee-bps", type=float, default=defaults.fee_bps, help="单边手续费（bps）")
    parser.add_argument("--slippage-bps", type=float, default=defaults.slippage_bps, help="模拟滑点（bps）")
    parser.add_argument("--max-total-exposure-pct", type=float, default=defaults.max_total_exposure_pct, help="组合总暴露上限（占净值百分比）")
    parser.add_argument("--daily-loss-limit-pct", type=float, default=defaults.daily_loss_limit_pct, help="当日回撤超阈值后停止开新仓")
    parser.add_argument("--cooldown-bars", type=int, default=defaults.cooldown_bars, help="卖出后冷却 bar 数")
    parser.add_argument("--best-params-file", type=str, default="", help="加载参数文件（best_params.json）")
    parser.add_argument("--export-best-params", type=str, default="", help="导出当前参数或 GA 最优参数到指定路径")

    parser.add_argument("--ga-output-dir", type=str, default="spot/ga_runs", help="GA 输出目录（保存 CSV/JSON）")
    parser.add_argument("--ga-pop-size", type=int, default=20, help="GA 每代种群数量")
    parser.add_argument("--ga-generations", type=int, default=10, help="GA 进化代数")
    parser.add_argument("--ga-mutation-rate", type=float, default=0.15, help="GA 变异概率")
    parser.add_argument("--ga-crossover-rate", type=float, default=0.75, help="GA 交叉概率")
    parser.add_argument("--ga-elitism-k", type=int, default=2, help="每代保留的精英个体数")
    parser.add_argument("--ga-top-k-log", type=int, default=5, help="每代写入日志的 Top-K 数量")
    parser.add_argument("--ga-workers", type=int, default=1, help="GA 候选评估并行进程数（1=串行）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（用于结果复现）")
    parser.add_argument("--fitness-weights", type=str, default="", help="fitness 权重，示例 ann_return=1,sharpe=0.8")
    parser.add_argument("--walkforward-train", type=int, default=730, help="walk-forward 训练窗口（天）")
    parser.add_argument("--walkforward-test", type=int, default=90, help="walk-forward 测试窗口（天）")
    parser.add_argument("--walkforward-step", type=int, default=0, help="walk-forward 滚动步长（天，0 表示等于 test）")
    parser.add_argument("--ga-final-test-days", type=int, default=120, help="GA 封存终检窗口（天，终检期间不参与调参）")
    parser.add_argument("--ga-search-timeframe", action="store_true", help="允许 GA 搜索 K 线周期")
    parser.add_argument("--ga-search-risk", action="store_true", help="允许 GA 搜索风险参数")
    parser.add_argument("--ga-search-cost", action="store_true", help="允许 GA 搜索手续费/滑点参数")
    parser.add_argument("--ga-max-search-dims", type=int, default=14, help="GA 搜索维度上限（用于抑制过拟合）")

    args = parser.parse_args()

    config = SpotTradingConfig()
    config.dry_run = not args.live
    config.check_interval = max(5, args.interval)
    config.max_requests_per_minute = max(0, args.api_max_requests_per_minute)
    config.rate_limit_max_retries = max(0, args.api_rate_limit_retries)
    config.rate_limit_retry_backoff_sec = max(0.05, args.api_rate_limit_backoff_sec)
    config.rate_limit_retry_max_backoff_sec = max(
        config.rate_limit_retry_backoff_sec,
        args.api_rate_limit_backoff_max_sec,
    )
    config.history_fetch_concurrency = max(1, args.history_fetch_concurrency)
    config.history_page_sleep_sec = max(0.0, args.history_page_sleep_sec)
    config.initial_capital = max(100.0, args.initial_capital)
    config.usdt_per_trade = max(10.0, args.usdt_per_trade)
    config.max_open_positions = max(1, args.max_positions)
    config.kline_interval = args.kline_interval
    config.decision_timing = args.decision_timing
    config.fast_ma_period = max(2, args.fast_ma_len)
    config.slow_ma_period = max(2, args.slow_ma_len)
    config.rsi_period = max(2, args.rsi_len)
    config.atr_period = max(2, args.atr_len)
    config.adx_period = max(2, args.adx_len)
    config.pullback_tol = max(0.0001, args.pullback_tol)
    config.ma_breakout_band = max(0.0, args.ma_breakout_band)
    config.confirm_breakout = max(config.ma_breakout_band, max(0.0, args.confirm_breakout))
    config.band_atr_k = max(0.0, args.band_atr_k)
    config.min_edge_over_cost = max(0.0, args.min_edge_over_cost)
    config.cost_buffer_k = max(0.1, args.cost_buffer_k)
    config.min_atr_pct = max(0.0, args.min_atr_pct)
    config.max_mark_spot_gap_pct = max(0.0, args.max_mark_spot_gap_pct)
    config.premium_abs_entry_max = max(0.0, args.premium_abs_entry_max)
    config.premium_z_entry_min = float(args.premium_z_entry_min)
    config.premium_z_entry_max = float(args.premium_z_entry_max)
    config.max_mark_spot_gap_exit = max(0.0, args.max_mark_spot_gap_exit)
    config.enable_overheat_derisk_exit = not args.disable_overheat_derisk_exit
    config.overheat_exit_min_pnl_pct = max(0.0, args.overheat_exit_min_pnl_pct)
    config.overheat_exit_funding_min = float(args.overheat_exit_funding_min)
    config.overheat_exit_premium_abs_min = max(0.0, args.overheat_exit_premium_abs_min)
    config.max_mark_spot_diverge = max(0.0, args.max_mark_spot_diverge)
    config.premium_abs_max = max(0.0, args.premium_abs_max)
    config.funding_long_max = float(args.funding_long_max)
    config.funding_cost_buffer_k = max(0.0, args.funding_cost_buffer_k)
    config.rsi_sell_min = max(0.0, min(100.0, args.rsi_sell_min))
    config.min_24h_quote_volume = max(0.0, args.min_24h_quote_volume)
    config.stop_loss_pct = max(0.2, args.stop_loss)
    config.take_profit_pct = max(0.2, args.take_profit)
    config.rsi_buy_min = args.rsi_buy_min
    config.rsi_buy_max = args.rsi_buy_max
    config.atr_k = max(0.1, args.atr_k)
    config.trail_atr_k = max(0.1, args.trail_atr_k)
    config.adx_min = max(0.0, args.adx_min)
    config.trend_strength_min = max(0.0, args.trend_strength_min)
    config.risk_per_trade_pct = max(0.01, args.risk_per_trade_pct)
    config.max_daily_trades = max(1, args.max_daily_trades)
    config.fee_bps = max(0.0, args.fee_bps)
    config.slippage_bps = max(0.0, args.slippage_bps)
    config.max_total_exposure_pct = max(1.0, args.max_total_exposure_pct)
    config.daily_loss_limit_pct = max(0.0, args.daily_loss_limit_pct)
    config.cooldown_bars = max(0, args.cooldown_bars)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if symbols:
            config.symbols = symbols

    if args.best_params_file:
        if args.optimize_ga:
            console.print(
                "[yellow]--optimize-ga is enabled: --best-params-file is ignored (GA starts from random population).[/yellow]"
            )
        else:
            loaded = config.load_best_params(args.best_params_file)
            if not loaded:
                console.print(f"❌ Failed to load best params file: {args.best_params_file}", style="red")
                sys.exit(1)
            console.print(f"✅ Loaded best params from: {args.best_params_file}", style="green")

    SpotDisplay.print_header()

    if not config.validate():
        sys.exit(1)

    if args.export_best_params and not args.optimize_ga:
        config.save_best_params(
            args.export_best_params,
            extra={
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "mode": "runtime_config",
            },
        )
        console.print(f"✅ Exported active params to: {args.export_best_params}", style="green")

    system = SpotTradingSystem(config)
    try:
        use_local_history = args.backtest_data_source == "local"
        local_history_for_research = use_local_history and (args.backtest or args.optimize_ga)
        require_connectivity = not local_history_for_research
        if not await system.initialize(require_connectivity=require_connectivity):
            sys.exit(1)

        start_time = _parse_utc_datetime(args.backtest_start) if args.backtest_start else None
        end_time = _parse_utc_datetime(args.backtest_end) if args.backtest_end else None
        if args.backtest_start and not start_time:
            console.print("❌ Invalid --backtest-start datetime format", style="red")
            sys.exit(1)
        if args.backtest_end and not end_time:
            console.print("❌ Invalid --backtest-end datetime format", style="red")
            sys.exit(1)

        now_utc = datetime.now(timezone.utc)
        end_time = end_time or now_utc
        start_time = start_time or (end_time - timedelta(days=365 * max(3, args.backtest_years)))
        if args.history_days > 0:
            start_time = end_time - timedelta(days=max(1, int(args.history_days)))
            console.print(
                f"[dim]Using --history-days={args.history_days}, effective start={start_time.date()}[/dim]"
            )
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        start_time = start_time.astimezone(timezone.utc)
        end_time = end_time.astimezone(timezone.utc)

        history_bundle: Optional[Dict[str, Any]] = None
        if local_history_for_research:
            if not args.backtest_data_file:
                console.print(
                    "❌ --backtest-data-source local requires --backtest-data-file for --backtest/--optimize-ga",
                    style="red",
                )
                sys.exit(1)
            try:
                history_bundle = SpotTradingSystem.load_history_bundle(args.backtest_data_file)
                meta = history_bundle.get("metadata", {})
                console.print(
                    f"✅ Loaded local history: {args.backtest_data_file} "
                    f"| interval={meta.get('kline_interval', '-')}"
                    f" | symbols={','.join(meta.get('symbols', []))}",
                    style="green",
                )
            except Exception as e:
                console.print(f"❌ Failed to load local history file: {e}", style="red")
                sys.exit(1)

        if args.prepare_backtest_data:
            if args.backtest_data_source == "local":
                console.print(
                    "❌ --prepare-backtest-data needs realtime API fetch; use --backtest-data-source realtime",
                    style="red",
                )
                sys.exit(1)
            if not args.backtest_data_file:
                console.print("❌ --prepare-backtest-data requires --backtest-data-file", style="red")
                sys.exit(1)
            if args.optimize_ga or args.backtest or args.monitor or args.scan:
                console.print(
                    "[yellow]--prepare-backtest-data takes priority; other run modes are skipped.[/yellow]"
                )

            bundle = await system._fetch_full_history_bundle(
                symbols=config.symbols,
                start_time=start_time,
                end_time=end_time,
                max_rows_per_symbol=max(0, int(args.history_max_rows_per_symbol)),
                verbose=True,
            )
            SpotTradingSystem.save_history_bundle(args.backtest_data_file, bundle)
            counts = []
            for symbol in bundle.get("metadata", {}).get("symbols", []):
                counts.append(
                    f"{symbol}:spot={len((bundle.get('spot', {}).get(symbol, []) or []))},"
                    f"mark={len((bundle.get('mark', {}).get(symbol, []) or []))},"
                    f"premium={len((bundle.get('premium', {}).get(symbol, []) or []))},"
                    f"funding={len((bundle.get('funding', {}).get(symbol, []) or []))}"
                )
            summary = "\n".join([
                f"Saved: {args.backtest_data_file}",
                f"Window: {start_time.date()} -> {end_time.date()}",
                f"Interval: {bundle.get('metadata', {}).get('kline_interval', config.kline_interval)}",
                "Rows per symbol:",
                *counts,
            ])
            console.print(Panel(summary, title="Backtest History Prepared", border_style="green"))
            return

        if args.optimize_ga:
            if args.backtest or args.monitor or args.scan:
                console.print(
                    "[yellow]--optimize-ga takes priority over --backtest/--monitor/--scan.[/yellow]"
                )
            ga_settings = GASettings(
                population_size=max(4, args.ga_pop_size),
                generations=max(1, args.ga_generations),
                mutation_rate=min(1.0, max(0.0, args.ga_mutation_rate)),
                crossover_rate=min(1.0, max(0.0, args.ga_crossover_rate)),
                elitism_k=max(1, args.ga_elitism_k),
                top_k_log=max(1, args.ga_top_k_log),
                seed=args.seed,
                workers=max(1, args.ga_workers),
            )
            fitness_weights = FitnessWeights.from_string(args.fitness_weights)
            await system.run_optimize_ga(
                backtest_start=start_time,
                backtest_end=end_time,
                ga_settings=ga_settings,
                fitness_weights=fitness_weights,
                output_dir=args.ga_output_dir,
                walkforward_train_days=max(30, args.walkforward_train),
                walkforward_test_days=max(7, args.walkforward_test),
                walkforward_step_days=max(1, args.walkforward_step) if args.walkforward_step > 0 else None,
                search_timeframe=args.ga_search_timeframe,
                search_risk=args.ga_search_risk,
                search_cost=args.ga_search_cost,
                max_search_dims=max(3, args.ga_max_search_dims),
                final_validation_days=max(30, args.ga_final_test_days),
                export_best_params_path=args.export_best_params or None,
                history_bundle=history_bundle,
            )
        elif args.backtest:
            await system.run_backtest(
                years=max(3, args.backtest_years),
                start_time=start_time,
                end_time=end_time,
                sleep_seconds=max(0.0, args.backtest_sleep),
                history_bundle=history_bundle,
                save_fetched_history_file=(
                    args.backtest_data_file
                    if (args.backtest_data_source == "realtime" and bool(args.backtest_data_file))
                    else None
                ),
                max_history_rows_per_symbol=max(0, int(args.history_max_rows_per_symbol)),
            )
        elif args.monitor:
            await system.monitor(auto_execute=args.auto_execute)
        else:
            await system.run_once(auto_execute=args.auto_execute)
    except KeyboardInterrupt:
        console.print("\n[yellow]Spot trading interrupted[/yellow]")
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
