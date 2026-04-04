#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

if command -v lume-fake-epics-ioc >/dev/null 2>&1; then
    exec lume-fake-epics-ioc "$@"
fi

if command -v python >/dev/null 2>&1; then
    if python -c "import caproto, lume_visualizations.fake_epics_ioc" >/dev/null 2>&1; then
        exec python -m lume_visualizations.fake_epics_ioc "$@"
    fi
fi

if command -v conda >/dev/null 2>&1; then
    exec conda run -n va-dev-2 python -m lume_visualizations.fake_epics_ioc "$@"
fi

echo "Unable to find a Python environment with caproto and lume_visualizations available." >&2
echo "Activate va-dev-2 and rerun ./start-fake-epics-ioc.sh, or install the package with pip install -e ." >&2
exit 1