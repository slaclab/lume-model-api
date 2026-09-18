#!/usr/bin/env bash
# Create a fresh, pinned conda env for running this API against any LUMEModel.
# Mirrors the pins in Dockerfile, including the lume stack. Keep the two in step: an env built
# from VA's own bare requirements gets a lume-bmad that drops every beam variable, so a model
# would introspect with no screens here while the image serves them.
#
# Usage:  bash scripts/setup-dev-env.sh
# Then:   see the printed instructions below for run commands
set -euo pipefail

ENV_NAME="${ENV_NAME:-lume-webapp}"
VA_REF="${VA_REF:-043a2f0fca3a8c7e1f837aa226a42a167a78f9fb}"
LUME_BMAD_REF="${LUME_BMAD_REF:-8f3ed201d546878441e06aced506fd4411c42492}"
LUME_BASE_VERSION="${LUME_BASE_VERSION:-0.6.0}"
LUME_TORCH_VERSION="${LUME_TORCH_VERSION:-3.0.0}"
LUME_CHEETAH_VERSION="${LUME_CHEETAH_VERSION:-0.1.0}"
VA_DIR="${VA_DIR:-$HOME/SLAC/virtual-accelerator-pinned}"
LATTICE_DIR="${LCLS_LATTICE:-$HOME/SLAC/lcls-lattice}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ">> Creating conda env: $ENV_NAME (python 3.12)"
conda create -y -n "$ENV_NAME" python=3.12

echo ">> Installing Bmad + pytao (conda-forge)"
conda install -y -n "$ENV_NAME" -c conda-forge bmad pytao

run() { conda run -n "$ENV_NAME" "$@"; }

echo ">> Installing CPU torch"
run pip install --index-url https://download.pytorch.org/whl/cpu torch

echo ">> Cloning virtual-accelerator @ $VA_REF (has the injector subtree)"
if [ ! -d "$VA_DIR/.git" ]; then
  git clone https://github.com/slaclab/virtual-accelerator.git "$VA_DIR"
fi
git -C "$VA_DIR" fetch --all --tags
git -C "$VA_DIR" checkout "$VA_REF"

echo ">> Installing the pinned lume stack"
# Before VA, so its bare lume-* requirements are already satisfied and pip never fetches the
# lume-bmad release that constructs beam variables without read_only=True. The Dockerfile
# explains the failure that causes in full.
run pip install \
  "lume-base==$LUME_BASE_VERSION" \
  "lume-torch==$LUME_TORCH_VERSION" \
  "lume-cheetah==$LUME_CHEETAH_VERSION" \
  "lume-bmad @ git+https://github.com/lume-science/lume-bmad@$LUME_BMAD_REF"

echo ">> Installing virtual-accelerator[surrogate,bmad] (editable)"
run pip install -e "$VA_DIR[surrogate,bmad]"

echo ">> Checking the resolved lume-bmad carries the beam read_only fix"
run python -c "import inspect, lume_bmad.model as m; src = inspect.getsource(m.LUMEBmadModel._refresh_dynamic_action_variables); assert 'read_only=True' in src, 'lume-bmad predates 110230c9, so every <ele>_beam variable would be dropped from the published outputs'"

echo ">> Installing lume-model-api (editable, with the EPICS extra)"
# pyproject.toml is the single source of truth for the pip-installable deps, so this pulls
# fastapi, uvicorn, sse-starlette, pydantic, prometheus-client, numpy and scipy.
run pip install -e "$REPO_ROOT[epics]"

cat <<EOF

Done. Run the cu_hxr_staged model (needs LCLS_LATTICE):

  conda run -n $ENV_NAME env \\
    LUME_MODELS=cu_hxr_staged LCLS_LATTICE=$LATTICE_DIR \\
    KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \\
    OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \\
    python -m uvicorn lume_model_api.api.main:app --host 0.0.0.0 --port 8000

Then GET http://localhost:8000/api/v1/models to see what is hosted, and drive it at
/api/v1/models/cu_hxr_staged/config and /api/v1/models/cu_hxr_staged/evaluate.

Dependency-free mode (no torch, no pytao, no EPICS needed):

  LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic python -m uvicorn lume_model_api.api.main:app

Any other factory, named for the URL it should answer on (no code changes required):

  LUME_MODELS=myname=module.path:factory_function \\
    python -m uvicorn lume_model_api.api.main:app

Several models in one process, with per-model kwargs and workers:

  LUME_MODELS='{"demo": {"workers": 1}, \\
                "cu_hxr_staged": {"kwargs": {"n_particles": 1000}, "workers": 2}}' \\
    python -m uvicorn lume_model_api.api.main:app

Memory scales with the sum of workers across models, roughly 2 GB per real model worker.
EOF
