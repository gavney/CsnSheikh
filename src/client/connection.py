"""Connection retry and backoff management with jitter."""

import random
from typing import Optional


class BackoffManager:
    """Calculates exponential backoff delays with randomized jitter."""

    def __init__(
        self,
        min_delay: float = 1.0,
        max_delay: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.5,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.factor = factor
        self.jitter = jitter
        self.consecutive_failures = 0
        self._current_delay = min_delay

    def next_delay(self) -> float:
        """Calculate the next backoff delay in seconds."""
        self.consecutive_failures += 1
        base_delay = min(self._current_delay, self.max_delay)
        # Apply jitter: base_delay +/- (jitter * random_uniform(-1, 1))
        jitter_range = self.jitter * base_delay
        jittered = base_delay + random.uniform(-jitter_range, jitter_range)
        delay = max(self.min_delay, min(jittered, self.max_delay))
        # Advance exponential step for next call
        self._current_delay = min(self._current_delay * self.factor, self.max_delay)
        return delay

    def reset(self) -> None:
        """Reset backoff counter to initial state after successful connection."""
        self.consecutive_failures = 0
        self._current_delay = self.min_delay
