"""Shared types + helpers for quota fetchers (quota plugin, standalone)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QuotaWindow:
    """One billing window (session / weekly / monthly) for a provider."""

    label: str
    used_percent: Optional[float] = None  # provider-reported *used* fraction 0..100
    reset_at: Optional[str] = None  # ISO-8601 UTC timestamp

    def remaining_pct(self) -> Optional[int]:
        if self.used_percent is None:
            return None
        try:
            rem = 100.0 - float(self.used_percent)
        except (TypeError, ValueError):
            return None
        rem = max(0.0, min(100.0, rem))
        return int(round(rem))


@dataclass
class AccountBalance:
    """Account money, not a key cap or a percentage denominator.

    Decimal strings preserve the precision returned by the provider.
    """

    currency: str
    total_balance: str
    granted_balance: Optional[str] = None
    topped_up_balance: Optional[str] = None


@dataclass
class QuotaResult:
    """Normalized quota for one provider, ready to cache."""

    label: str
    windows: list[QuotaWindow] = field(default_factory=list)
    plan: Optional[str] = None
    unavailable_reason: Optional[str] = None
    # Extra provider facts shown under the windows in the widget (e.g. Codex
    # "Credits balance: $12.50", "You have 2 resets banked").
    details: list[str] = field(default_factory=list)
    account_balances: list[AccountBalance] = field(default_factory=list)
    api_calls_available: Optional[bool] = None

    def has_data(self) -> bool:
        return (bool(self.windows) or bool(self.details) or bool(self.account_balances)) and self.unavailable_reason is None


def build_unavailable(label: str, reason: str) -> QuotaResult:
    return QuotaResult(label=label, windows=[], plan=None, unavailable_reason=reason)


class Deadline:
    """A wall-clock budget shared across a fetcher's serial requests.

    ``quota_cache.REFRESH_BUDGET_S`` (20s) bounds the whole sweep, and a
    provider that overruns it is recorded as ``timeout`` and loses its previous
    value. A per-request ``timeout=15`` is therefore not a bound on the
    provider: three serial requests at 15s each is 45s, and four 15s retries
    with backoff is over 60s.

    Clamp each request to whatever remains, so the provider as a whole stays
    inside the sweep. ``minimax`` already does this with a private
    ``_DEADLINE_S``; this makes it available to every provider and testable
    with one formula.
    """

    def __init__(self, budget: float, clock=None) -> None:
        # Resolved at call time, not bound as a default argument, so a test can
        # patch time.monotonic after import and still drive the clock.
        self._clock = clock if clock is not None else time.monotonic
        self._expires = self._clock() + max(0.0, float(budget))

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self._expires - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def slice(self, cap: float) -> float:
        """The timeout to hand the next request: the smaller of ``cap`` and
        what is left. Returns 0.0 once spent, which fails fast rather than
        blocking for the full per-request timeout."""
        return min(float(cap), self.remaining())
