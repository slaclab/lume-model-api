# Build context = repo root:  docker build -f webapp/Dockerfile -t lume-monitor .
ARG PYTHON_VERSION=3.12
ARG LCLS_LATTICE_REF=52ad1a5ddd00aa57a89a4fc7f2fa1a2363216ae8
ARG VA_REF=77bbda8
ARG DOCKER_PLATFORM=linux/amd64

# --- Stage 1: build the React frontend ---
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci
COPY webapp/frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime with Bmad + the cu_hxr_staged model ---
FROM --platform=${DOCKER_PLATFORM} python:${PYTHON_VERSION}-slim AS runtime
ARG PYTHON_VERSION
ARG LCLS_LATTICE_REF
ARG VA_REF

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/conda/bin:$PATH \
    PYTHONPATH=/app \
    LCLS_LATTICE=/opt/lcls-lattice \
    KMP_DUPLICATE_LIB_OK=TRUE \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    TORCH_NUM_THREADS=2

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

# virtual-accelerator @ pinned ref (bundles the injector-surrogate subtree),
# then the pinned lume-bmad / lume-torch commits. No FEL surrogate (out of scope).
RUN python -m pip install --upgrade setuptools wheel \
    && python -m pip install --upgrade --index-url https://download.pytorch.org/whl/cpu torch \
    && git clone https://github.com/slaclab/virtual-accelerator.git /opt/virtual-accelerator \
    && cd /opt/virtual-accelerator && git checkout ${VA_REF} \
    && python -m pip install -e ".[surrogate]" \
    && cd /app \
    && python -m pip install --force-reinstall --no-deps \
        "lume-bmad @ git+https://github.com/lume-science/lume-bmad.git@e49c6891978ae2d0c09229307ebd2f3a4aa4887f" \
        "lume-torch @ git+https://github.com/lume-science/lume-torch@acd21eb1f66a525078db7baac21c99d973d47b94" \
    && python -m pip install fastapi "uvicorn[standard]" sse-starlette pydantic numpy scipy pyepics caproto

# The [surrogate] extra pulls an incompatible lume-cheetah (0.1.0, missing
# `.transformer`); pin the git build that virtual-accelerator@${VA_REF} expects.
RUN python -m pip install --force-reinstall --no-deps \
    "lume-cheetah @ git+https://github.com/lume-science/lume-cheetah@148d598c6"

# App code: the lume_visualizations model layer + the webapp package (import via PYTHONPATH=/app)
COPY lume_visualizations/ ./lume_visualizations/
COPY webapp/backend/ ./webapp/backend/
COPY webapp/__init__.py ./webapp/__init__.py
# Built frontend served by the backend at "/"
COPY --from=frontend-build /app/frontend/dist ./webapp/backend/static/

EXPOSE 8000
CMD ["uvicorn", "webapp.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
