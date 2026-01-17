"""
MEXC API error classification for retry and health tracking.

This module defines a hierarchy of HTTP error types that enable
appropriate retry behavior and run health assessment:

- **RateLimitedError (429)**: API rate limit exceeded, retry with backoff
- **WafLimitedError (403)**: WAF/firewall limit, reduce request rate
- **TransientHttpError (5xx, timeouts)**: Temporary failure, retry
- **FatalHttpError (4xx except 403/429)**: Permanent failure, no retry

Error Classification Strategy:
    HTTP 429 → RateLimitedError → retry + mark run degraded
    HTTP 403 → WafLimitedError → retry + mark run degraded
    HTTP 5xx → TransientHttpError → retry
    HTTP 4xx → FatalHttpError → fail immediately
    Timeout  → TransientHttpError → retry
    Network  → TransientHttpError → retry

Run Health Impact:
    - RateLimitedError: api_health = "degraded_429"
    - WafLimitedError: api_health = "degraded_403"
    - 5xx errors: api_health = "degraded_5xx"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MexcHttpError(Exception):
    """
    Base exception for all MEXC API HTTP errors.

    Dataclass exception with structured error information for
    logging and debugging.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code (None for non-HTTP errors).
        response_text: Raw response body text.
        payload: Parsed response payload if available.
    """
    message: str
    status_code: int | None = None
    response_text: str | None = None
    payload: Any | None = None

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.response_text:
            parts.append(f"response={self.response_text}")
        return " | ".join(parts)


class RateLimitedError(MexcHttpError):
    """
    HTTP 429 - Rate limit exceeded.

    Raised when the API returns 429 status. The client should
    retry with exponential backoff. Run is marked as degraded.
    """
    pass


class WafLimitedError(MexcHttpError):
    """
    HTTP 403 - WAF/firewall rate limit.

    Raised when the API returns 403 due to Web Application Firewall
    limits. Indicates request rate is too high. Should reduce RPS.
    """
    pass


class TransientHttpError(MexcHttpError):
    """
    Temporary/retryable HTTP error.

    Raised for 5xx server errors, timeouts, connection errors,
    and invalid JSON responses. The client should retry.
    """
    pass


class FatalHttpError(MexcHttpError):
    """
    Permanent/non-retryable HTTP error.

    Raised for 4xx client errors (except 403, 429) and malformed
    responses that indicate a bug or invalid request. No retry.
    """
    pass
