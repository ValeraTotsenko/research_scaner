from __future__ import annotations


class StageTimeoutError(RuntimeError):
    def __init__(self, stage: str, timeout_s: float, elapsed_s: float) -> None:
        super().__init__(f"Stage {stage} exceeded timeout ({elapsed_s:.2f}s > {timeout_s:.2f}s)")
        self.stage = stage
        self.timeout_s = timeout_s
        self.elapsed_s = elapsed_s
