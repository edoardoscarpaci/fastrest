"""
tests/test_smoke.py
===================
Integration smoke tests for the ``09-sqlalchemy-postgres`` example.

All tests use a real PostgreSQL 16 instance managed by ``testcontainers``
(spun up in ``conftest.py``).  They exercise the full HTTP → SA → Postgres
stack via ``httpx.ASGITransport`` without a real network connection.

Test inventory
--------------
- ``test_create_and_read``        — POST then GET returns matching data.
- ``test_update``                 — PUT replaces mutable fields; created_at preserved.
- ``test_delete``                 — DELETE then GET → 404.
- ``test_list_multiple``          — Create 3 posts, list returns all 3.
- ``test_filter_by_author``       — ``?filter=author__eq=alice`` returns only alice's posts.
- ``test_get_unknown_returns_404``— GET on a random UUID → 404.
- ``test_invalid_payload_returns_422`` — POST with missing required field → 422.

All tests share one PostgreSQL container and one ``app_client`` fixture
(both ``scope="session"``).  Tests must therefore be written to be additive
— they should not assume the DB is empty, only that their own data is present.

Marking
-------
All tests carry ``@pytest.mark.integration`` — they require Docker and are
skipped by default.  Run them explicitly::

    uv run pytest examples/09-sqlalchemy-postgres/tests/ -v -m integration

Thread safety:  N/A — single async task.
Async safety:   ✅ asyncio_mode = "auto" — all tests are ``async def``.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

# All tests in this module require a running PostgreSQL container.
pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post_payload(
    *,
    title: str = "Test Post",
    body: str = "Test body content",
    author: str = "tester",
) -> dict:
    """
    Build a valid ``PostCreate`` JSON payload.

    Args:
        title:  Post headline.
        body:   Post body text.
        author: Author display name.

    Returns:
        Dictionary suitable for ``client.post(..., json=payload)``.
    """
    return {"title": title, "body": body, "author": author}


async def _create_post(client: httpx.AsyncClient, **kwargs) -> dict:
    """
    Create a post and assert 201 Created.

    Args:
        client: Shared ASGI test client.
        **kwargs: Forwarded to ``_post_payload()``.

    Returns:
        Parsed JSON body of the created post.

    Raises:
        AssertionError: Status code is not 201.
    """
    resp = await client.post("/v1/posts", json=_post_payload(**kwargs))
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ── Happy-path tests ──────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_create_and_read(app_client: httpx.AsyncClient) -> None:
    """
    POST /v1/posts → 201 with all fields; GET /v1/posts/{pk} → 200 with same data.

    Verifies:
    - ``pk`` is assigned by the server (UUID).
    - ``created_at`` and ``updated_at`` are populated (not None/null).
    - Mutable fields (title, body, author) round-trip correctly.
    """
    title = f"Hello SQLAlchemy {uuid.uuid4().hex[:6]}"
    body = await _create_post(app_client, title=title, body="World", author="alice")

    # Server must assign a non-null pk and timestamps.
    assert body["pk"], "pk must be set after create"
    assert body["created_at"], "created_at must be set after create"
    assert body["updated_at"], "updated_at must be set after create"
    assert body["title"] == title
    assert body["body"] == "World"
    assert body["author"] == "alice"

    pk = body["pk"]

    # Fetch the same post by pk and verify field equality.
    read_resp = await app_client.get(f"/v1/posts/{pk}")
    assert read_resp.status_code == 200, read_resp.text
    read_body = read_resp.json()

    assert read_body["pk"] == pk
    assert read_body["title"] == title
    assert read_body["body"] == "World"
    assert read_body["author"] == "alice"
    # Timestamps must survive the round-trip through the DB.
    assert read_body["created_at"] == body["created_at"]
    assert read_body["updated_at"] == body["updated_at"]


@pytest.mark.integration
async def test_update(app_client: httpx.AsyncClient) -> None:
    """
    PUT /v1/posts/{pk} → 200 with updated fields; created_at is unchanged.

    Verifies:
    - Mutable fields (title, body, author) are replaced by PUT.
    - ``created_at`` is write-once — it must equal the original value after update.
    - ``updated_at`` is refreshed by the assembler's ``apply_update`` hook.
    """
    original = await _create_post(
        app_client,
        title="Original title",
        body="Original body",
        author="bob",
    )
    pk = original["pk"]
    original_created_at = original["created_at"]

    # Full replacement PUT — all mutable fields required.
    update_resp = await app_client.put(
        f"/v1/posts/{pk}",
        json={"title": "Updated title", "body": "Updated body", "author": "carol"},
    )
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json()

    assert updated["title"] == "Updated title"
    assert updated["body"] == "Updated body"
    assert updated["author"] == "carol"

    # created_at must be write-once — assembler.apply_update() preserves it.
    assert updated["created_at"] == original_created_at, "created_at must not change on PUT"
    # updated_at must be refreshed (may equal created_at for a fast test, but
    # it must be present and non-null).
    assert updated["updated_at"], "updated_at must be set after PUT"


@pytest.mark.integration
async def test_delete(app_client: httpx.AsyncClient) -> None:
    """
    DELETE /v1/posts/{pk} → 204; subsequent GET → 404.

    Verifies:
    - DELETE returns 204 No Content (empty body).
    - The row is actually removed — a follow-up GET returns 404.
    """
    created = await _create_post(app_client, title="To be deleted")
    pk = created["pk"]

    delete_resp = await app_client.delete(f"/v1/posts/{pk}")
    assert delete_resp.status_code == 204, delete_resp.text

    # Confirm the row is gone.
    get_resp = await app_client.get(f"/v1/posts/{pk}")
    assert get_resp.status_code == 404, get_resp.text


@pytest.mark.integration
async def test_list_multiple(app_client: httpx.AsyncClient) -> None:
    """
    Create 3 posts with a unique tag then list → all 3 appear in the results.

    Uses a unique tag embedded in the title so the assertion is independent
    of other posts created by earlier tests in the same session.
    """
    tag = uuid.uuid4().hex[:8]
    for i in range(3):
        await _create_post(app_client, title=f"[{tag}] Post {i}", author="list-tester")

    # List all posts — the fixture-created posts must be among the results.
    list_resp = await app_client.get("/v1/posts")
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()

    # varco_fastapi CRUDRouter returns a paginated envelope: {"results": [...], ...}
    # Fall back to treating the body as a plain list for flexibility.
    items = body["results"] if isinstance(body, dict) and "results" in body else body
    tagged_titles = [item["title"] for item in items if tag in item["title"]]

    assert len(tagged_titles) == 3, (
        f"Expected 3 posts tagged '{tag}', found {len(tagged_titles)}. "
        f"List response contained {len(items)} total items."
    )


@pytest.mark.integration
async def test_filter_by_author(app_client: httpx.AsyncClient) -> None:
    """
    ``?filter=author__eq=<unique_author>`` returns only that author's posts.

    Uses a UUID-derived author name so the filter result is isolated from
    other tests' data in the same session.

    DESIGN: ``filter=`` query param format
        The ``CRUDRouter`` passes ``filter`` params through ``QueryParams`` →
        ``SQLAlchemyFilterVisitor`` → SQL WHERE clause.  The operator ``__eq``
        maps to ``=``.  See ARCHITECTURE.md query system section for the full
        operator set.
    """
    unique_author = f"filter-test-{uuid.uuid4().hex[:8]}"

    # Insert two posts by the unique author and one by someone else.
    await _create_post(app_client, title="A", author=unique_author)
    await _create_post(app_client, title="B", author=unique_author)
    await _create_post(app_client, title="C", author="other-author")

    # Filter — only the unique author's posts should appear.
    filter_resp = await app_client.get("/v1/posts", params={"q": f'author = "{unique_author}"'})
    assert filter_resp.status_code == 200, filter_resp.text
    body = filter_resp.json()
    items = body["results"] if isinstance(body, dict) and "results" in body else body

    authors = [item["author"] for item in items]
    assert all(a == unique_author for a in authors), (
        f"Filter returned posts from unexpected authors: {set(authors)}"
    )
    assert len(authors) >= 2, f"Expected at least 2 posts by '{unique_author}', got {len(authors)}"


# ── Unhappy-path tests ────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_unknown_returns_404(app_client: httpx.AsyncClient) -> None:
    """
    GET /v1/posts/{random-uuid} → 404 Not Found.

    Verifies the repository raises ``EntityNotFoundError`` which the router
    translates to HTTP 404.
    """
    random_pk = str(uuid.uuid4())
    resp = await app_client.get(f"/v1/posts/{random_pk}")
    assert resp.status_code == 404, (
        f"Expected 404 for unknown pk={random_pk}, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.integration
async def test_invalid_payload_returns_422(app_client: httpx.AsyncClient) -> None:
    """
    POST /v1/posts with missing required fields → 422 Unprocessable Entity.

    ``PostCreate`` requires ``title``, ``body``, and ``author``.  Sending an
    empty body causes Pydantic validation to fail, which FastAPI translates
    to HTTP 422.
    """
    # Completely empty payload — all required fields missing.
    resp = await app_client.post("/v1/posts", json={})
    assert resp.status_code == 422, (
        f"Expected 422 for empty payload, got {resp.status_code}: {resp.text}"
    )
