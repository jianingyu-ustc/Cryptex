import asyncio

import pytest

from common.binance_client import BinanceAPIConfig, BinanceAPIError, BinanceClient

# Binance 在报错文案中会返回当前 IP 的分钟级限制（你日志里是 2400/min）。
EXCHANGE_HARD_LIMIT_PER_MIN = 2400
# 给批量抓数留出裕度，避免打满后抖动触发 -1003。
SAFE_HEADROOM_RATIO = 0.8


def _run(coro):
    """在同步 pytest 用例中执行异步协程。"""
    return asyncio.run(coro)


def _effective_limit_per_min(config_limit_per_min: int) -> int:
    """计算最终可用的分钟频率上限。"""
    if config_limit_per_min and config_limit_per_min > 0:
        return min(int(config_limit_per_min), EXCHANGE_HARD_LIMIT_PER_MIN)
    return EXCHANGE_HARD_LIMIT_PER_MIN


def _suggested_safe_limit_per_min(config_limit_per_min: int) -> int:
    """按安全裕度给出建议频率上限。"""
    eff = _effective_limit_per_min(config_limit_per_min)
    return max(1, int(eff * SAFE_HEADROOM_RATIO))


class _FakeResponseCtx:
    """模拟 aiohttp 请求上下文管理器。"""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    """按预设 payload 序列返回响应，用于稳定复现重试路径。"""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0

    def _next_payload(self):
        idx = min(self.calls, max(0, len(self._payloads) - 1))
        payload = self._payloads[idx]
        self.calls += 1
        return payload

    def get(self, url, params=None, headers=None):
        return _FakeResponseCtx(self._next_payload())

    def post(self, url, params=None, headers=None):
        return _FakeResponseCtx(self._next_payload())

    def delete(self, url, params=None, headers=None):
        return _FakeResponseCtx(self._next_payload())


def test_print_usable_rate_limit_ceiling(capsys):
    """打印当前配置下可使用的频率上限（便于命令行调参对照）。"""
    cfg = BinanceAPIConfig(max_requests_per_minute=900)
    effective = _effective_limit_per_min(cfg.max_requests_per_minute)
    safe_limit = _suggested_safe_limit_per_min(cfg.max_requests_per_minute)
    safe_per_sec = safe_limit / 60.0

    # 关闭 capture，确保 pytest 运行时直接在终端打印。
    with capsys.disabled():
        print(
            "[RateLimit] 交易所硬上限: "
            f"{EXCHANGE_HARD_LIMIT_PER_MIN}/min; "
            f"当前配置上限: {cfg.max_requests_per_minute}/min; "
            f"有效上限: {effective}/min; "
            f"建议可用上限: {safe_limit}/min ({safe_per_sec:.2f}/s)"
        )

    assert safe_limit <= effective
    assert safe_limit > 0


def test_rate_limit_retry_then_success(monkeypatch):
    """命中 -1003 时应按指数退避重试，并在后续成功时返回结果。"""
    cfg = BinanceAPIConfig(
        max_requests_per_minute=0,
        rate_limit_max_retries=6,
        rate_limit_retry_backoff_sec=0.6,
        rate_limit_retry_max_backoff_sec=10.0,
    )
    client = BinanceClient(cfg)
    fake_session = _FakeSession(
        [
            {"code": -1003, "msg": "Too many requests"},
            {"code": -1003, "msg": "Too many requests"},
            {"ok": True, "price": "123.45"},
        ]
    )

    async def _fake_get_session():
        return fake_session

    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(client, "_get_session", _fake_get_session)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    result = _run(client._request("GET", "https://example.com", "/fapi/v1/markPriceKlines", {}))

    assert result["ok"] is True
    assert fake_session.calls == 3
    assert sleep_calls == [0.6, 1.2]


def test_rate_limit_retry_exhausted_should_raise(monkeypatch):
    """超过重试上限后，应抛出原始 BinanceAPIError。"""
    cfg = BinanceAPIConfig(
        max_requests_per_minute=0,
        rate_limit_max_retries=2,
        rate_limit_retry_backoff_sec=0.5,
        rate_limit_retry_max_backoff_sec=1.0,
    )
    client = BinanceClient(cfg)
    fake_session = _FakeSession(
        [
            {"code": -1003, "msg": "Too many requests"},
            {"code": -1003, "msg": "Too many requests"},
            {"code": -1003, "msg": "Too many requests"},
        ]
    )

    async def _fake_get_session():
        return fake_session

    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(client, "_get_session", _fake_get_session)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    with pytest.raises(BinanceAPIError) as exc:
        _run(client._request("GET", "https://example.com", "/fapi/v1/premiumIndexKlines", {}))

    assert exc.value.code == -1003
    # retries=2 => 触发两次 sleep（0.5, 1.0），第三次仍失败后抛错。
    assert sleep_calls == [0.5, 1.0]
    assert fake_session.calls == 3
