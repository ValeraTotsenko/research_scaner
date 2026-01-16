from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MexcHttpError(Exception):
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
    pass


class WafLimitedError(MexcHttpError):
    pass


class TransientHttpError(MexcHttpError):
    pass


class FatalHttpError(MexcHttpError):
    pass
