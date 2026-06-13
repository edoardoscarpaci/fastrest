"""
stub.py
=======
A configurable in-process payment stub that can simulate real-world failure modes.

Used by the gateway to demonstrate each resilience primitive without needing any
external services (no Docker, no broker).

DESIGN: mode-based stub over multiple stub classes
    ✅ Single class — easy to swap modes mid-test via the control endpoints.
    ✅ ``_call_count`` gives tests a reliable way to verify how many times
       the stub was actually reached (e.g. proving retries fired).
    ❌ All methods share one ``mode`` — fine for this demo; a production stub
       would isolate behaviour per method.
"""

from __future__ import annotations

import asyncio
from enum import Enum


# ── FailMode ──────────────────────────────────────────────────────────────────


class FailMode(str, Enum):
    """
    Controls how the stub behaves on each call.

    SUCCESS
        Returns a well-formed result immediately.

    TRANSIENT
        Fails with ``RuntimeError`` on the first two calls, then succeeds.
        Intended for retry tests: verifies that ``@retry`` fires multiple times
        before a success is returned.

    SLOW
        Sleeps for 10 seconds (much longer than the gateway timeout of 0.5 s).
        Intended for timeout tests: verifies that ``@timeout`` cancels the call.

    ALWAYS_FAIL
        Raises ``RuntimeError`` on every call forever.
        Intended for circuit-breaker tests: verifies that repeated failures
        trip the breaker open.
    """

    SUCCESS = "success"
    TRANSIENT = "transient"  # fails first 2 calls, then succeeds
    SLOW = "slow"  # sleeps longer than the gateway timeout
    ALWAYS_FAIL = "always_fail"  # never succeeds — trips the circuit breaker


# ── FlakeyPaymentStub ─────────────────────────────────────────────────────────


class FlakeyPaymentStub:
    """
    In-process stub for an external payment service.

    Simulates realistic failure modes so each resilience primitive can be
    exercised without a real network dependency.

    Thread safety:  ⚠️  Not thread-safe — intended for single-threaded
                        asyncio tests only.
    Async safety:   ✅  All methods are ``async def`` and safe to await
                        concurrently *within a single event loop* (but the
                        mode/counter are not protected by a lock; concurrent
                        mutation from multiple coroutines is not expected in
                        these tests).

    Attributes:
        mode:         Active failure mode.  Change via test control endpoints.
        _call_count:  Cumulative count of all calls across all methods and
                      modes.  Reset by creating a fresh stub (per-test app).

    Edge cases:
        - In ``TRANSIENT`` mode the stub counts calls per stub instance, not
          per gateway method — creating a fresh app resets the counter.
        - In ``SLOW`` mode ``get_balance`` also sleeps, so both endpoints
          exercise the timeout path.
    """

    def __init__(self) -> None:
        self.mode: FailMode = FailMode.SUCCESS
        # Cumulative call count — shared across charge() and get_balance().
        # Tests use /v1/control/call-count to read this.
        self._call_count: int = 0

    async def charge(self, amount: float, card_token: str) -> dict:
        """
        Attempt to charge a card.

        Args:
            amount:     Amount to charge in the stub currency.
            card_token: Opaque token identifying the card.

        Returns:
            Dict with ``transaction_id`` and ``amount`` on success.

        Raises:
            RuntimeError: When mode is TRANSIENT (first 2 calls) or ALWAYS_FAIL.

        Edge cases:
            - TRANSIENT mode uses the *global* ``_call_count``, not a
              per-method counter.  If charge() and get_balance() are both
              called before the 3rd attempt, the transient window may behave
              differently than expected.  In tests, only one method is
              exercised per mode switch.
        """
        self._call_count += 1

        if self.mode == FailMode.TRANSIENT:
            # Fail on the first two calls to prove retries actually fire
            if self._call_count < 3:
                raise RuntimeError(f"transient failure on attempt {self._call_count}")
        elif self.mode == FailMode.SLOW:
            # Sleep far longer than the gateway timeout (0.5 s) — the gateway's
            # @timeout will cancel this coroutine before it wakes up
            await asyncio.sleep(10.0)
        elif self.mode == FailMode.ALWAYS_FAIL:
            raise RuntimeError("upstream payment service unavailable")

        return {
            "transaction_id": f"txn-{self._call_count}",
            "amount": amount,
        }

    async def get_balance(self, account_id: str) -> dict:
        """
        Return a fake account balance.

        Args:
            account_id: Identifier for the account.

        Returns:
            Dict with ``account_id`` and ``balance`` on success.

        Raises:
            RuntimeError: When mode is ALWAYS_FAIL (or SLOW times out).

        Edge cases:
            - TRANSIENT mode does NOT affect get_balance — the threshold check
              in charge() uses the shared _call_count which may already be ≥ 3
              when get_balance is called.  The intent is to keep each mode
              focused on demonstrating one resilience pattern.
        """
        self._call_count += 1

        if self.mode == FailMode.SLOW:
            # Same slow path as charge() — exercises timeout on balance endpoint
            await asyncio.sleep(10.0)
        elif self.mode == FailMode.ALWAYS_FAIL:
            raise RuntimeError("upstream payment service unavailable")

        return {
            "account_id": account_id,
            "balance": 1000.0,
        }
