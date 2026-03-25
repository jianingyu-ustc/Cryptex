"""
Deribit DVOL 真实接口测试脚本。

用途：
1) 验证 DVOL 接口可用性与返回结构；
2) 验证时间有序性与数值有效性；
3) 打印可直接参考的统计摘要。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from common.binance_client import BinanceAPIConfig, BinanceClient


def _run(coro):
    # 在同步 pytest 用例中执行异步协程。
    return asyncio.run(coro)


async def _load_dvol_sample(symbol: str = "BTCUSDT", days: int = 7):
    """拉取最近 N 天 DVOL 数据样本。"""
    client = BinanceClient(
        BinanceAPIConfig(
            max_requests_per_minute=120,
            rate_limit_max_retries=4,
            rate_limit_retry_backoff_sec=0.4,
            rate_limit_retry_max_backoff_sec=4.0,
        )
    )
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, int(days)))
        rows = await client.get_dvol_index_history(
            symbol=symbol,
            interval="15m",
            limit=10_000,
            start_time=start,
            end_time=end,
        )
        return rows, start, end
    finally:
        await client.close()


def test_deribit_dvol_real_api(capsys):
    """真实 Deribit DVOL 接口联通与数据质量校验。"""
    rows, start, end = _run(_load_dvol_sample("BTCUSDT", days=7))
    assert rows, "Deribit DVOL 接口不可达或返回空数据"

    # 时间应单调不降，且 DVOL 值应为正。
    times = [r["time"] for r in rows]
    values = [float(r["dvol_value"]) for r in rows]
    assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))
    assert all(v > 0 for v in values)

    with capsys.disabled():
        print(
            "[DeribitDVOL] "
            f"rows={len(rows)} "
            f"window={start.date()}->{end.date()} "
            f"first={times[0].strftime('%Y-%m-%d %H:%M')} "
            f"last={times[-1].strftime('%Y-%m-%d %H:%M')} "
            f"min={min(values):.2f} "
            f"max={max(values):.2f}"
        )
