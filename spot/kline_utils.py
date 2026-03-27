"""
Spot kline helper utilities.
"""

from typing import Any, Dict, Iterable


def kline_quote_volume(row: Dict[str, Any]) -> float:
    """优先使用交易所原始 quote_asset_volume；缺失时回退为 volume*close。"""
    raw_quote_volume = row.get("quote_asset_volume")
    if raw_quote_volume is None:
        raw_quote_volume = row.get("quote_volume")
    if raw_quote_volume is not None:
        try:
            return float(raw_quote_volume)
        except (TypeError, ValueError):
            pass

    try:
        return float(row.get("volume", 0.0)) * float(row.get("close", 0.0))
    except (TypeError, ValueError):
        return 0.0


def sum_kline_quote_volume(rows: Iterable[Dict[str, Any]]) -> float:
    """按逐根 spot kline 累加 24h 成交额。"""
    return sum(kline_quote_volume(row) for row in rows)
