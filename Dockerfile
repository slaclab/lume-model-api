# API-only image that hosts a LUMEModel over HTTP.
# Build context = repo root:  docker build -t lume-model-api .
#
# This image ships no UI. A UI repo builds its own image `FROM` this one and copies its
# built assets into /app/lume_model_api/static/, which main.py serves at "/" when present.
# Nothing here needs Node.
ARG PYTHON_VERSION=3.12
ARG LCLS_LATTICE_REF=10ec2d2faeb6228640979e51683bbedebc78c62b
ARG FACET_LATTICE_REF=bd628b7c00b3c405dae7ebd85e7c5b1833141e7c
ARG DOCKER_PLATFORM=linux/amd64

# --- Python runtime with Bmad: hosts a LUMEModel ---
FROM --platform=${DOCKER_PLATFORM} python:${PYTHON_VERSION}-slim AS runtime
ARG PYTHON_VERSION
ARG LCLS_LATTICE_REF
ARG FACET_LATTICE_REF

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
    && conda install -y "python=${PYTHON_VERSION}" pip bmad pytao \
    && patchelf --clear-execstack /opt/conda/lib/libtao.so \
    && conda clean -afy

WORKDIR /app

RUN git clone https://github.com/slaclab/lcls-lattice.git /opt/lcls-lattice \
    && cd /opt/lcls-lattice && git checkout ${LCLS_LATTICE_REF}
RUN git clone https://github.com/slaclab/facet2-lattice.git /opt/facet-lattice \
    && cd /opt/facet-lattice && git checkout ${FACET_LATTICE_REF}

# CPU torch first, so the bare `torch` in the [va] extra is already satisfied and pip never
# pulls the much larger CUDA wheel.
RUN python -m pip install --upgrade setuptools wheel \
    && python -m pip install --upgrade --index-url https://download.pytorch.org/whl/cpu torch

# Every pip pin lives in pyproject.toml. The requirements are read out of it here, before the
# app code is copied, so this heavy layer is rebuilt only when pyproject.toml changes.
#
# The lume-bmad git ref there is load-bearing. VA declares a bare `lume-bmad` requirement, and
# the PyPI release v0.1.0 constructs every `<ele>_beam` variable without `read_only=True`.
# lume-base defaults `Variable.read_only` to False and does not set pydantic's
# `validate_default`, so `ReadOnlyActionMixin`'s guard never fires, `introspect.describe` drops
# the beams as writable non-scalars, and the API publishes `screens: []`. lume-bmad fixed this
# in 110230c9 (2026-08-25) and has not tagged a release since.
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; x = p['optional-dependencies']; print('\n'.join(p['dependencies'] + x['va'] + x['epics']))" > /tmp/requirements.txt \
    && python -m pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Fail the build, rather than the pod, if the resolved lume-bmad predates the beam fix. A pod
# built on the older release starts healthy and serves every scalar and image, so the only
# symptom is an empty `screens` list on a route nothing probes. The source check is deliberate:
# the flag is set at the call site, so no importable constant records whether this build has it.
RUN python -c "import inspect, lume_bmad.model as m; src = inspect.getsource(m.LUMEBmadModel._refresh_dynamic_action_variables); assert 'read_only=True' in src, 'lume-bmad predates 110230c9, so every <ele>_beam variable would be dropped from the published outputs'" \
    && python -m pip freeze | grep -iE 'lume|virtual-accelerator'

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
