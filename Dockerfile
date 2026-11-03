# API-only image that hosts a LUMEModel over HTTP.
# Build context = repo root:  docker build -t lume-model-api .
#
# This image ships no UI. A UI repo builds its own image `FROM` this one and copies its
# built assets into /app/lume_model_api/static/, which main.py serves at "/" when present.
# Nothing here needs Node.
ARG PYTHON_VERSION=3.12
ARG LCLS_LATTICE_REF=48c907d0c1b4ade6db351a4d5872a4680ae6523e
ARG FACET_LATTICE_REF=bd628b7c00b3c405dae7ebd85e7c5b1833141e7c
# bmad and pytao from conda-forge. Last tested together on 2026-11-03 against the va extra.
ARG BMAD_VERSION=20261023.0
ARG PYTAO_VERSION=1.3.3
ARG DOCKER_PLATFORM=linux/amd64

# --- Python runtime with Bmad: hosts a LUMEModel ---
FROM --platform=${DOCKER_PLATFORM} python:${PYTHON_VERSION}-slim AS runtime
ARG PYTHON_VERSION
ARG LCLS_LATTICE_REF
ARG FACET_LATTICE_REF
ARG BMAD_VERSION
ARG PYTAO_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/conda/bin:$PATH \
    LCLS_LATTICE=/opt/lcls-lattice \
    FACET2_LATTICE=/opt/facet-lattice \
    KMP_DUPLICATE_LIB_OK=TRUE \
    HDF5_USE_FILE_LOCKING=FALSE \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    TORCH_NUM_THREADS=2 \
    LUME_POOL_WORKERS=2 \
    LUME_WORKER_THREADS=2

# Models the image serves, keyed by URL name at /api/v1/models/<name>/... . The comma form is
# used here because it needs no quoting inside ENV; k8s deployment.yaml overrides this per
# workload with the JSON object form, which is what carries per-model kwargs and workers.
ENV LUME_MODELS=cu_hxr_staged

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash bzip2 curl git patchelf \
    && rm -rf /var/lib/apt/lists/*

# miniforge + Bmad/pytao (pytao needs the libtao shared library from conda-forge)
RUN arch="$(dpkg --print-architecture)" \
    && case "${arch}" in \
        amd64) conda_arch="x86_64" ;; \
        arm64) conda_arch="aarch64" ;; \
        *) echo "Unsupported arch: ${arch}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-${conda_arch}.sh" -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm -f /tmp/miniforge.sh \
    && conda config --system --add channels conda-forge \
    && conda config --system --set channel_priority strict \
    && conda install -y "python=${PYTHON_VERSION}" pip "bmad=${BMAD_VERSION}" "pytao=${PYTAO_VERSION}" \
    && patchelf --clear-execstack /opt/conda/lib/libtao.so \
    && conda clean -afy

WORKDIR /app

RUN git clone https://github.com/slaclab/lcls-lattice.git /opt/lcls-lattice \
    && cd /opt/lcls-lattice && git checkout ${LCLS_LATTICE_REF}
RUN git clone https://github.com/slaclab/facet2-lattice.git /opt/facet-lattice \
    && cd /opt/facet-lattice && git checkout ${FACET_LATTICE_REF}

# CPU torch first, so the `torch==` pin in the [va] extra is already satisfied and pip never
# pulls the much larger CUDA wheel. The two versions must match.
RUN python -m pip install --upgrade setuptools wheel \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0

# Every pip pin lives in pyproject.toml. The requirements are read out of it here, before the
# app code is copied, so this heavy layer is rebuilt only when pyproject.toml changes.
# fastapi and pydantic are pinned on the command line to the versions CI tests and generates
# openapi.json with (.github/workflows/contract.yml). Keep the two in step.
#
# virtual-accelerator declares a bare `lume-bmad` requirement, and the PyPI release v0.1.0
# constructs every `<ele>_beam` variable without `read_only=True`. lume-base defaults
# `Variable.read_only` to False and does not set pydantic's `validate_default`, so
# `ReadOnlyActionMixin`'s guard never fires, `introspect.describe` drops the beams as writable
# non-scalars, and the API publishes `screens: []`. The fix is in lume-bmad 0.1.1 and later.
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; x = p['optional-dependencies']; print('\n'.join(p['dependencies'] + x['va'] + x['epics']))" > /tmp/requirements.txt \
    && python -m pip install -r /tmp/requirements.txt fastapi==0.141.1 pydantic==2.13.4 \
    && rm /tmp/requirements.txt

# Fail the build, rather than the pod, if the resolved lume-bmad predates the beam fix. A pod
# built on the older release starts healthy and serves every scalar and image, so the only
# symptom is an empty `screens` list on a route nothing probes. The source check is deliberate:
# the flag is set at the call site, so no importable constant records whether this build has it.
RUN python -c "import inspect, lume_bmad.model as m; src = inspect.getsource(m.LUMEBmadModel._refresh_dynamic_action_variables); assert 'read_only=True' in src, 'lume-bmad predates 0.1.1, so every <ele>_beam variable would be dropped from the published outputs'" \
    && python -m pip freeze | grep -iE '^(lume|virtual-accelerator|lcls-cu-inj|facet2.inj|distgen|torch=)'

# App code, then the install. This order is load-bearing and was verified by reversing it:
# `pip install -e .` with the package directory absent finds no packages, reports
# "Successfully installed lume-model-api-0.1.0" with exit 0, and then every import fails with
# ModuleNotFoundError even after the code is copied in. pyproject.toml declares
# readme = "README.md", so the build also hard-fails without the README.
COPY pyproject.toml README.md ./
COPY lume_model_api/ ./lume_model_api/

RUN python -m pip install -e ".[va,epics]"

EXPOSE 8000
# TLS terminates on a load balancer in front of the cluster and the pod sees plain HTTP, so
# trust the X-Forwarded-* headers the ingress controller adds. uvicorn otherwise trusts them
# from 127.0.0.1 only and would build http:// URLs in redirects. The pod is reachable only
# through the ingress, which is what makes '*' acceptable here.
CMD ["uvicorn", "lume_model_api.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
