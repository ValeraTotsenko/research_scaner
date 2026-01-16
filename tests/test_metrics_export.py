import json
from pathlib import Path

import httpx
import pytest

from scanner.config import MexcConfig
from scanner.mexc.client import MexcClient
from scanner.mexc.errors import RateLimitedError, TransientHttpError
from scanner.mexc.ratelimit import TokenBucket
from scanner.obs.metrics import summarize_api_health, update_http_metrics


def _build_client(transport: httpx.BaseTransport, *, max_retries: int = 0) -> MexcClient:
    config = MexcConfig(
        base_url="https://api.mexc.com",
        timeout_s=1,
        max_retries=max_retries,
        backoff_base_s=0,
        backoff_max_s=0,
        max_rps=1000,
    )
    return MexcClient(config, transport=transport, rate_limiter=TokenBucket(rate_per_sec=1000))


def _read_metrics(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_metrics_export_increments_requests_total(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbols": []})

    client = _build_client(httpx.MockTransport(handler))
    client.get_exchange_info()

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text("{}", encoding="utf-8")
    update_http_metrics(metrics_path, client.metrics)
    payload = _read_metrics(metrics_path)

    assert payload["requests_total"] == 1
    assert payload["errors_total"] == 0
    assert payload["requests_by_status"]["200"] == 1
    assert payload["http_429_total"] == 0
    assert payload["http_403_total"] == 0
    assert payload["http_5xx_total"] == 0


def test_metrics_export_increments_errors_total(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"msg": "rate limit"})

    client = _build_client(httpx.MockTransport(handler))

    with pytest.raises(RateLimitedError):
        client.get_exchange_info()

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text("{}", encoding="utf-8")
    update_http_metrics(metrics_path, client.metrics)
    payload = _read_metrics(metrics_path)

    assert payload["requests_total"] == 1
    assert payload["errors_total"] == 1
    assert payload["requests_by_status"]["429"] == 1
    assert payload["http_429_total"] == 1
    assert payload["http_403_total"] == 0
    assert payload["http_5xx_total"] == 0


def test_api_health_marks_degraded_on_429(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"msg": "rate limit"})

    client = _build_client(httpx.MockTransport(handler))

    with pytest.raises(RateLimitedError):
        client.get_exchange_info()

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text("{}", encoding="utf-8")
    update_http_metrics(metrics_path, client.metrics)
    payload = _read_metrics(metrics_path)

    summary = summarize_api_health(payload)
    assert summary["run_health"] == "degraded"
    assert summary["http_429_total"] == 1


def test_api_health_marks_unstable_on_5xx(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"msg": "server error"})

    client = _build_client(httpx.MockTransport(handler))

    with pytest.raises(TransientHttpError):
        client.get_exchange_info()

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text("{}", encoding="utf-8")
    update_http_metrics(metrics_path, client.metrics)
    payload = _read_metrics(metrics_path)

    summary = summarize_api_health(payload)
    assert summary["run_health"] == "api_unstable"
    assert summary["http_5xx_total"] == 1
