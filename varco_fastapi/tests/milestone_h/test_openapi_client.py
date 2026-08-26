"""
Milestone H tests — OpenAPIClient (runtime typed client from OpenAPI spec).

Tests cover:
- from_dict: builds a live client from an in-memory spec
- Method generation: one method per operation, named from operationId
- Path params: positional args forwarded into URL template
- Query params: keyword-only args forwarded as query_params
- Body: passed through to _request
- $ref resolution: schemas referenced via $ref are resolved
- Pydantic models: built for components/schemas entries
- servers[0].url used as base_url fallback
- Duplicate operationId: deduplicated with counter suffix
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from varco_fastapi.client.openapi import OpenAPIClient, _resolve_refs, _to_snake

# ── Minimal specs for testing ─────────────────────────────────────────────────

PETSTORE_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Petstore", "version": "1.0"},
    "servers": [{"url": "https://petstore.example.com/api/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "parameters": [
                    {"in": "query", "name": "limit", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Pet"},
                                }
                            }
                        }
                    }
                },
            },
            "post": {
                "operationId": "createPet",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/CreatePetRequest"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}
                        }
                    }
                },
            },
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "getPetById",
                "parameters": [
                    {"in": "path", "name": "petId", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}
                        }
                    }
                },
            },
        },
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "tag": {"type": "string"},
                },
            },
            "CreatePetRequest": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "tag": {"type": "string"},
                },
            },
        }
    },
}


# ── _resolve_refs helper ──────────────────────────────────────────────────────


def test_resolve_refs_simple():
    root = {
        "components": {"schemas": {"Foo": {"type": "object"}}},
        "paths": {},
    }
    schema = {"$ref": "#/components/schemas/Foo"}
    resolved = _resolve_refs(schema, root)
    assert resolved == {"type": "object"}


def test_resolve_refs_nested():
    root = {
        "components": {
            "schemas": {
                "Bar": {"type": "string"},
                "Foo": {"properties": {"bar": {"$ref": "#/components/schemas/Bar"}}},
            }
        }
    }
    resolved = _resolve_refs({"$ref": "#/components/schemas/Foo"}, root)
    assert resolved["properties"]["bar"] == {"type": "string"}


def test_resolve_refs_passthrough_for_non_ref():
    obj = {"type": "object", "properties": {"x": {"type": "integer"}}}
    assert _resolve_refs(obj, {}) == obj


# ── _to_snake helper ──────────────────────────────────────────────────────────


def test_to_snake_camel():
    assert _to_snake("listPets") == "list_pets"


def test_to_snake_path_slug():
    assert _to_snake("getPetById") == "get_pet_by_id"


def test_to_snake_already_snake():
    assert _to_snake("list_pets") == "list_pets"


# ── from_dict: basic construction ─────────────────────────────────────────────


def test_from_dict_builds_client():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    assert isinstance(client, OpenAPIClient)


def test_from_dict_uses_servers_url_as_fallback():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC)
    assert client._client_instance._base_url == "https://petstore.example.com/api/v1"


def test_from_dict_explicit_base_url_overrides_servers():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://override.example.com")
    assert client._client_instance._base_url == "https://override.example.com"


# ── Method generation ─────────────────────────────────────────────────────────


def test_list_pets_method_generated():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    assert callable(getattr(client, "list_pets", None))


def test_create_pet_method_generated():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    assert callable(getattr(client, "create_pet", None))


def test_get_pet_by_id_method_generated():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    assert callable(getattr(client, "get_pet_by_id", None))


def test_all_three_methods_present():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    for name in ("list_pets", "create_pet", "get_pet_by_id"):
        assert hasattr(client, name), f"Missing method: {name}"


# ── Method dispatch ───────────────────────────────────────────────────────────


async def test_list_pets_dispatches_get():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    with patch.object(
        client._client_instance, "_request", new=AsyncMock(return_value=[])
    ) as mock_req:
        await client.list_pets()
        mock_req.assert_called_once()
        assert mock_req.call_args.args[0] == "GET"
        assert mock_req.call_args.args[1] == "/pets"


async def test_create_pet_dispatches_post():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    with patch.object(
        client._client_instance, "_request", new=AsyncMock(return_value={})
    ) as mock_req:
        await client.create_pet(body={"name": "Fido"})
        assert mock_req.call_args.args[0] == "POST"
        assert mock_req.call_args.kwargs.get("body") == {"name": "Fido"}


async def test_get_pet_by_id_fills_path_param():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    with patch.object(
        client._client_instance, "_request", new=AsyncMock(return_value={})
    ) as mock_req:
        await client.get_pet_by_id(42)
        # The path /pets/{petId} should be filled to /pets/42
        called_path = mock_req.call_args.args[1]
        assert "42" in called_path
        assert "{" not in called_path


async def test_list_pets_passes_query_param():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    with patch.object(
        client._client_instance, "_request", new=AsyncMock(return_value=[])
    ) as mock_req:
        await client.list_pets(limit=10)
        query_params = mock_req.call_args.kwargs.get("query_params", {})
        assert query_params.get("limit") == "10"


# ── Pydantic model building ───────────────────────────────────────────────────


def test_pydantic_models_created_for_schemas():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    assert "Pet" in client._models
    assert "CreatePetRequest" in client._models


def test_pet_model_has_correct_fields():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    Pet = client._models["Pet"]
    # Should be a Pydantic model with id and name fields
    from pydantic import BaseModel

    assert issubclass(Pet, BaseModel)
    fields = Pet.model_fields
    assert "id" in fields
    assert "name" in fields


def test_create_pet_request_model_fields():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    Req = client._models["CreatePetRequest"]
    from pydantic import BaseModel

    assert issubclass(Req, BaseModel)
    assert "name" in Req.model_fields


# ── Context manager ───────────────────────────────────────────────────────────


async def test_openapi_client_context_manager():
    client = OpenAPIClient.from_dict(PETSTORE_SPEC, base_url="https://test.example.com")
    async with client as c:
        assert isinstance(c, OpenAPIClient)


# ── Edge: spec with no operationId ───────────────────────────────────────────


def test_method_name_derived_from_path_when_no_operation_id():
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "NoId", "version": "1.0"},
        "paths": {
            "/things": {
                "get": {
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    client = OpenAPIClient.from_dict(spec, base_url="https://test.example.com")
    # Method name derived from "get_/things" → "get_things"
    assert any("things" in name for name in dir(client) if not name.startswith("_"))


# ── Edge: spec with no paths ──────────────────────────────────────────────────


def test_empty_paths_builds_client():
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "Empty", "version": "1.0"},
        "paths": {},
    }
    client = OpenAPIClient.from_dict(spec, base_url="https://test.example.com")
    assert isinstance(client, OpenAPIClient)
