"""
gateway.py
==========
``PaymentGateway`` — wraps ``FlakeyPaymentStub`` with a full resilience stack:

    charge()       @timeout(0.5) → @retry(3 attempts, RuntimeError only) → shared CircuitBreaker
    get_balance()  @timeout(0.5) → @hedge(delay=0.05) → shared CircuitBreaker

Decorator stacking order
------------------------
Decorators are applied bottom-to-top (innermost first):

    @timeout(0.5)            ← outermost — overall deadline for the whole operation
    @retry(policy)           ← middle — retries the entire circuit-breaker-wrapped call
    (circuit breaker used    ← innermost — checks/updates state per attempt
     via .call_async())

Execution order (first call, no failures):

    timeout wrapper
      └─ retry wrapper (attempt 1)
           └─ circuit-breaker call_async()
                └─ stub.charge(...)

Key consequences of this order:
  - ``@timeout`` covers the ENTIRE retry loop.  If retries add up, the outer
    timeout may fire before all retries complete.
  - ``@retry`` sees the exception from ``CircuitBreaker.call_async()`` —
    ``CircuitOpenError`` IS an ``Exception``.  We exclude it from
    ``retryable_on`` so the retry loop does not try to hammer an open circuit.
  - The circuit breaker counts failures across ALL callers because
    ``_charge_breaker`` is a class-level singleton.

DESIGN: class-level ``CircuitBreaker`` singletons
    ✅ Shared instance accumulates failures from all callers —
       a per-call instance would never trip.
    ✅ ``reset_breakers()`` provides clean teardown for tests.
    ❌ All ``PaymentGateway`` instances share state.  For this demo that is
       intentional — it mirrors real-world DI usage (one breaker per dep).

DESIGN: ``@hedge`` on ``get_balance`` only
    ✅ ``get_balance`` is idempotent (read-only) — safe to issue duplicates.
    ❌ ``charge()`` is NOT idempotent — hedging would risk double-charging.

Thread safety:  ⚠️  Class-level breakers are safe within a single asyncio
                     event loop; do not share across threads.
Async safety:   ✅  All primitives use lazy asyncio.Lock / asyncio.Semaphore.
"""

from __future__ import annotations

from stub import FlakeyPaymentStub  # noqa: PLC0415 — resolved by pytest sys.path
from varco_core.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    HedgeConfig,
    RetryPolicy,
    hedge,
    retry,
    timeout,
)

# ── Shared resilience configuration ──────────────────────────────────────────

# Short recovery_timeout so circuit-breaker tests don't have to wait 30 s.
_BREAKER_CONFIG = CircuitBreakerConfig(
    failure_threshold=3,  # open after 3 consecutive failures
    recovery_timeout=5.0,  # stay OPEN for 5 s (short for test speed)
    monitored_on=(RuntimeError,),  # only RuntimeError trips the breaker
)

# jitter=False for deterministic test timing; short delays to keep tests fast.
_CHARGE_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.05,  # 50 ms — fast for tests, visible in call_count
    max_delay=0.2,
    jitter=False,
    # Exclude CircuitOpenError — retrying an open circuit wastes the timeout
    # budget and is never useful.
    retryable_on=(RuntimeError,),
)


class PaymentGateway:
    """
    Application-level gateway to the payment service.

    Wraps a ``FlakeyPaymentStub`` with the full varco resilience stack.

    ``charge()``
        Charges a card.  Stack: timeout → retry → circuit breaker.

    ``get_balance()``
        Reads balance.  Stack: timeout → hedge → circuit breaker.
        Hedging is safe here: operation is idempotent (read-only).

    Usage::

        stub    = FlakeyPaymentStub()
        gateway = PaymentGateway(stub)
        txn     = await gateway.charge(49.99, "tok_abc")
        balance = await gateway.get_balance("acc_123")

    Thread safety:  ⚠️  Circuit breakers are class-level singletons — safe
                         within a single asyncio event loop only.
    Async safety:   ✅  All decorators use lazy async primitives.

    Edge cases:
        - The 0.5 s timeout covers the entire retry loop on ``charge()``.
          With 3 attempts × 50 ms delay, the budget is tight — at most 2
          retries may complete before the timeout fires.
        - Reset breakers between tests with ``PaymentGateway.reset_breakers()``
          OR create a fresh app (which creates a fresh gateway instance via a
          fresh constructor — but the class-level breakers persist).
          Use ``reset_breakers()`` explicitly in test teardown.
    """

    # ── Class-level singletons ────────────────────────────────────────────────
    # One breaker for charge, one for balance — separate failure domains.
    # DESIGN: separate breakers per operation
    #   ✅ A slow balance endpoint doesn't trip the charge circuit.
    #   ❌ More state to manage; tests must reset both.
    _charge_breaker: CircuitBreaker = CircuitBreaker(
        _BREAKER_CONFIG, name="payment-charge"
    )
    _balance_breaker: CircuitBreaker = CircuitBreaker(
        _BREAKER_CONFIG, name="payment-balance"
    )

    def __init__(self, stub: FlakeyPaymentStub) -> None:
        """
        Args:
            stub: The underlying payment stub (or real payment client).
        """
        self._stub = stub

    # ── Public API ────────────────────────────────────────────────────────────

    async def charge(self, amount: float, card_token: str) -> dict:
        """
        Charge a card — timeout + retry + circuit breaker.

        Args:
            amount:     Charge amount.
            card_token: Opaque card token.

        Returns:
            Dict with ``transaction_id`` and ``amount`` on success.

        Raises:
            CallTimeoutError:    Entire operation (including retries) > 0.5 s.
            RetryExhaustedError: All 3 attempts raised ``RuntimeError``.
            CircuitOpenError:    Circuit is OPEN; stub is considered down.
        """
        return await self._charge_with_resilience(amount, card_token)

    async def get_balance(self, account_id: str) -> dict:
        """
        Read balance — timeout + hedge + circuit breaker.

        Args:
            account_id: Account identifier.

        Returns:
            Dict with ``account_id`` and ``balance`` on success.

        Raises:
            CallTimeoutError:  Operation > 0.5 s.
            CircuitOpenError:  Circuit is OPEN.

        Edge cases:
            - Hedge may issue a second call after 50 ms.  Both calls are
              idempotent; first to respond wins, other is cancelled.
        """
        return await self._balance_with_resilience(account_id)

    # ── Class-level helper ────────────────────────────────────────────────────

    @classmethod
    def reset_breakers(cls) -> None:
        """
        Reset both circuit breakers to CLOSED with counters zeroed.

        Call this in test teardown (or ``pytest.fixture`` autouse) when tests
        intentionally trip the circuit and the next test needs a clean state.

        Thread safety:  ✅ GIL makes individual attribute writes atomic.
        """
        cls._charge_breaker.reset()
        cls._balance_breaker.reset()

    # ── Private resilience-decorated implementations ──────────────────────────
    #
    # DESIGN: separate private methods hold the decorators.
    #   ✅ Decorators are applied at class-definition time — no per-call
    #      overhead for creating wrappers.
    #   ✅ Public methods have clean signatures; decorator stack is documented
    #      separately.
    #   ❌ Slight indirection — two methods per operation instead of one.

    @timeout(0.5)
    @retry(_CHARGE_RETRY_POLICY)
    async def _charge_with_resilience(self, amount: float, card_token: str) -> dict:
        """
        Inner charge — @timeout is outermost, @retry is middle, breaker is innermost.

        The circuit breaker is called via call_async() rather than .protect() so we
        can use a class-level shared breaker without decorating at class-definition
        time (which would create a new breaker per class instantiation).
        """
        return await self._charge_breaker.call_async(
            self._stub.charge, amount, card_token
        )

    @timeout(0.5)
    @hedge(HedgeConfig(delay=0.05))
    async def _balance_with_resilience(self, account_id: str) -> dict:
        """
        Inner balance — @timeout outermost, @hedge middle, breaker innermost.

        Hedge fires a duplicate call after 50 ms if the first hasn't returned.
        Both copies go through the circuit breaker.
        """
        return await self._balance_breaker.call_async(
            self._stub.get_balance, account_id
        )

    def __repr__(self) -> str:
        return (
            f"PaymentGateway("
            f"charge_breaker={self._charge_breaker!r}, "
            f"balance_breaker={self._balance_breaker!r})"
        )
