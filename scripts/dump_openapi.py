"""Dump the backend OpenAPI schema to webapp/openapi.json.

The committed schema is the single artifact that both the frontend type generator
(`npm run gen:api`) and the external contract test (`tests/test_api_contract.py`) read.
Regenerate and commit it whenever a route or webapp/backend/schemas.py changes, or CI
will fail the drift check.

Importing the app is cheap. No torch, pytao, Bmad lattice or EPICS connection is needed,
because the model is only built inside ModelPool worker subprocesses and app.openapi()
never starts them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # `webapp` is not an installed package, only importable from the repo root

from webapp.backend.main import app  # noqa: E402

OUT = ROOT / "webapp" / "openapi.json"


def main() -> None:
    # sort_keys plus a trailing newline keep the diff stable, so the CI drift check only
    # fails on real schema changes rather than on dict ordering.
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
