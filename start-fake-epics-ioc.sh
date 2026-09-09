#!/usr/bin/env bash
# Serve the staged-model input PVs with synthetic motion, so the live stream can be exercised
# without the real machine. Prefers the console script, falls back to the module.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

# Renamed from lume-fake-epics-ioc so this package can be installed alongside the older
# lume-visualizations, which still ships a script under the old name.
if command -v lume-model-api-fake-ioc >/dev/null 2>&1; then
    exec lume-model-api-fake-ioc "$@"
fi

if command -v python >/dev/null 2>&1; then
    if python -c "import caproto, lume_model_api.model.fake_epics_ioc" >/dev/null 2>&1; then
        exec python -m lume_model_api.model.fake_epics_ioc "$@"
    fi
fi

echo "No Python environment on PATH has caproto and lume_model_api available." >&2
echo "Install the package first: pip install -e ." >&2
exit 1
