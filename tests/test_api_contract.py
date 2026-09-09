"""Guard the external /api/v1 contract.

`/api/v1/*` is the programmatic surface that notebooks, emittance GUIs and any other HTTP
client call. The CI drift check only proves that webapp/openapi.json was regenerated, so it
passes happily when a field is renamed. These expectations are written out by hand so a
rename or removal fails loudly instead.

Field names only, deliberately. Asserting JSON types too would make this a third copy of
webapp/backend/schemas.py for very little extra protection.

Reads app.openapi() directly rather than the committed snapshot, so it needs no TestClient,
no lifespan and no model. Nothing here loads torch, pytao or EPICS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `webapp` is importable only from the repo root

from webapp.backend.main import app  # noqa: E402

BREAKING = (
    "\n\n/api/v1/* is the EXTERNAL contract. Notebooks and GUIs outside this repo depend "
    "on it.\nAdding a new optional field is fine: update the expectation in this test.\n"
    "Renaming or removing a field, or making an optional field required, breaks those "
    "callers.\nAdd /api/v2 instead of changing v1 in place."
)

# schema name -> (required field names, optional field names)
V1_SCHEMAS: dict[str, tuple[set[str], set[str]]] = {
    "EvaluateV1Request": (
        {"screen"},
        {"inputs", "include_image", "include_distribution", "include_twiss", "max_particles"},
    ),
    "EvaluateV1Response": (
        {"model", "version", "screen", "frame_index", "timestamp", "scalars"},
        {"image", "distribution", "twiss"},
    ),
    "V1Image": ({"shape", "data_b64"}, {"dtype"}),
    "V1Distribution": ({"n", "units", "coords"}, set()),
    "V1Twiss": ({"s", "beta_x", "beta_y"}, set()),
}

SCHEMA = app.openapi()


def test_v1_evaluate_route_exists() -> None:
    assert "post" in SCHEMA["paths"].get("/api/v1/evaluate", {}), (
        "POST /api/v1/evaluate is gone." + BREAKING
    )


@pytest.mark.parametrize("name", sorted(V1_SCHEMAS))
def test_v1_schema_fields(name: str) -> None:
    expected_required, expected_optional = V1_SCHEMAS[name]
    schemas = SCHEMA["components"]["schemas"]
    assert name in schemas, f"schema {name} is gone." + BREAKING

    required = set(schemas[name].get("required", []))
    optional = set(schemas[name]["properties"]) - required

    assert required == expected_required, (
        f"{name} required fields changed."
        f"\n  added:   {sorted(required - expected_required)}"
        f"\n  removed: {sorted(expected_required - required)}" + BREAKING
    )
    assert optional == expected_optional, (
        f"{name} optional fields changed."
        f"\n  added:   {sorted(optional - expected_optional)}"
        f"\n  removed: {sorted(expected_optional - optional)}" + BREAKING
    )
