"""
Milestone H tests — OpenAPI code generator (openapi_gen.generate_client).

Tests cover:
- generate_client writes a file to the output path
- Output is valid Python (ast.parse passes)
- Output contains a GenericClient subclass
- Output contains method stubs for each operation
- Output contains Pydantic model definitions
- class_name override works
- base_url embedding works
- main() CLI entry point parses args and delegates to generate_client
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from varco_fastapi.client.openapi_gen import _to_pascal, _to_snake, generate_client

# ── Minimal spec ─────────────────────────────────────────────────────────────

SIMPLE_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Todo API", "version": "1.0"},
    "servers": [{"url": "https://todo.example.com"}],
    "paths": {
        "/todos": {
            "get": {
                "operationId": "listTodos",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Todo"},
                                }
                            }
                        }
                    }
                },
            },
            "post": {
                "operationId": "createTodo",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/CreateTodoRequest"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Todo"}
                            }
                        }
                    }
                },
            },
        },
        "/todos/{todoId}": {
            "get": {
                "operationId": "getTodo",
                "parameters": [
                    {"in": "path", "name": "todoId", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Todo"}
                            }
                        }
                    }
                },
            },
            "delete": {
                "operationId": "deleteTodo",
                "parameters": [
                    {"in": "path", "name": "todoId", "schema": {"type": "integer"}},
                ],
                "responses": {"204": {"description": "Deleted"}},
            },
        },
    },
    "components": {
        "schemas": {
            "Todo": {
                "type": "object",
                "required": ["id", "title"],
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "done": {"type": "boolean"},
                },
            },
            "CreateTodoRequest": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string"},
                },
            },
        }
    },
}


# ── _to_snake / _to_pascal helpers ────────────────────────────────────────────


def test_to_snake_camel():
    assert _to_snake("listTodos") == "list_todos"


def test_to_snake_path_slug():
    assert _to_snake("/todos/{todoId}") == "todos_todo_id"


def test_to_pascal_snake():
    assert _to_pascal("todo_api") == "TodoApi"


def test_to_pascal_camel():
    assert _to_pascal("TodoApi") == "TodoApi"


# ── generate_client: basic output ────────────────────────────────────────────


def test_generate_client_creates_file(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    assert out.exists()


def test_generate_client_returns_path_object(tmp_path: Path):
    result = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    assert isinstance(result, Path)


def test_generate_client_output_is_valid_python(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    # ast.parse raises SyntaxError if Python is invalid
    ast.parse(source)


def test_generate_client_contains_generic_client_import(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "GenericClient" in source


def test_generate_client_contains_class_definition(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "class" in source
    assert "GenericClient" in source


def test_generate_client_default_class_name_from_title(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    # Title "Todo API" → "TodoApiClient"
    assert "TodoApiClient" in source


def test_generate_client_custom_class_name(tmp_path: Path):
    out = generate_client(
        SIMPLE_SPEC, output_path=tmp_path / "client.py", class_name="MyTodoClient"
    )
    source = out.read_text()
    assert "MyTodoClient" in source


# ── Method stubs ──────────────────────────────────────────────────────────────


def test_generate_client_contains_list_todos_method(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "list_todos" in source


def test_generate_client_contains_create_todo_method(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "create_todo" in source


def test_generate_client_contains_get_todo_method(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "get_todo" in source


def test_generate_client_contains_delete_todo_method(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "delete_todo" in source


# ── Pydantic model generation ─────────────────────────────────────────────────


def test_generate_client_contains_todo_model(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "Todo" in source
    assert "BaseModel" in source


def test_generate_client_contains_create_todo_request_model(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "CreateTodoRequest" in source


# ── base_url embedding ────────────────────────────────────────────────────────


def test_generate_client_embeds_base_url(tmp_path: Path):
    out = generate_client(
        SIMPLE_SPEC,
        output_path=tmp_path / "client.py",
        base_url="https://custom.todo.api",
    )
    source = out.read_text()
    assert "custom.todo.api" in source


def test_generate_client_uses_servers_url_when_no_base_url(tmp_path: Path):
    out = generate_client(SIMPLE_SPEC, output_path=tmp_path / "client.py")
    source = out.read_text()
    assert "todo.example.com" in source


# ── from_file (JSON) ──────────────────────────────────────────────────────────


def test_generate_client_from_json_file(tmp_path: Path):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SIMPLE_SPEC))
    out = generate_client(spec_file, output_path=tmp_path / "client.py")
    assert out.exists()
    source = out.read_text()
    ast.parse(source)
    assert "list_todos" in source


def test_generate_client_from_str_path(tmp_path: Path):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SIMPLE_SPEC))
    out = generate_client(str(spec_file), output_path=tmp_path / "client.py")
    assert out.exists()


# ── CLI main() ────────────────────────────────────────────────────────────────


def test_main_help_exits_zero():
    from varco_fastapi.client.openapi_gen import main

    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["varco-gen", "--help"]):
            main()
    assert exc_info.value.code == 0


def test_main_generates_file(tmp_path: Path):
    from varco_fastapi.client.openapi_gen import main

    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SIMPLE_SPEC))
    out_file = tmp_path / "out.py"

    with patch("sys.argv", ["varco-gen", str(spec_file), "-o", str(out_file)]):
        main()

    assert out_file.exists()
    ast.parse(out_file.read_text())
