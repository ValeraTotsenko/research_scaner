import httpx
import pytest

from scanner.config import MexcConfig
from scanner.mexc.client import MexcClient
from scanner.mexc.errors import FatalHttpError, TransientHttpError
from scanner.mexc.ratelimit import TokenBucket


def build_client(transport: httpx.BaseTransport, *, max_retries: int = 3) -> MexcClient:
    config = MexcConfig(
        base_url="https://api.mexc.com",
        timeout_s=1,
        max_retries=max_retries,
        backoff_base_s=0,
        backoff_max_s=0,
        max_rps=1000,
    )
    return MexcClient(config, transport=transport, rate_limiter=TokenBucket(rate_per_sec=1000))


def test_rate_limit_retries_then_success() -> None:
    responses = [
        httpx.Response(429, json={"msg": "rate limit"}),
        httpx.Response(429, json={"msg": "rate limit"}),
        httpx.Response(200, json=[{"symbol": "BTCUSDT"}]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    transport = httpx.MockTransport(handler)
    client = build_client(transport)

    data = client.get_ticker_24hr()

    assert data == [{"symbol": "BTCUSDT"}]
    assert client.metrics.http_retries_total[("/api/v3/ticker/24hr", "rate_limited")] == 2
    assert sum(
        count
        for (endpoint, _status), count in client.metrics.http_requests_total.items()
        if endpoint == "/api/v3/ticker/24hr"
    ) == 3


def test_server_error_retries_then_success() -> None:
    responses = [
        httpx.Response(500, json={"msg": "server error"}),
        httpx.Response(200, json=[{"symbol": "ETHUSDT"}]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    transport = httpx.MockTransport(handler)
    client = build_client(transport)

    data = client.get_book_ticker()

    assert data == [{"symbol": "ETHUSDT"}]
    assert client.metrics.http_retries_total[("/api/v3/ticker/bookTicker", "server_error")] == 1


def test_fatal_error_no_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"msg": "bad request"})

    transport = httpx.MockTransport(handler)
    client = build_client(transport)

    with pytest.raises(FatalHttpError):
        client.get_exchange_info()

    assert call_count == 1


def test_timeout_retries_then_fails() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout("timeout", request=request)

    transport = httpx.MockTransport(handler)
    client = build_client(transport, max_retries=1)

    with pytest.raises(TransientHttpError):
        client.get_exchange_info()

    assert call_count == 2
    assert client.metrics.http_retries_total[("/api/v3/exchangeInfo", "timeout")] == 1
