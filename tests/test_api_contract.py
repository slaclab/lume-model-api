"""Guard the /api/v1 contract.

`/api/v1/*` is the ONE evaluate contract: any UI, and every programmatic client such as
notebooks and emittance GUIs, calls it. Every model-specific route carries the model's URL
name, so the paths here are the templated ones a generated client sees.

The CI drift check only proves that openapi.json was regenerated, so it passes happily when a
field is renamed. These expectations are written out by hand so a rename or removal fails
loudly instead.

Field names only, deliberately. Asserting JSON types too would make this a third copy of
lume_model_api/api/schemas.py for very little extra protection.

Reads app.openapi() directly rather than the committed snapshot, so it needs no TestClient, no
lifespan and no model. Nothing here loads torch, pytao or EPICS.
"""

from __future__ import annotations

import pytest

from lume_model_api.api.main import app

BREAKING = (
    "\n\n/api/v1/* is the EXTERNAL contract. UIs, notebooks and GUIs in other repos depend "
    "on it.\nAdding a new optional field is fine: update the expectation in this test.\n"
    "Renaming or removing a field, or making an optional field required, breaks those "
    "callers.\nAdd /api/v2 instead of changing v1 in place.\n\n"
    "The v1 paths moved once more, to /api/v1/models/{name}/..., while there is still no "
    "deployed\nconsumer and the UI port is in progress (see "
    "docs/MIGRATING_LUME_VISUALIZATIONS.md). From that\nchange onward the contract is "
    "additive only, paths included."
)

# schema name -> (required field names, optional field names)
#
# The output kinds are a discriminated union on `kind`, so `kind` is required on every member:
# a response model cannot pick a member without it.
V1_SCHEMAS: dict[str, tuple[set[str], set[str]]] = {
    "EvaluateV1Request": (
        set(),
        {"inputs", "outputs", "screen", "max_particles", "smooth_images_sigma_px"},
    ),
    "EvaluateV1Response": (
        {"model", "version", "timestamp", "frame_index", "inputs", "outputs"},
        # `input_sources` says whether each value in `inputs` was read off the machine, sent by
        # the caller, or filled in from the model's design values. Optional, so a client that
        # predates it is unaffected, and `inputs` itself was left untouched.
        {"input_sources"},
    ),
    # `variable_class` is the lume class name beside the coarser `kind`, on every output kind and
    # on OutputInfo below, so the config route and an evaluate response describe an id the same
    # way. Optional, so a client that predates it is unaffected and `kind` stays the thing to
    # switch on.
    "ScalarOutput": ({"kind", "value"}, {"unit", "variable_class"}),
    "ArrayOutput": ({"kind", "shape", "data_b64"}, {"dtype", "unit", "variable_class"}),
    "ParticlesOutput": (
        {"kind", "n", "units", "coords", "stats", "stats_units"},
        {"variable_class"},
    ),
    "ValueOutput": ({"kind"}, {"value", "variable_class"}),
    "ModelListEntry": ({"name", "version"}, {"description"}),
    "ConfigResponse": ({"model", "version", "inputs", "outputs", "screens"}, {"description"}),
    # `alias_of` is on both: a model may publish two writable handles on one control, in which
    # case only one stays settable and the others become read-only outputs naming it. Always null
    # on a published input, so the field means the same thing wherever a client meets it.
    "InputInfo": (
        {"id", "default", "min", "max", "range_source"},
        {"unit", "constant", "alias_of"},
    ),
    "OutputInfo": (
        {"id", "kind"},
        {"unit", "shape", "element_name", "variable_class", "alias_of"},
    ),
    "ScreenInfo": ({"key", "particles"}, {"image"}),
    "SnapshotResponse": ({"inputs"}, {"sources"}),
}

# The literal templated paths, exactly as they appear in openapi.json, because that is what a
# generated client turns into a method. `{name}` is the model's URL name, which a client reads
# from GET /api/v1/models rather than hard-coding.
ROUTES = (
    "/api/v1/models",
    "/api/v1/models/{name}/config",
    "/api/v1/models/{name}/evaluate",
    "/api/v1/models/{name}/live/stream",
    "/api/v1/models/{name}/machine-snapshot",
    "/metrics",
)

NAMED_ROUTES = tuple(path for path in ROUTES if "{name}" in path)

SCHEMA = app.openapi()


def test_the_schema_carries_no_servers_entry() -> None:
    """The committed contract must be deployment-neutral.

    `LUME_ROOT_PATH` is set in the cluster so Swagger works under the ingress prefix, and a set
    root_path makes FastAPI emit a `servers` entry. A client that generated from a schema
    carrying one would prefix every request with whichever deployment produced it.
    """
    assert "servers" not in SCHEMA, (
        "openapi.json declares a `servers` entry, so it was generated with LUME_ROOT_PATH set. "
        "Regenerate with scripts/dump_openapi.py, which unsets it." + BREAKING
    )


def test_v1_evaluate_route_exists() -> None:
    assert "post" in SCHEMA["paths"].get("/api/v1/models/{name}/evaluate", {}), (
        "POST /api/v1/models/{name}/evaluate is gone." + BREAKING
    )


def test_the_model_list_route_exists() -> None:
    """The route a UI reads to populate a model picker, and the k8s probe target."""
    assert "get" in SCHEMA["paths"].get("/api/v1/models", {}), (
        "GET /api/v1/models is gone, so a client has no way to discover the hosted models."
        + BREAKING
    )


@pytest.mark.parametrize("path", ROUTES)
def test_route_exists(path: str) -> None:
    assert path in SCHEMA["paths"], f"{path} is gone." + BREAKING


@pytest.mark.parametrize("path", NAMED_ROUTES)
def test_named_route_declares_the_name_path_parameter(path: str) -> None:
    """Without the declared parameter a generated client cannot fill in the model name."""
    for method, operation in SCHEMA["paths"][path].items():
        found = {
            item["name"]
            for item in operation.get("parameters", [])
            if item.get("in") == "path" and item.get("required")
        }
        assert "name" in found, (
            f"{method.upper()} {path} does not declare a required `name` path parameter, "
            f"only {sorted(found)}." + BREAKING
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


def test_evaluate_response_outputs_is_a_discriminated_union() -> None:
    """`kind` is how a client narrows an output without guessing from the keys present."""
    outputs = SCHEMA["components"]["schemas"]["EvaluateV1Response"]["properties"]["outputs"]
    union = outputs["additionalProperties"]
    assert union.get("discriminator", {}).get("propertyName") == "kind", (
        "outputs lost its `kind` discriminator, so generated clients can no longer narrow "
        "an output to one shape." + BREAKING
    )
    members = {ref.rsplit("/", 1)[-1] for ref in union["discriminator"]["mapping"].values()}
    assert members == {"ScalarOutput", "ArrayOutput", "ParticlesOutput", "ValueOutput"}, (
        f"the output kinds changed: {sorted(members)}" + BREAKING
    )
