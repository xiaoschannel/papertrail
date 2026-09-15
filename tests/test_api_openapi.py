"""OpenAPI contract: every JSON endpoint has a typed response, and the committed snapshot the
frontend's TypeScript types are generated from is current."""

import json
import sys
from pathlib import Path

from api.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import export_openapi  # noqa: E402


REGENERATE = (
    "run `python tools/export_openapi.py` and `npm --prefix frontend run gen:api`. "
    "If no API code changed, CI probably installed a newer fastapi/pydantic than you have "
    "(requirements are unpinned): upgrade them locally first"
)


def test_openapi_snapshot_is_current():
    assert export_openapi.SNAPSHOT.is_file(), f"frontend/openapi.json is missing: {REGENERATE}"
    committed = json.loads(export_openapi.SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == create_app().openapi(), f"frontend/openapi.json is stale: {REGENERATE}"


def loose_nodes(schema, where="response"):
    """Places in a response schema that would generate an untyped TS value: an empty schema,
    or an object with neither declared properties nor a typed value schema (e.g. a bare
    ``-> dict`` return annotation). ``$ref``s point at response models and are fine."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        return []
    meaningful = {k: v for k, v in schema.items() if k not in ("title", "description")}
    if not meaningful:
        return [where]
    found = []
    if schema.get("type") == "object" and "properties" not in schema:
        extra = schema.get("additionalProperties")
        if not isinstance(extra, dict):
            found.append(where)
        else:
            found += loose_nodes(extra, f"{where}{{*}}")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key, [])):
            found += loose_nodes(sub, f"{where}.{key}[{i}]")
    if "items" in schema:
        found += loose_nodes(schema["items"], f"{where}[]")
    return found


def test_every_json_endpoint_has_a_typed_response():
    spec = create_app().openapi()
    untyped = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            content = op["responses"].get("200", {}).get("content", {})
            if "application/json" in content:
                untyped += [f"{method.upper()} {path}: {w}"
                            for w in loose_nodes(content["application/json"].get("schema", {}))]
    assert not untyped, f"JSON responses without a response model: {untyped}"


def test_loose_nodes_catches_untyped_responses():
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/bare-dict")
    def bare_dict() -> dict:
        return {}

    @app.get("/list-of-dicts")
    def list_of_dicts() -> list[dict]:
        return []

    @app.get("/unannotated")
    def unannotated():
        return {}

    @app.get("/typed-map")
    def typed_map() -> dict[str, list[int]]:
        return {}

    paths = app.openapi()["paths"]
    schema = lambda p: paths[p]["get"]["responses"]["200"]["content"]["application/json"]["schema"]  # noqa: E731
    assert loose_nodes(schema("/bare-dict"))
    assert loose_nodes(schema("/list-of-dicts"))
    assert loose_nodes(schema("/unannotated"))
    assert not loose_nodes(schema("/typed-map"))
