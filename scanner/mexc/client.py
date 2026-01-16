from __future__ import annotations

import json
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx

from scanner.config import MexcConfig
from scanner.mexc.errors import FatalHttpError, RateLimitedError, TransientHttpError, WafLimitedError
from scanner.mexc.ratelimit import TokenBucket
from scanner.obs.logging import log_event


@dataclass
class MexcMetrics:
    http_requests_total: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    http_retries_total: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    http_latency_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def record_request(self, endpoint: str, status: str, latency_ms: float) -> None:
        self.http_requests_total[(endpoint, status)] += 1
        self.http_latency_ms[endpoint].append(latency_ms)

    def record_retry(self, endpoint: str, reason: str) -> None:
        self.http_retries_total[(endpoint, reason)] += 1


class MexcClient:
    def __init__(
        self,
        config: MexcConfig,
        *,
        logger: logging.Logger | None = None,
        run_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._run_id = run_id or "n/a"
        self._metrics = MexcMetrics()
        timeout = httpx.Timeout(
            connect=config.timeout_s,
            read=config.timeout_s,
            write=config.timeout_s,
            pool=config.timeout_s,
        )
        self._client = httpx.Client(base_url=config.base_url, timeout=timeout, transport=transport)
        self._rate_limiter = rate_limiter or TokenBucket(rate_per_sec=config.max_rps)

    @property
    def metrics(self) -> MexcMetrics:
        return self._metrics

    def close(self) -> None:
        self._client.close()

    def get_exchange_info(self) -> dict:
        payload = self._request("GET", "/api/v3/exchangeInfo")
        if not isinstance(payload, dict):
            raise FatalHttpError("exchangeInfo response must be a dict", payload=payload)
        return payload

    def get_default_symbols(self) -> list[str]:
        payload = self._request("GET", "/api/v3/defaultSymbols")
        symbols = self._coerce_symbol_list(payload)
        if symbols is None:
            raise FatalHttpError("defaultSymbols response must be a list of strings", payload=payload)
        return symbols

    def get_ticker_24hr(self) -> list[dict]:
        payload = self._request("GET", "/api/v3/ticker/24hr")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise FatalHttpError("ticker/24hr response must be a list of objects", payload=payload)
        return payload

    def get_book_ticker(self) -> list[dict]:
        payload = self._request("GET", "/api/v3/ticker/bookTicker")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise FatalHttpError("bookTicker response must be a list of objects", payload=payload)
        return payload

    def get_book_ticker_symbol(self, symbol: str) -> dict:
        payload = self._request("GET", "/api/v3/ticker/bookTicker", params={"symbol": symbol})
        if not isinstance(payload, dict):
            raise FatalHttpError("bookTicker symbol response must be a dict", payload=payload)
        return payload

    def get_depth(self, symbol: str, limit: int) -> dict:
        payload = self._request("GET", "/api/v3/depth", params={"symbol": symbol, "limit": limit})
        if not isinstance(payload, dict):
            raise FatalHttpError("depth response must be a dict", payload=payload)
        return payload

    def _request(self, method: str, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        attempts = self._config.max_retries + 1
        json_retry_budget = min(2, self._config.max_retries)
        json_retry_count = 0

        for attempt in range(1, attempts + 1):
            self._rate_limiter.acquire()
            start = time.monotonic()
            status_label = "error"
            response: httpx.Response | None = None

            try:
                response = self._client.request(method, endpoint, params=params)
                latency_ms = (time.monotonic() - start) * 1000
                status_label = str(response.status_code)
                self._metrics.record_request(endpoint, status_label, latency_ms)
                log_event(
                    self._logger,
                    logging.INFO,
                    "http_request",
                    f"{method} {endpoint}",
                    endpoint=endpoint,
                    status=response.status_code,
                    attempt=attempt,
                    latency_ms=round(latency_ms, 2),
                    run_id=self._run_id,
                )

                if response.status_code == 429:
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "api_rate_limited",
                        "Rate limit response received; backing off",
                        endpoint=endpoint,
                        status=response.status_code,
                        attempt=attempt,
                        run_id=self._run_id,
                    )
                    if attempt <= self._config.max_retries:
                        self._metrics.record_retry(endpoint, "rate_limited")
                        self._backoff_sleep(attempt)
                        continue
                    raise RateLimitedError("Rate limit exceeded", status_code=429, response_text=response.text)

                if response.status_code == 403:
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "api_waf_limited",
                        "WAF limit response received; reduce request rate",
                        endpoint=endpoint,
                        status=response.status_code,
                        attempt=attempt,
                        run_id=self._run_id,
                        recommendation="reduce_request_rate",
                    )
                    if attempt <= self._config.max_retries:
                        self._metrics.record_retry(endpoint, "waf_limited")
                        self._backoff_sleep(attempt)
                        continue
                    raise WafLimitedError(
                        "WAF limit exceeded", status_code=403, response_text=response.text
                    )

                if response.status_code >= 500:
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "api_server_error",
                        "Server error response received; backing off",
                        endpoint=endpoint,
                        status=response.status_code,
                        attempt=attempt,
                        run_id=self._run_id,
                    )
                    if attempt <= self._config.max_retries:
                        self._metrics.record_retry(endpoint, "server_error")
                        self._backoff_sleep(attempt)
                        continue
                    raise TransientHttpError(
                        "Server error", status_code=response.status_code, response_text=response.text
                    )

                if response.status_code >= 400:
                    raise FatalHttpError(
                        "HTTP error", status_code=response.status_code, response_text=response.text
                    )

                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    json_retry_count += 1
                    if attempt <= self._config.max_retries and json_retry_count <= json_retry_budget:
                        self._metrics.record_retry(endpoint, "invalid_json")
                        self._backoff_sleep(attempt)
                        continue
                    raise TransientHttpError(
                        "Invalid JSON response", status_code=response.status_code, response_text=response.text
                    ) from exc

            except httpx.TimeoutException as exc:
                latency_ms = (time.monotonic() - start) * 1000
                status_label = "timeout"
                self._metrics.record_request(endpoint, status_label, latency_ms)
                log_event(
                    self._logger,
                    logging.WARNING,
                    "http_request",
                    f"{method} {endpoint}",
                    endpoint=endpoint,
                    status=None,
                    attempt=attempt,
                    latency_ms=round(latency_ms, 2),
                    run_id=self._run_id,
                )
                if attempt <= self._config.max_retries:
                    self._metrics.record_retry(endpoint, "timeout")
                    self._backoff_sleep(attempt)
                    continue
                self._log_fail(endpoint, "timeout")
                raise TransientHttpError("Request timed out") from exc

            except httpx.RequestError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                status_label = "connection_error"
                self._metrics.record_request(endpoint, status_label, latency_ms)
                log_event(
                    self._logger,
                    logging.WARNING,
                    "http_request",
                    f"{method} {endpoint}",
                    endpoint=endpoint,
                    status=None,
                    attempt=attempt,
                    latency_ms=round(latency_ms, 2),
                    run_id=self._run_id,
                )
                if attempt <= self._config.max_retries:
                    self._metrics.record_retry(endpoint, "connection_error")
                    self._backoff_sleep(attempt)
                    continue
                self._log_fail(endpoint, "connection_error")
                raise TransientHttpError("Request failed", payload=str(exc)) from exc

            except (RateLimitedError, TransientHttpError, FatalHttpError) as exc:
                self._log_fail(endpoint, type(exc).__name__)
                raise

        raise TransientHttpError("Request failed after retries")

    def _backoff_sleep(self, attempt: int) -> None:
        base = self._config.backoff_base_s
        capped = min(self._config.backoff_max_s, base * (2 ** (attempt - 1)))
        jitter = random.uniform(0, base)
        time.sleep(min(self._config.backoff_max_s, capped + jitter))

    @staticmethod
    def _coerce_symbol_list(payload: Any) -> list[str] | None:
        if isinstance(payload, list):
            if all(isinstance(item, str) for item in payload):
                return payload
            if all(isinstance(item, dict) for item in payload):
                symbols = [item.get("symbol") for item in payload if isinstance(item.get("symbol"), str)]
                return symbols or None
            return None
        if isinstance(payload, dict):
            for key in ("data", "symbols", "defaultSymbols"):
                value = payload.get(key)
                if isinstance(value, list):
                    return MexcClient._coerce_symbol_list(value)
        return None

    def _log_fail(self, endpoint: str, error_type: str) -> None:
        log_event(
            self._logger,
            logging.ERROR,
            "http_fail",
            f"Request failed for {endpoint}",
            endpoint=endpoint,
            error_type=error_type,
            run_id=self._run_id,
        )
