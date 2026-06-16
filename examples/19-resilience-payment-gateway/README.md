# 19 — Resilience Payment Gateway

Demonstrates all `varco_core.resilience` primitives applied to a flaky in-process
payment stub.  No database, no broker, no Docker required.

## What you'll learn

| Primitive | Where used | Why |
|---|---|---|
| `@timeout` | `charge()`, `get_balance()` | Cancel calls that hang forever |
| `@retry(RetryPolicy)` | `charge()` | Retry transient failures with back-off |
| `CircuitBreaker` (shared) | `charge()`, `get_balance()` | Stop hammering a dead service |
| `@hedge(HedgeConfig)` | `get_balance()` | Reduce tail latency for idempotent reads |

## Decorator stacking order

```
@timeout(0.5)           ← outermost: overall deadline (includes retry loop)
@retry(policy)          ← middle: retries per-attempt failures
CircuitBreaker.call_async()  ← innermost: fast-fail when circuit is OPEN
```

Execution order (first call):

```
timeout wrapper
  └─ retry wrapper (attempt 1)
       └─ circuit-breaker call_async()
            └─ stub.charge(...)
```

## File structure

```
app.py          Application factory — wires app state + exception handlers
stub.py         FlakeyPaymentStub — configurable failure modes for testing
gateway.py      PaymentGateway — resilience decorators on top of the stub
router.py       HTTP endpoints + AppState dataclass
tests/
  test_smoke.py 9 tests covering all primitives
```

## Endpoints

```
POST /v1/charge              body: {amount: float, card_token: str}
GET  /v1/balance/{account_id}
POST /v1/control/mode        body: {mode: "success"|"transient"|"slow"|"always_fail"}
GET  /v1/control/call-count  returns: {count: N}
```

## Running locally

```bash
cd examples/19-resilience-payment-gateway
uvicorn app:create_app --factory --reload
```

Then exercise the resilience patterns:

```bash
# Happy path
curl -X POST http://localhost:8000/v1/charge \
  -H "Content-Type: application/json" \
  -d '{"amount": 49.99, "card_token": "tok_123"}'

# Trigger retries (stub fails first 2 calls, succeeds on 3rd)
curl -X POST http://localhost:8000/v1/control/mode -d '{"mode": "transient"}'
curl -X POST http://localhost:8000/v1/charge -d '{"amount": 1, "card_token": "x"}'
curl http://localhost:8000/v1/control/call-count  # should be >= 3

# Trigger timeout
curl -X POST http://localhost:8000/v1/control/mode -d '{"mode": "slow"}'
curl -X POST http://localhost:8000/v1/charge -d '{"amount": 1, "card_token": "x"}'
# → 503 {"error": "timeout", ...}

# Trip the circuit breaker
curl -X POST http://localhost:8000/v1/control/mode -d '{"mode": "always_fail"}'
# Send 3 requests to exhaust failure_threshold=3
for i in 1 2 3; do
  curl -s -X POST http://localhost:8000/v1/charge -d '{"amount": 1, "card_token": "x"}' | jq .
done
# 4th request → 503 {"error": "circuit_open", "retry_after": ...}
curl -X POST http://localhost:8000/v1/charge -d '{"amount": 1, "card_token": "x"}'
```

## Running tests

```bash
uv run pytest .claude/worktrees/feature+examples-catalog/examples/19-resilience-payment-gateway/tests/ -v
```

## Key design notes

### Shared CircuitBreaker and Bulkhead — must be class-level or module-level singletons

A per-call instance never accumulates failures.  In this example both breakers
are class-level on `PaymentGateway`.  Tests reset them with
`PaymentGateway.reset_breakers()` between runs.

### @hedge is safe only on idempotent operations

`get_balance()` is hedged — it is a pure read.  `charge()` is NOT hedged — a
duplicate concurrent charge would double-bill the customer.

### @timeout covers the entire retry loop

The 0.5 s timeout is the outer decorator on `charge()`.  It covers all retry
attempts combined, not just a single attempt.  With `base_delay=0.05` and 3
attempts, two retries can typically complete before the timeout fires.

### CircuitOpenError is excluded from retryable_on

`RetryPolicy(retryable_on=(RuntimeError,))` means `CircuitOpenError` is NOT
retried.  Retrying an open circuit would waste the timeout budget with no
chance of success.
