"""Internal timing statistics utilities."""

from collections import deque


class TimingStats:
    """Track timing statistics over a rolling window."""

    def __init__(self, window_size: int = 100):
        self._window: deque[float] = deque(maxlen=window_size)

    def record(self, ms: float):
        self._window.append(ms)

    @property
    def count(self) -> int:
        return len(self._window)

    def avg_ms(self) -> float:
        if not self._window:
            return 0.0
        return sum(self._window) / len(self._window)

    def min_ms(self) -> float:
        return min(self._window) if self._window else 0.0

    def max_ms(self) -> float:
        return max(self._window) if self._window else 0.0

    def reset(self):
        self._window.clear()

    def __str__(self) -> str:
        if not self._window:
            return "n=0"
        return f"n={self.count} avg={self.avg_ms():.2f}ms min={self.min_ms():.2f}ms max={self.max_ms():.2f}ms"
