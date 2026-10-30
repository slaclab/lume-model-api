# Deploying

The image serves the API only and hosts whatever `LUME_MODELS` selects. The manifests in
`deploy/kubernetes/` run it as two Deployments: `lume-model-api-eval`, a stateless eval pool
that can run any number of replicas, and `lume-model-api-live`, a single pod that reads EPICS
and serves the live stream. [ARCHITECTURE.md](ARCHITECTURE.md#roles) covers why the work is
split this way.

Scaling is done by hand. There's no autoscaler in the cluster.

## Build the image

Build from the repo root, for `linux/amd64`:

```bash
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t ghcr.io/slaclab/lume-model-api:<tag> --load .
docker image inspect ghcr.io/slaclab/lume-model-api:<tag> --format '{{.Os}}/{{.Architecture}}'
```

The second command should print `linux/amd64`. A plain `docker build` on an arm64 Mac produces
an image that Kubernetes rejects with "no match for platform", and that only shows up when the
pod is scheduled.

The image contains:

- miniforge, with `bmad` and `pytao` from conda-forge (and `libtao.so` patched with
  `patchelf --clear-execstack`, which pytao needs)
- the CPU torch wheel, installed first so pip never pulls the CUDA wheel
- the LCLS and FACET2 lattices, cloned at `LCLS_LATTICE_REF` and `FACET_LATTICE_REF`
- this package with the `[va,epics]` extras

The build args are `PYTHON_VERSION`, `LCLS_LATTICE_REF`, `FACET_LATTICE_REF` and
`DOCKER_PLATFORM`. Pip versions aren't build args. They live in the `va` extra in
`pyproject.toml`, which the README's local install uses too, so the image and a workstation
get the same stack.

### Pinned dependencies

virtual-accelerator declares bare `lume-base`, `lume-bmad`, `lume-torch` and `lume-cheetah`
requirements, so the `va` extra pins each one. Without the pins, a rebuild installs whatever
PyPI has that day. lume-bmad `0.1.0` builds every `<ele>_beam` variable without
`read_only=True`, so the service drops the beams as writable non-scalars and publishes
`screens: []`. The fix landed in lume-bmad `0.1.1` (on PyPI since 2026-09-24). The `va` extra
still pins lume-bmad to an older git commit that has the fix, and can move to `lume-bmad==0.1.1`
with the next pin update. See
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#output_beam-and-writable-non-scalars-are-not-published).

After installing the pins, the Dockerfile checks the source of
`LUMEBmadModel._refresh_dynamic_action_variables` for `read_only=True` and fails the build if
it's missing. It then prints the resolved lume and virtual-accelerator versions. A pod built
without the fix would start healthy and serve every scalar and image, and the only symptom
would be an empty `screens` list.

`conda install bmad pytao` isn't pinned, so a fresh solve can pull a Bmad that rejects a
model's `tao.init`. If that happens, pin the versions from the last build that worked. The last
known-good pair is bmad `20261023.0` with pytao `1.3.3` (checked 2026-10-29 against
virtual-accelerator and lume-bmad `main`).

### Packaging checks

Two packaging mistakes produce a build that looks successful.

`pip install -e .` exits 0 when the package directory is missing. It prints
`Successfully installed lume-model-api-0.1.0`, finds no packages, and every import then fails
with `ModuleNotFoundError`. That's why the Dockerfile copies `lume_model_api/` before running
the install. `pyproject.toml` also declares `readme = "README.md"` and the Dockerfile copies
it, so the build fails if the README is removed.

`uvicorn` always puts the current directory on `sys.path`. Its CLI defaults `--app-dir` to
`""`, so starting it from the repo root imports the source tree whether or not the package is
installed (in the container, `WORKDIR /app` does the same). To test the installed package
instead:

```bash
cd /tmp && LUME_MODELS=demo /path/to/.venv/bin/uvicorn --app-dir /nonexistent \
  lume_model_api.api.main:app --port 8001
```

An editable install also hides a bad `packages.find`, so check the wheel too:

```bash
pip wheel --no-deps . -w /tmp/wh && unzip -l /tmp/wh/*.whl | grep lume_model_api/
```

Both `lume_model_api/api/` and `lume_model_api/model/` should appear. The trailing `*` in
`include = ["lume_model_api*"]` matters here. Without it, setuptools matches the name exactly
and drops both subpackages.

## Apply to Kubernetes

No image has been pushed under `ghcr.io/slaclab/lume-model-api` yet, and nothing in CI builds
one, so the first deploy starts with a build and push.

1. Build and push the image, then smoke-test it (see [Verify a deployment](#verify-a-deployment)).
2. Set `newTag` under `images:` in `deploy/kubernetes/kustomization.yaml`.
3. Apply and wait for both rollouts:

   ```bash
   kubectl apply -k deploy/kubernetes
   kubectl -n lume-model-api rollout status deployment/lume-model-api-eval
   kubectl -n lume-model-api rollout status deployment/lume-model-api-live
   ```

The kustomization creates the namespace, the EPICS ConfigMap, both Deployments, both Services
and the Ingress. A rollback is the previous tag plus a re-apply. To restart pods without
changing the tag:

```bash
kubectl -n lume-model-api rollout restart deployment/lume-model-api-eval
kubectl -n lume-model-api rollout restart deployment/lume-model-api-live
```

The eval pool rolls with no downtime. The live pod uses `strategy: Recreate`, so a rollout never
runs two EPICS readers, at the cost of a gap of about 30 seconds per deploy.

Every object here is named `lume-model-api-*` in the `lume-model-api` namespace, separate from
the old monolith's `lume-monitor-*` objects in `lume-visualizations`, which belong to that repo
and keep serving `/live-monitor` until the UI is moved over. Applying this kustomization doesn't
touch them. The UI side of the move is in
[Coming from lume-visualizations](../frontend/CONCEPTS.md#coming-from-lume-visualizations).

## Configuration

Both Deployments run the same image and differ only in env. These are set on both:

| Variable | Value in the manifests | Notes |
| --- | --- | --- |
| `LUME_MODELS` | `{"cu_hxr_staged": {"kwargs": {"n_particles": 1000, "end_element": "TD11"}}}` | Models to host, keyed by URL name. Format in [ADDING_A_MODEL.md](ADDING_A_MODEL.md#lume_models-reference). |
| `LCLS_LATTICE` | `/opt/lcls-lattice` | Read by the model. Also set in the image. |
| `KMP_DUPLICATE_LIB_OK` | `TRUE` | Needed with torch and OpenMP. |
| `LUME_POOL_WORKERS` | `2` | Default worker processes per model (each holds one model instance). |
| `LUME_MAX_INFLIGHT` | `8` | Default evaluates in flight per model before a 503. |
| `LUME_WORKER_THREADS` | `2` | BLAS and OpenMP threads per worker. See the note below. |
| `LUME_ROOT_PATH` | `/lume-model-api` | The ingress prefix. Only affects generated links, so Swagger at `<base>/docs` can find its schema. |

The eval Deployment adds `LUME_ROLE=eval` and mounts no EPICS config. The live Deployment adds
`LUME_ROLE=live` and `LUME_LIVE_SOURCE=epics`, and loads `EPICS_CA_AUTO_ADDR_LIST` and
`EPICS_CA_ADDR_LIST` from the `lume-model-api-epics-config` ConfigMap with `envFrom`. Channel
access reads those once when pyepics is imported, so they have to be in the pod env at startup.
To point the live pod at a different CA gateway, edit `EPICS_CA_ADDR_LIST` in
`configmap-epics.yaml`.

`LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` apply to the whole process, so every model on the live
pod reads from the same source.

The image itself sets `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and
`TORCH_NUM_THREADS` to 2. Workers apply `LUME_WORKER_THREADS` with `setdefault`, so in the image
those preset values win and changing `LUME_WORKER_THREADS` alone has no effect. Change the
thread variables directly to tune it.

Other variables the service reads, unset in the manifests:

| Variable | Default | Effect |
| --- | --- | --- |
| `LUME_EVALUATE_TIMEOUT_S` | unset (no timeout) | Seconds before an evaluate is abandoned. Expiry marks the model's pool dead and restarts the pod. |
| `LUME_LIVE_INPUTS` | unset (all inputs) | JSON list restricting which input ids the live pod reads. |
| `LUME_MAX_IMAGE_DIM` | `512` | Longest side of 2-D array outputs after downsampling. `0` disables it. |
| `LUME_DERIVED_RANGE_FRACTION` | `0.5` | Width of the range given to an input with no `value_range`. |
| `LUME_STATIC_DIR` | unset | Directory served at `/` as a single-page app. |

`LUME_MODEL` and `LUME_MODEL_KWARGS` are retired. A pod that sets either fails at startup with a
message giving the `LUME_MODELS` equivalent, so a manifest copied from the old monolith can't
silently serve the demo model.

### Evaluate timeout

Neither Deployment sets `LUME_EVALUATE_TIMEOUT_S`. A subprocess evaluate can't be cancelled, so
when one times out, the worker keeps running it and its model memory is only freed by a restart.
The pool is marked dead, `/healthz` fails, and the pod restarts. Any value has to be well above
both the 25 to 30 second model build and the roughly 2.5 seconds a `cu_hxr_staged` evaluate
takes, or a slow frame becomes a pod restart. See
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#evaluate-timeouts-are-fatal).

### Changing the hosted models

Changing `LUME_MODELS` is an env edit and a rollout, with no rebuild, as long as each model's
Python dependencies are already in the image. Keep `LUME_MODELS` the same in both Deployments. A
model served by the eval pool but not the live pod has no live stream. Adding a model also means
raising memory and the startup budget (see [Sizing](#sizing)).

## Sizing

`LUME_POOL_WORKERS` and `LUME_MAX_INFLIGHT` are per model, not per pod. A pod's worker count is
the sum of `workers` over its models, and each worker holds a full model instance.

| Quantity | Value |
| --- | --- |
| Memory per `cu_hxr_staged` worker | budgeted at about 2 GB |
| Pod model memory | about 2 GB times the total workers across the pod's models |
| CPU | about 2 cores per worker |
| Model build time | 25 to 30 seconds for `cu_hxr_staged`. `facet_staged` at its default 10000 particles took about 4.5 minutes on a laptop, which is longer than the startup probe allows. |
| Current requests and limits (both Deployments) | `cpu: 2`, `memory: 5Gi` requested, `cpu: 4`, `memory: 7Gi` limit |

Startup takes the sum of the models' build times, because pools are warmed one model at a time
(see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#warmup-is-sequential)). When a pod's model list or
any `workers` value changes, update `resources` and the `startupProbe` budget together.

### Measured numbers

From production `/metrics` on 2026-08-24 for `cu_hxr_staged` on S3DF (`ad-accel-online-ml`).
Another model will differ, so re-measure from `lume_evaluate_seconds`, which has a `model`
label.

- An evaluate takes about 2.5 seconds at p50, on both the eval pod (mean 2.52 s over 48) and the
  live pod (mean 2.56 s over 18k). Live and interactive evaluates cost the same. An interactive
  call feels slower only because of the HTTP round trip and transfer.
- Thread count doesn't change single-evaluate latency.
- One worker per 2 cores is the best setting found. On a 4-core pod, `K=4, threads=1` had worse
  latency and throughput than `K=2, threads=2`, because one evaluate uses about 2 cores even at
  one thread.
- Concurrent evaluates without added latency are about `replicas * cores / 2`.
- An idle pod with `K=2` used about 2 GiB in total, so the 5Gi request is probably generous.
  Memory under load hasn't been measured.

Estimated capacity, assuming an interactive user waits about 2.5 seconds and then thinks for
about 10 (`users = concurrent * (1 + 10 / 2.5)`):

| Eval replicas (K=2, 4 cores) | Concurrent evaluates | Interactive users |
| --- | --- | --- |
| 2 | about 4 | about 20 |
| 8 | about 16 | about 80 |

These came from one node, one screen (OTR3) and the design inputs (`inputs={}`). Real latency
varies with track range, particle count and beam loss. Untested so far: removing the CPU limit
(nodes ran at about 11% utilization, and the slowdown at 4 concurrent looked like CFS
throttling), `K=2` with one thread, and a load test with varied inputs.

### The live pod

The live pod's pool is small (2 workers per model) because each live stream evaluates once per
frame no matter how many viewers are watching it. The number of distinct streams per model is
capped at that model's `max_inflight` (8 in the manifests). At the cap, a viewer asking for a new
set of outputs gets a 503. `lume_live_streams` sitting at `lume_pool_max_inflight` means the live
pool is too small for the number of different views people are opening. A stream produces one
frame per evaluate, about every 2.5 seconds for `cu_hxr_staged`, and only while it has a viewer.

## Scaling

The eval pool runs `replicas: 2`, so a single pod restart (25 to 30 seconds of model build)
isn't visible to users. To add capacity, scale it by hand a few minutes before a class or demo,
and scale it back afterwards:

```bash
kubectl -n lume-model-api scale deployment/lume-model-api-eval --replicas=4
kubectl -n lume-model-api scale deployment/lume-model-api-eval --replicas=2
```

The live Deployment stays at `replicas: 1`. Each replica would read EPICS and evaluate on its
own, doubling the model work and showing different viewers different frames. See
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#live-producer-is-a-singleton).

### What to watch

Every pod serves Prometheus metrics at `/metrics`. Nothing scrapes them in the cluster today, so
read them from a pod directly:

```bash
kubectl -n lume-model-api get pods
kubectl -n lume-model-api port-forward pod/<eval-pod-name> 8000:8000
curl -s localhost:8000/metrics | grep '^lume_'
```

| Metric | What it says about scaling |
| --- | --- |
| `lume_pool_inflight`, `lume_pool_max_inflight` | Evaluates in flight per model. Sum over `model` for the pod's load. Sitting near the max means more replicas are needed. |
| `lume_pool_rejected_total` | 503s from a full pool. Any steady increase means users are being turned away. |
| `lume_pool_dead` | 1 when a model's pool can't serve another evaluate. Has a `reason` label, so sum over it. |
| `lume_evaluate_seconds` | Evaluate latency histogram, by `model` and `kind` (`interactive` or `live`). |
| `lume_live_streams` | Distinct live streams per model, on the live pod. |

In-flight work is the signal to scale on, not CPU. Workers pin their thread counts, so CPU use
says little about how close a pod is to its limit. With several models per pod, add up the
`model` series per pod before comparing pods. Averaging the raw series reads low by a factor of
the number of models.

A dead pool reports `lume_pool_inflight` 0, so a pod that can't serve anything looks idle. Check
`lume_pool_dead` instead of inferring it from the in-flight numbers. The probes restart a pod
with a dead pool on their own, so a dead pool that keeps coming back points at a crash loop worth
investigating. The `reason` label (`worker_lost`, `unusable` or `timeout`) says where to look.
See [ARCHITECTURE.md](ARCHITECTURE.md#when-a-worker-dies) for what each one means and
[ARCHITECTURE.md](ARCHITECTURE.md#metrics) for the full metric list.

### Splitting models across pods

Today one pod pair hosts every model. Splitting is worth it when the summed worker memory stops
fitting a node, or when one busy model slows another down. In-flight limits and 503s are per
model, but CPU is shared across a pod's pools.

The split is one Deployment pair per model, each with a one-entry `LUME_MODELS` sized for its
own workers, plus one ingress rule per `/api/v1/models/<name>/` prefix. Clients don't change,
since the model name is already in the path. `ingress.yaml` has a commented sketch of the rules.
Every live Deployment stays at `replicas: 1`.

One problem needs solving at the same time. `GET /api/v1/models` is answered by a single pod, so
after a split it lists only that pod's models. That needs an aggregating catalog, either one pod
that knows about the others or a static JSON served by the ingress.

## Probes

All three probes on both Deployments hit `/healthz`. It returns 200 once every model's pool has
warmed, and 503 as soon as any pool is dead. A pod whose worker died is pulled from the Service
and restarted instead of answering 503 until someone notices. `/` can't be the target, since it
returns 404 on an image with no UI.

`/healthz` is kept separate from `GET /api/v1/models` because that route is for clients and has
to keep listing the models that still work on a pod where one pool has died. `/healthz` isn't in
`openapi.json` (`include_in_schema=False`), so no client can come to depend on it. See
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#dead-pools-are-not-rebuilt) for why a dead pool means
a restart.

| Probe | Setting | Effect |
| --- | --- | --- |
| `startupProbe` | `periodSeconds: 5`, `failureThreshold: 48` | Up to 4 minutes to build every model. |
| `readinessProbe` | `periodSeconds: 10` | Takes a dead pod out of the Service after about 30 seconds (3 failures). |
| `livenessProbe` | `periodSeconds: 30` | Restarts a dead pod after about 90 seconds (3 failures). |

On the eval pool the other replica covers those windows. The live pod has nothing to fail over
to, so the live view is down until the restart finishes. For a slower model or an extra model,
raise the startup `failureThreshold` rather than shortening the period.

## Verify a deployment

Before deploying an image, build each model inside it the way a pod does at startup:

```bash
docker run --rm -e LUME_MODELS=cu_hxr_staged --entrypoint python \
  ghcr.io/slaclab/lume-model-api:<tag> -c "
from lume_model_api.model.introspect import describe
from lume_model_api.model.loader import build_model, resolve
path, kwargs, _ = resolve('cu_hxr_staged')
info = describe(build_model(path, kwargs), name='cu_hxr_staged')
print(info.model, len(info.inputs), 'inputs', len(info.outputs), 'outputs')
print([item.key for item in info.screens])"
```

If it prints a non-empty screen list, the image can import, build and describe that model.
Repeat it for each model a pod hosts.

After applying, check health and the model list through a port-forward, then through the
ingress:

```bash
kubectl -n lume-model-api port-forward deploy/lume-model-api-eval 8000:8000
curl -s localhost:8000/healthz
curl -s https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api/api/v1/models
```

`/healthz` returns `{"status": "ok", "models": [...]}`. A 503 names each dead model and its
reason.

## Serve a UI

The image has no UI, so `/` returns 404. The recommended setup is a separate static nginx
Deployment that serves the UI's `dist/` on its own prefix and calls this API at the absolute path
`/lume-model-api` on the same host. That way no CORS is involved, a UI release doesn't restart
the model pods, and the UI doesn't have to track an API image tag. A UI with relative asset paths
needs a redirect from its bare path to the trailing-slash path. That Ingress belongs in the UI
repo, as a separate object from the one serving the UI, because nginx's `permanent-redirect`
annotation applies to every path in its Ingress and pairing it with a catch-all rule loops.

For local development, `LUME_STATIC_DIR` serves any directory at `/` as a single-page app (unknown
paths fall back to `index.html`):

```bash
LUME_STATIC_DIR=../my-ui/dist uvicorn lume_model_api.api.main:app --port 8000
```

The same mount serves `lume_model_api/static/` if a build has been copied there. That directory
is gitignored and shouldn't be committed.

CORS allows every origin, for both `fetch` and `EventSource`, so a UI on another origin works
without changes.

## URLs

| Environment | Cluster | Model list |
| --- | --- | --- |
| Production | `ad-accel-online-ml` | `https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models` |
| Development | `ad-accel-online-ml-dev` (vcluster) | `https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api/api/v1/models` |

`ingress.yaml` sets the dev host only, and there is no production overlay yet, so applying
`deploy/kubernetes/` deploys to dev. A production deploy needs the host changed to
`ard-modeling-service.slac.stanford.edu`.

Clients use everything through `/lume-model-api` as their base URL. The ingress strips the prefix
(`rewrite-target: /$2`), so the app sees `/api/v1/models`. Requests matching
`api/v1/models/<name>/live/*` and `api/v1/models/<name>/machine-snapshot` go to
`lume-model-api-live`, and everything else goes to `lume-model-api-eval`, both on Service port 80
to container port 8000. The model name matches `[^/]+`, so hosting another model needs no new
rule. The ingress-nginx controller orders regex locations by descending path length, not by
their order in the file, so a rule that has to match one exact path needs a `$` anchor.

TLS terminates on an F5 BigIP in front of the cluster, not at the Ingress, so `ingress.yaml` has
no `tls:` block. The pods see plain HTTP, and the image starts uvicorn with
`--proxy-headers --forwarded-allow-ips '*'` so redirects the app builds use `https://`. The
certificate belongs to whoever runs the BigIP. To check the certificate and the HTTP redirect
on either host:

```bash
HOST=ard-modeling-service.slac.stanford.edu  # or ad-accel-online-ml-dev.slac.stanford.edu
openssl s_client -connect $HOST:443 -servername $HOST </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
curl -sI http://$HOST/lume-model-api/api/v1/models | head -3
```

The `whitelist-source-range` annotation limits access to SLAC and a few other ranges. It was
tested on 2026-09-16 on the production host: an allowlist of one client IP admitted that client
and refused others, so the controller sees the real client address through the BigIP. A client that can't reach the URL
from off-site is hitting the allowlist. Remove the annotation if the service goes public.

## Run without Kubernetes

For a real model on a workstation, set up Bmad, pytao and the lattices with "Local install for a
VA model" in the [README](../../README.md), then run:

```bash
LUME_MODELS=cu_hxr_staged LCLS_LATTICE=$HOME/SLAC/lcls-lattice \
  KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \
  python -m uvicorn lume_model_api.api.main:app --host 0.0.0.0 --port 8000
```

The model takes 25 to 30 seconds to build before `/api/v1/models/cu_hxr_staged/config`
answers. The demo model needs none of that setup:

```bash
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```
