#!/usr/bin/env bash
# Create a fresh, pinned conda env for running this API against the REAL cu_hxr_staged
# model. Mirrors the commit pins in ai-lab/Dockerfile so the injector-surrogate data
# subtree (bundled at virtual-accelerator@77bbda8) is present.
#
# Usage:  bash scripts/setup-dev-env.sh
# Then:   conda run -n "$ENV_NAME" env LCLS_LATTICE=... uvicorn lume_model_api.api.main:app
set -euo pipefail

ENV_NAME="${ENV_NAME:-lume-webapp}"
VA_REF="${VA_REF:-77bbda8}"
LUME_BMAD_REF="e49c6891978ae2d0c09229307ebd2f3a4aa4887f"
LUME_TORCH_REF="acd21eb1f66a525078db7baac21c99d973d47b94"
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

echo ">> Installing virtual-accelerator[surrogate] (editable)"
run pip install -e "$VA_DIR[surrogate]"

# The [surrogate] extra pulls an incompatible lume-cheetah (0.1.0, missing
# `.transformer`); pin the git build that virtual-accelerator@$VA_REF expects.
echo ">> Pinning lume-cheetah (git build with .transformer)"
run pip install --force-reinstall --no-deps \
  "lume-cheetah @ git+https://github.com/lume-science/lume-cheetah@148d598c6"

echo ">> Pinning lume-bmad / lume-torch"
run pip install --force-reinstall --no-deps \
  "lume-bmad @ git+https://github.com/lume-science/lume-bmad.git@${LUME_BMAD_REF}" \
  "lume-torch @ git+https://github.com/lume-science/lume-torch@${LUME_TORCH_REF}"

echo ">> Installing lume-model-api (editable, with the EPICS extra)"
# pyproject.toml is the single source of truth for the pip-installable deps, so this pulls
# fastapi, uvicorn, sse-starlette, pydantic, prometheus-client, numpy, scipy and caproto.
run pip install -e "$REPO_ROOT[epics]"

cat <<EOF

Done. Run the REAL backend with:

  conda run -n $ENV_NAME env \\
    LCLS_LATTICE=$LATTICE_DIR KMP_DUPLICATE_LIB_OK=TRUE \\
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \\
    python -m uvicorn lume_model_api.api.main:app --host 0.0.0.0 --port 8000

(omit LUME_MOCK; set LUME_MOCK=1 for the dependency-free synthetic backend.)
EOF
