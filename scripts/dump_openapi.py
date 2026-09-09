"""Dump the API's OpenAPI schema to openapi.json at the repo root.

The committed schema is the artifact every consumer reads. The contract test
(`tests/test_api_contract.py`) checks it here, and UI repos fetch it at a pinned ref to
generate their own client types. Regenerate and commit it whenever a route or
lume_model_api/api/schemas.py changes, or CI will fail the drift check.

Importing the app is cheap. No torch, pytao, Bmad lattice or EPICS connection is needed,
because the model is only built inside ModelPool worker subprocesses and app.openapi()
never starts them.
"""

from __future__ import annotations

import json
from pathlib import Path

from lume_model_api.api.main import app

OUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    # sort_keys plus a trailing newline keep the diff stable, so the CI drift check only
    # fails on real schema changes rather than on dict ordering.
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
