"""
Binance 真实接口限流压测与自动参数寻优脚本（无环境变量输入）。

用途：
1) 自动探测较优的限流参数组合；
2) 打印推荐参数，便于直接带入 `spot.main`；
3) 统计成功率、限流失败、延迟与吞吐。
"""

import asyncio
import time
from typing import Dict, List, Tuple

import pytest

from common.binance_client import BinanceAPIConfig, BinanceAPIError, BinanceClient

# Binance 限流文案通常会返回当前 IP 分钟上限（你日志中是 2400/min）。
EXCHANGE_HARD_LIMIT_PER_MIN = 2400
# 建议运行上限预留安全裕度，避免贴着硬上限震荡触发 -1003。
SAFE_HEADROOM_RATIO = 0.8

# 固定压测对象和请求规模（不通过环境变量覆盖）。
STRESS_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
STRESS_INTERVAL = "15m"
STRESS_ROUNDS = 3
STRESS_CONCURRENCY = 8

# 自动搜索空间：先粗扫 RPM，再细扫 retries/backoff。
RPM_CANDIDATES = [360, 480, 600, 720, 900, 1100]
RETRY_CANDIDATES = [4, 6, 8]
BACKOFF_CANDIDATES = [0.4, 0.6, 0.8]


def _run(coro):
    """在同步 pytest 用例中执行异步协程。"""
    return asyncio.run(coro)


def _effective_limit_per_min(config_limit_per_min: int) -> int:
    """计算最终分钟上限：取配置上限与交易所硬上限中的较小值。"""
    if config_limit_per_min and config_limit_per_min > 0:
        return min(int(config_limit_per_min), EXCHANGE_HARD_LIMIT_PER_MIN)
    return EXCHANGE_HARD_LIMIT_PER_MIN


def _suggested_safe_limit_per_min(config_limit_per_min: int) -> int:
    """按安全裕度推导建议可用上限。"""
    effective = _effective_limit_per_min(config_limit_per_min)
    return max(1, int(effective * SAFE_HEADROOM_RATIO))


async def _is_binance_reachable() -> bool:
    """连通性探测：不可达时跳过真实压测，避免误报。"""
    client = BinanceClient(
        BinanceAPIConfig(
            max_requests_per_minute=120,
            rate_limit_max_retries=2,
            rate_limit_retry_backoff_sec=0.3,
            rate_limit_retry_max_backoff_sec=1.0,
        )
    )
    try:
        return await client.test_connectivity()
    except Exception:
        return False
    finally:
        await client.close()


async def _real_request_once(
    client: BinanceClient,
    method: str,
    base_url: str,
    endpoint: str,
    params: dict,
) -> Dict:
    """执行一次真实请求，并统一返回摘要结果。"""
    started = time.perf_counter()
    try:
        data = await client._request(method, base_url, endpoint, params)
        elapsed = time.perf_counter() - started
        ok = isinstance(data, dict) or (isinstance(data, list) and len(data) > 0)
        return {
            "ok": ok,
            "elapsed_sec": elapsed,
            "rate_limit_exhausted": False,
        }
    except BinanceAPIError as exc:
        elapsed = time.perf_counter() - started
        return {
            "ok": False,
            "elapsed_sec": elapsed,
            "rate_limit_exhausted": exc.code in {-1003, -1015, 418, 429},
        }
    except Exception:
        elapsed = time.perf_counter() - started
        return {
            "ok": False,
            "elapsed_sec": elapsed,
            "rate_limit_exhausted": False,
        }


def _build_request_specs(cfg: BinanceAPIConfig) -> List[Tuple[str, str, str, Dict]]:
    """构造一轮压测请求列表。"""
    specs: List[Tuple[str, str, str, Dict]] = []
    for _ in range(STRESS_ROUNDS):
        for symbol in STRESS_SYMBOLS:
            specs.extend(
                [
                    ("GET", cfg.binance_spot_base, "/api/v3/klines", {"symbol": symbol, "interval": STRESS_INTERVAL, "limit": 200}),
                    ("GET", cfg.binance_futures_base, "/fapi/v1/markPriceKlines", {"symbol": symbol, "interval": STRESS_INTERVAL, "limit": 200}),
                    ("GET", cfg.binance_futures_base, "/fapi/v1/premiumIndexKlines", {"symbol": symbol, "interval": STRESS_INTERVAL, "limit": 200}),
                    ("GET", cfg.binance_futures_base, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 100}),
                    ("GET", cfg.binance_spot_base, "/api/v3/ticker/24hr", {"symbol": symbol}),
                ]
            )
    return specs


async def _run_real_binance_stress(cfg: BinanceAPIConfig) -> Dict:
    """按给定限流配置运行一次真实压测。"""
    client = BinanceClient(cfg)
    sem = asyncio.Semaphore(STRESS_CONCURRENCY)
    specs = _build_request_specs(cfg)

    async def _guarded_request(spec: Tuple[str, str, str, Dict]) -> Dict:
        method, base_url, endpoint, params = spec
        async with sem:
            return await _real_request_once(client, method, base_url, endpoint, params)

    try:
        started = time.perf_counter()
        results = await asyncio.gather(*[_guarded_request(spec) for spec in specs])
        elapsed_total = time.perf_counter() - started
    finally:
        await client.close()

    total = len(results)
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = total - ok_count
    rate_limit_fail_count = sum(1 for r in results if r["rate_limit_exhausted"])
    latencies = [float(r["elapsed_sec"]) for r in results]
    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
    max_latency = max(latencies) if latencies else 0.0
    success_rate = (ok_count / total) if total > 0 else 0.0
    qps = (total / elapsed_total) if elapsed_total > 0 else 0.0
    rate_limit_ratio = (rate_limit_fail_count / total) if total > 0 else 0.0

    return {
        "max_requests_per_minute": cfg.max_requests_per_minute,
        "rate_limit_max_retries": cfg.rate_limit_max_retries,
        "rate_limit_retry_backoff_sec": cfg.rate_limit_retry_backoff_sec,
        "rate_limit_retry_max_backoff_sec": cfg.rate_limit_retry_max_backoff_sec,
        "total_requests": total,
        "ok_requests": ok_count,
        "failed_requests": fail_count,
        "rate_limit_exhausted_requests": rate_limit_fail_count,
        "success_rate": success_rate,
        "rate_limit_ratio": rate_limit_ratio,
        "avg_latency_sec": avg_latency,
        "max_latency_sec": max_latency,
        "total_elapsed_sec": elapsed_total,
        "qps": qps,
    }


def _score_report(report: Dict) -> float:
    """
    报告评分：优先稳定性，其次吞吐。

    规则：
    - 成功率 < 97% 直接重罚；
    - 正常区间下，成功率越高、限流失败越少、QPS 越高越好；
    - 平均延迟越低越好。
    """
    success_rate = float(report["success_rate"])
    rate_limit_ratio = float(report["rate_limit_ratio"])
    qps = float(report["qps"])
    avg_latency = float(report["avg_latency_sec"])

    if success_rate < 0.97:
        return -1e6 + success_rate * 1e4 - rate_limit_ratio * 5e4
    return success_rate * 1e5 - rate_limit_ratio * 8e4 + qps * 120 - avg_latency * 300


async def _auto_tune_real_binance_params() -> Tuple[Dict, List[Dict], List[Dict]]:
    """两阶段自动寻优：先粗扫 RPM，再细扫 retries/backoff。"""
    coarse_reports: List[Dict] = []
    for rpm in RPM_CANDIDATES:
        cfg = BinanceAPIConfig(
            max_requests_per_minute=rpm,
            rate_limit_max_retries=6,
            rate_limit_retry_backoff_sec=0.6,
            rate_limit_retry_max_backoff_sec=10.0,
        )
        report = await _run_real_binance_stress(cfg)
        report["score"] = _score_report(report)
        coarse_reports.append(report)

    coarse_best = max(coarse_reports, key=lambda x: x["score"])
    best_rpm = int(coarse_best["max_requests_per_minute"])

    fine_reports: List[Dict] = []
    for retries in RETRY_CANDIDATES:
        for backoff in BACKOFF_CANDIDATES:
            cfg = BinanceAPIConfig(
                max_requests_per_minute=best_rpm,
                rate_limit_max_retries=int(retries),
                rate_limit_retry_backoff_sec=float(backoff),
                rate_limit_retry_max_backoff_sec=max(6.0, float(backoff) * 16.0),
            )
            report = await _run_real_binance_stress(cfg)
            report["score"] = _score_report(report)
            fine_reports.append(report)

    best_report = max(fine_reports, key=lambda x: x["score"])
    return best_report, coarse_reports, fine_reports


def _print_report_line(prefix: str, report: Dict):
    """统一压测行输出格式，便于比较。"""
    print(
        f"{prefix} rpm={report['max_requests_per_minute']:<4} "
        f"retries={report['rate_limit_max_retries']:<2} "
        f"backoff={report['rate_limit_retry_backoff_sec']:.1f}s "
        f"ok={report['ok_requests']}/{report['total_requests']} "
        f"success={report['success_rate']:.2%} "
        f"rl_fail={report['rate_limit_exhausted_requests']} "
        f"avg_lat={report['avg_latency_sec']:.3f}s "
        f"qps={report['qps']:.2f} "
        f"score={report['score']:.1f}"
    )


def test_auto_find_usable_rate_limit_params(capsys):
    """
    自动压测并输出推荐参数（不依赖环境变量）。

    输出包括：
    - 粗扫阶段每个 RPM 候选结果
    - 细扫阶段 retries/backoff 组合结果
    - 最终推荐参数（可直接用于 `spot.main`）
    """
    if not _run(_is_binance_reachable()):
        pytest.skip("Binance 网络不可达，跳过真实压测。")

    best, coarse_reports, fine_reports = _run(_auto_tune_real_binance_params())

    with capsys.disabled():
        # 打印频率上限基线。
        base_limit = _suggested_safe_limit_per_min(best["max_requests_per_minute"])
        print(
            "[RateLimitBase] "
            f"hard_limit={EXCHANGE_HARD_LIMIT_PER_MIN}/min "
            f"suggested_safe={base_limit}/min ({base_limit / 60.0:.2f}/s)"
        )

        print("[AutoTune-Coarse]")
        for item in coarse_reports:
            _print_report_line("  ", item)

        print("[AutoTune-Fine]")
        for item in fine_reports:
            _print_report_line("  ", item)

        print("[AutoTune-Best]")
        _print_report_line("  ", best)
        print(
            "  推荐 CLI 参数: "
            f"--api-max-requests-per-minute {best['max_requests_per_minute']} "
            f"--api-rate-limit-retries {best['rate_limit_max_retries']} "
            f"--api-rate-limit-backoff-sec {best['rate_limit_retry_backoff_sec']:.1f} "
            f"--api-rate-limit-backoff-max-sec {best['rate_limit_retry_max_backoff_sec']:.1f}"
        )

    assert best["total_requests"] > 0
    assert best["ok_requests"] > 0
    assert best["ok_requests"] + best["failed_requests"] == best["total_requests"]
