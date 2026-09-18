# Deploying

The image is API-only and hosts whatever `LUME_MODELS` selects, one or more models at a time. The
manifests in `deploy/kubernetes/` run it as two workloads: an autoscalable eval pool and a
singleton live producer. See
[ARCHITECTURE.md](ARCHITECTURE.md#lume_role-and-the-two-deployments) for why the split exists.

This file is the canonical home for the image build, the apply, the probes, scaling, pod sizing and
the packaging traps.

## Building the image

Build context is the repo root.

```bash
docker build -t ghcr.io/slaclab/lume-model-api:latest .
```

**Build for `linux/amd64` explicitly.** A plain `docker build` on an arm64 Mac mislabels the
manifest and k8s rejects it with "no match for platform":

```bash
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t ghcr.io/slaclab/lume-model-api:<tag> --load .
docker image inspect ghcr.io/slaclab/lume-model-api:<tag> --format '{{.Os}}/{{.Architecture}}'
```

The second command should print `linux/amd64`. Check it, because the failure otherwise appears
only at pod scheduling time.

The image installs miniforge plus `bmad` and `pytao` from conda-forge (and patches
`libtao.so`'s execstack, which pytao needs), the CPU torch wheel, the LCLS and FACET2 lattices at
pinned refs, the pinned lume stack, virtual-accelerator at `VA_REF` with its `[surrogate,bmad]`
extras, and finally this package with the `[epics]` extra. Build args `PYTHON_VERSION`,
`LCLS_LATTICE_REF`, `FACET_LATTICE_REF`, `VA_REF`, `LUME_BMAD_REF`, `LUME_BASE_VERSION`,
`LUME_TORCH_VERSION`, `LUME_CHEETAH_VERSION` and `DOCKER_PLATFORM` are all overridable.

**The lume stack is pinned in the Dockerfile, not resolved by virtual-accelerator.** VA declares
bare `lume-base`, `lume-bmad`, `lume-torch` and `lume-cheetah` requirements, so an unpinned build
installs whatever PyPI serves that day. `LUME_BMAD_REF` in particular has to stay a git ref: the
released lume-bmad `v0.1.0` builds every `<ele>_beam` variable without `read_only=True`, which
makes `introspect.describe` drop the beams as writable non-scalars and publish `screens: []` with
no particle outputs at all. The fix is upstream in `110230c9` and is not in any tag yet. The build
asserts the resolved lume-bmad carries it and prints the resolved lume versions, so a regression
fails the build instead of shipping a pod whose only symptom is a missing screen list.

> **`conda install bmad pytao` is still unpinned,** so a fresh solve can pull a Bmad that rejects a
> model's `tao.init`. Capture the working versions from a verified build if that ever bites.

Smoke-test before deploying, which catches model or env breakage without touching the cluster:

```bash
docker run --rm -e LUME_MODELS=cu_hxr_staged --entrypoint python ghcr.io/slaclab/lume-model-api:dev -c "
from lume_model_api.model.introspect import describe
from lume_model_api.model.loader import build_model, resolve
path, kwargs, _ = resolve('cu_hxr_staged')
info = describe(build_model(path, kwargs), name='cu_hxr_staged')
print(info.model, len(info.inputs), 'inputs', len(info.outputs), 'outputs')
print([item.key for item in info.screens])"
```

That does exactly what a pod does at startup, for one model: import it, build it, introspect it. If
it prints, the image can serve that model's config. Repeat it per model when a pod hosts several.

## Applying to Kubernetes

```bash
kubectl apply -k deploy/kubernetes
```

`kustomization.yaml` pins the tag under `images:`, so the deploy sequence is: build, push, bump
`newTag`, re-apply. Rollback is the old tag plus a re-apply. The eval pool rolls with no
downtime. The live singleton uses `strategy: Recreate` so a rollout never runs two EPICS
readers, which costs a gap of roughly 30 seconds per deploy.

**Everything here is named `lume-model-api-*` in namespace `lume-model-api`, on purpose.** It is
a new set of objects rather than an update of the old ones, so applying it leaves the running
monolith untouched and the two serve side by side under different prefixes. See "Cutting over
from the old monolith" below for the sequence, and do not rename these to `lume-monitor-*`
thinking it will update the old service in place.

`keda-scaledobject.yaml` is deliberately **not** in `kustomization.yaml`. It needs KEDA
installed plus site-specific values, so apply it separately when the cluster is ready:

```bash
kubectl apply -f deploy/kubernetes/keda-scaledobject.yaml
```

## Cutting over from the old monolith

The old `lume-monitor-*` Deployments, Services and Ingresses in namespace `lume-visualizations`
serve `/live-monitor`, which is the pre-split API plus the React UI in one image. They belong to
the `lume-visualizations` repo and **nothing in this repo touches them.** They keep serving
`/live-monitor` until the UI is ported, so users lose nothing while this service is brought up.

That is why the manifests here use a different namespace, different object names and a different
prefix. Both can be live on the same host at the same time, and the cut-over is a UI change
rather than a backend outage.

1. **Build and push the image.** No tag exists under `ghcr.io/slaclab/lume-model-api` yet, and
   the build is unverified, so do this first and smoke-test it as described above. Then set
   `newTag` in `kustomization.yaml`.
2. **Apply.** This creates the namespace, the ConfigMap, both Deployments, both Services and the
   Ingress. Nothing it creates collides with an existing object.

   ```bash
   kubectl apply -k deploy/kubernetes
   kubectl -n lume-model-api rollout status deployment/lume-model-api-eval
   kubectl -n lume-model-api rollout status deployment/lume-model-api-live
   ```

   or
  ```
  kubectl -n lume-model-api rollout restart  deployment/lume-model-api-eval    
  kubectl -n lume-model-api rollout restart deployment/lume-model-api-live     
  ```

3. **Verify the new prefix** while the old one is still serving its own traffic.

   ```bash
   curl -s https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models
   ```

   That should list the hosted models. `/live-monitor/api/config` should still answer with the
   old shape, unchanged, which is how you know the two are independent.

4. **Port the UI and deploy it as its own workload,** per "Serving a UI" below. Build it with
   `VITE_API_URL=/lume-model-api`, an absolute path on the same host, so no CORS is involved and
   the UI's own prefix is irrelevant to its API calls. It needs a Deployment of an nginx image
   carrying `dist/`, a Service on 80 to 8080, and one Ingress rule keeping the old public path
   `/live-monitor(/|$)(.*)`, all in the UI repo. A UI with relative asset paths also wants the
   bare-path redirect to `/live-monitor/`, which is the other reason that Ingress belongs with the
   UI rather than here.

5. **Delete the old backend.** Once the ported UI is serving `/live-monitor` and calling
   `/lume-model-api`, remove the old Deployments so nothing is paying for two copies of the model.

   ```bash
   kubectl -n lume-visualizations delete deployment lume-monitor-eval lume-monitor-live
   ```

**Rollback at any point before step 5 is deleting the new Ingress.** With
`kubectl -n lume-model-api delete ingress lume-model-api` the new prefix stops answering and the
old service is unaffected, because it was never modified. The pods can stay up while you
diagnose. After step 5 the rollback is re-applying the old manifests from the
`lume-visualizations` repo.

## Environment per Deployment

Both Deployments run the same image and differ only in env. Common to both:

| Variable | Value | Why |
| --- | --- | --- |
| `LUME_MODELS` | `{"cu_hxr_staged": {"kwargs": {"n_particles": 1000, "end_element": "TD11"}}}` | The hosted models, keyed by URL name. The manifests list `cu_hxr_staged` only. A key with no `factory` is a shortcut, so its kwargs merge over the shortcut's defaults. The rules in full are in [ADDING_A_MODEL.md](ADDING_A_MODEL.md#the-rules-in-full). |
| `LCLS_LATTICE` | `/opt/lcls-lattice` | Read by the model, not by this service. Cloned into the image. |
| `KMP_DUPLICATE_LIB_OK` | `TRUE` | Required with the torch/OpenMP combination. |
| `LUME_POOL_WORKERS` | `2` | Default model instances **per model**, not per pod. See [sizing](#sizing-a-pod). |
| `LUME_MAX_INFLIGHT` | `8` | Default evaluates in flight before 503, also per model. |
| `LUME_WORKER_THREADS` | `2` | Per-worker BLAS/OMP cap, matched to about 2 cores per worker. |
| `LUME_ROOT_PATH` | `/lume-model-api` | The prefix the ingress strips. The app still serves every route at `/api/...`, so this changes no routing. It only fixes the links FastAPI generates, which is what makes Swagger work under the prefix. |

`LUME_MODEL` and `LUME_MODEL_KWARGS` are retired. A Deployment that still sets either fails
startup with a message naming the variable and printing the `LUME_MODELS` equivalent, which is
deliberate: a manifest ported from the pre-split monolith would otherwise serve the default model
instead of the configured one.

With `LUME_ROOT_PATH` set, the interactive docs are at
`https://ard-modeling-service.slac.stanford.edu/lume-model-api/docs` and the schema they fetch is
at `<base>/openapi.json`. Without it, that page loads and then fails to fetch a schema from the
host root, which is the whole reason the variable exists. Leave it **unset** when regenerating
`openapi.json`, see [API.md](API.md#changing-the-contract).

`deployment.yaml` (eval pool) additionally sets `LUME_ROLE=eval` and mounts **no** EPICS
ConfigMap, since it never touches channel access. `deployment-live.yaml` sets `LUME_ROLE=live`
and `LUME_LIVE_SOURCE=epics`, and pulls `EPICS_CA_AUTO_ADDR_LIST` and `EPICS_CA_ADDR_LIST` from
the `lume-model-api-epics-config` ConfigMap via `envFrom`. Those must be set before pyepics is
imported, which is why the code imports it lazily and why they arrive as pod env rather than being
set at runtime. `LUME_LIVE_SOURCE` is process-wide rather than per model, so every model on the
live pod reads from channel access. To point the reader at your site's gateway, edit
`configmap-epics.yaml`'s `EPICS_CA_ADDR_LIST`.

### `LUME_EVALUATE_TIMEOUT_S` is unset, and that is a real choice

Neither Deployment sets it. Unset (or `0`) means no timeout: an evaluate takes as long as the model
takes.

Set it to a float number of seconds and an evaluate that exceeds it marks that model's pool dead,
which fails `/healthz` and restarts the pod. That is not an overreaction. A subprocess evaluate
cannot be cancelled, so the worker keeps computing the abandoned frame and its multi-GB model
memory is only released by a process restart. See
[ARCHITECTURE.md](ARCHITECTURE.md#lume_evaluate_timeout_s).

So any value has to sit well above both the 25 to 30 second model build and the roughly 2.5 seconds
a real evaluate takes. A value near normal latency turns a slow frame into a pod restart, which is
strictly worse than the hang it was meant to catch.

### Changing the hosted models

An env edit plus a rollout, with no image rebuild, as long as each model's Python dependencies are
already in the image. Add a key to `LUME_MODELS` for another shortcut, or give an explicit
`factory` for a full `module.path:factory` reference.

**Adding a model to a pod is a memory decision first.** See sizing, next.

## Sizing a pod

`LUME_POOL_WORKERS` and `LUME_MAX_INFLIGHT` are per-model defaults rather than a pod budget, so a
pod's worker count is the sum over its models.

| Quantity | Value |
| --- | --- |
| Per real model worker | about 2 GB |
| Per pod model memory | about 2 GB times the sum of `workers` across that pod's models |
| The demo model | far smaller, so a mixed pod is dominated by its real models |
| CPU | about 2 cores per worker, so the sum of workers should be roughly half the pod's cores |

Two real models at 2 workers each is therefore about 8 GB before the web process. `requests` and
`limits` move whenever a pod's model list or any model's `workers` changes, and the `startupProbe`
budget moves with it, because pools are warmed one model at a time and startup becomes the sum of
their build times.

The measured numbers behind the 2 GB figure and the `K = cores/2` guidance are in
[`../deploy/kubernetes/CAPACITY.md`](../deploy/kubernetes/CAPACITY.md), measured against
`cu_hxr_staged`. Another model will differ, so re-measure from `lume_evaluate_seconds` on the
running pods and note that it carries a `model` label.

## The singleton live pod

`deployment-live.yaml` has `replicas: 1` and **must keep it**. This is a singleton on purpose:
if it ran on N replicas each would independently read EPICS and evaluate, costing N times the
model work and showing different viewers divergent frames. One producer fans out to all browsers
over SSE. `keda-scaledobject.yaml` targets the eval Deployment only, for the same reason.

Its pool is small (2 workers) because the hub evaluates once per output set per frame regardless
of viewer count, so a couple of workers cover several concurrent views. That budget is per model,
and there is one hub per model, so size its pool for the views you expect across all hosted models
rather than per model. The number of distinct producer loops is capped at each model's
`max_inflight`, and at the cap a viewer asking for a new output set gets a 503, so
`lume_live_streams` sitting at `lume_pool_max_inflight` is the signal that the live pool is too
small for how many different views people are opening.

## Splitting models across pods

Not done today, and deferrable, because the model name is already in the path: one pod per model
and one pod for every model serve the same client contract. Two triggers say it is time.

**Pod memory.** Once the sum in [sizing](#sizing-a-pod) stops fitting a node, or you are cutting
workers per model to make it fit and losing eval concurrency, splitting beats shrinking.

**One model's load starving another.** In-flight accounting and the 503 threshold are per model, so
a saturated model rejects only its own traffic. CPU is not: the pools share the pod's cores, so a
heavily used model slows a quiet one down and neither `max_inflight` nor KEDA sees the cause.

The change is one Deployment pair per model, each with a one-entry `LUME_MODELS` and sized for its
own workers, plus one ingress rule per `/api/v1/models/<name>/` prefix. **No client changes and no
contract change**, since the name was already in the path. Keep every live pod at `replicas: 1`.

**Open item to close as part of the split:** `GET /api/v1/models` is answered by one pod, so after
a split it lists that pod's models rather than the whole catalog, and a dropdown built from it
silently loses the rest. Nothing here closes that today, deliberately, because the right answer
(an aggregating catalog on one pod, or a static catalog served at that path by the ingress) depends
on how the pods end up divided.

## Probes

**All three probes hit `/healthz`, on both Deployments.** It returns 200 only once **every** pool
has warmed and every model description has come back from a worker, and 503 as soon as any pool has
lost a worker process, which is unrecoverable in-process. So readiness genuinely means "can serve
an evaluate on any hosted model", and a pod whose model subprocess died gets pulled from the
Service and restarted rather than serving 503s until someone notices. It cannot be `/`, which 404s
on an API-only image.

**The probe target is deliberately not `GET /api/v1/models`.** That route is client-facing
discovery, and on a pod hosting several models it has to keep listing the ones that still work even
when one pool is dead, which is the opposite of what a probe wants. `/healthz` is also outside the
`/api/v1` contract: it carries `include_in_schema=False`, so it is absent from `openapi.json`, no
consumer can pin it, and adding it did not require regenerating the committed schema. See
[ARCHITECTURE.md](ARCHITECTURE.md#when-a-worker-dies-the-pool-is-dead-and-the-pod-restarts) for why
a lost worker is fatal to the pool.

Two timings worth knowing. Readiness at `periodSeconds: 10` with the default `failureThreshold: 3`
takes about 30 seconds to remove a dead pod from the Service, and liveness at `periodSeconds: 30`
about 90 seconds to restart it. On the eval pool that is covered by the other replica. On the live
singleton there is nothing to fail over to, so the live view is down for that window either way.

| Probe | Setting | Reason |
| --- | --- | --- |
| `startupProbe` | `periodSeconds: 5`, `failureThreshold: 48` | Up to four minutes for model init. A real model takes 25 to 30 seconds, times K workers, and pools are warmed one model at a time, so budget the sum over models. |
| `readinessProbe` | `periodSeconds: 10` | Keeps a cold or wedged pod out of the Service, and pulls a pod whose pool has died. |
| `livenessProbe` | `periodSeconds: 30` | Restarts a pod whose event loop is gone or whose pool has died. |

If you host a slower model, or a second model, raise `failureThreshold` rather than lowering the
probe period.

## Scaling

Baseline is `replicas: 2` on the eval pool for HA, which avoids the 25 to 30 second cold start
being user-visible on a single-pod restart. When KEDA is enabled its `minReplicaCount` governs
and the Deployment's `replicas` field becomes only the pre-KEDA value.

Manual scaling, which is the current reality since the cluster has no KEDA or Prometheus
installed:

```bash
kubectl -n lume-model-api scale deployment/lume-model-api-eval --replicas=4
```

Scale up a few minutes **before** a class or demo, because of the cold start. Never scale
`lume-model-api-live` above 1.

The honest saturation signal is `lume_pool_inflight`, not CPU. Thread pinning makes CPU a poor
proxy, which is why the KEDA Prometheus trigger scales on in-flight work. That series carries a
`model` label, so the per-pod number is `sum by (pod) (lume_pool_inflight)` and the KEDA query is

```
avg(sum by (pod) (lume_pool_inflight))
```

A plain `avg(lume_pool_inflight)` would average over the model label too and read low by a factor
of M on a pod hosting M models. The `sum by (pod)` form relies on the scrape config attaching a
`pod` label, which the Prometheus Operator's `ServiceMonitor` does by default.

**That query cannot see a bricked pod.** A pool whose worker died reports `lume_pool_inflight` 0
forever, so a pod that can serve nothing reads as idle and drags the average down. Alert on
`lume_pool_dead`, summed over its `reason` label, instead of trying to infer it from the other
gauges. The probes restart such a pod by themselves, so the alert is about noticing a crash loop
rather than about reacting to one event.

- [`../deploy/kubernetes/SCALING.md`](../deploy/kubernetes/SCALING.md) covers the manual and
  KEDA paths and the KEDA and Prometheus install.
- [`../deploy/kubernetes/CAPACITY.md`](../deploy/kubernetes/CAPACITY.md) has the measured
  per-eval latency, the right worker count per pod and concurrent-user estimates.
- [API.md](API.md#get-metrics) is the full metric list and what each one is for.

## Verifying an install actually works

Two packaging failure modes here look like success. Both have bitten this repo.

**`pip install -e .` with the package directory absent exits 0.** It prints
`Successfully installed lume-model-api-0.1.0`, finds no packages, and then every import fails
with `ModuleNotFoundError` even after the code arrives. This is why the `Dockerfile` copies
`lume_model_api/` **before** installing. Reversing those two lines produces a broken image with
a green build. `pyproject.toml` also declares `readme = "README.md"` and the Dockerfile copies it,
so the build hard-fails if the README is ever removed.

**`uvicorn` always puts the current directory on `sys.path`.** Its CLI defaults `--app-dir` to
`""` and calls `sys.path.insert(0, app_dir)` unconditionally, so launching from the repo root
imports the source tree whether or not the package is installed, and in the container
`WORKDIR /app` does the same. To actually test an install:

```bash
cd /tmp && LUME_MODELS=demo /path/to/.venv/bin/uvicorn --app-dir /nonexistent \
  lume_model_api.api.main:app --port 8001
```

Also check the wheel, because an editable install masks a bad `packages.find`:

```bash
pip wheel --no-deps . -w /tmp/wh && unzip -l /tmp/wh/*.whl | grep lume_model_api/
```

Both `lume_model_api/api/` and `lume_model_api/model/` must appear. The trailing `*` in
`include = ["lume_model_api*"]` in `pyproject.toml` is load-bearing: without it setuptools
matches the name exactly and silently drops both subpackages.

## Serving a UI

This image ships no UI, so `/` returns 404 and the probes target `/healthz` instead.

**The recommended shape is a separate static nginx workload.** A small Deployment serving the
UI's `dist/`, on its own prefix, calling this API by the absolute path `/lume-model-api` on the
same host. No CORS is involved, a UI release does not restart the model pods, and the UI does not
have to track an API image tag. That is what the cut-over above describes and what the UI port in
[MIGRATING_LUME_VISUALIZATIONS.md](MIGRATING_LUME_VISUALIZATIONS.md) assumes.

Note that `deploy/kubernetes/deployment.yaml`'s header comment still blesses overlaying this
Deployment's image with a derived one that bakes a UI in. That comment is the outdated statement of
the three, and it should be trimmed the next time that file is edited. Baking a UI in works
mechanically, but it couples a UI release to an API rollout and restarts the model pods for a
frontend change, which is the cost the separate workload exists to avoid.

For a dev loop, mount a build at runtime instead. `LUME_STATIC_DIR` points at any directory and it
is served at `/` as a single-page app (`html=True`, so unknown paths fall back to `index.html`).
The same mount picks up `lume_model_api/static/` if a build was ever copied there, which is
gitignored and must never be committed.

```bash
LUME_STATIC_DIR=../my-ui/dist uvicorn lume_model_api.api.main:app --port 8000
```

If a UI is served from a genuinely different origin, CORS is open here and covers both `fetch` and
the `EventSource` stream, so no change is needed either way.

This repo carries no bare-path redirect Ingress. An API has no relative asset paths, so nothing
here breaks without the trailing slash. A UI that does need one should carry that Ingress itself,
as a separate object from its serving Ingress, because nginx's `permanent-redirect` annotation
applies to every path in its Ingress and pairing it with a catch-all rule would loop.

## The deployed URL

The ingress serves one host, defined in `ingress.yaml`. The model list, which is the first URL a
client or an operator wants, is

```
https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models
```

Clients use everything up to and including `/lume-model-api` as their base URL and append
`/api/v1/models/...`. The `/lume-model-api` prefix is consumed by the ingress
(`rewrite-target: /$2`), so the app sees `/api/v1/models`. It is told the prefix through
`LUME_ROOT_PATH`, but only so Swagger at `<base>/docs` can find the schema, and no route depends
on it. Routing splits by regex: `api/v1/models/<name>/live/*` and
`api/v1/models/<name>/machine-snapshot` reach `lume-model-api-live`, everything else reaches
`lume-model-api-eval`, both on port 80 to container port 8000. The model name is `[^/]+` in that
regex, so adding a model to a pod changes no rule.

One property of the ingress worth knowing before anyone edits it: the nginx controller sorts regex
locations by descending path length rather than honouring manifest order. So a rule that must match
exactly one path needs a `$` anchor rather than a position in the file.

Three things about this deployment are worth knowing before anyone relies on the URL.

**TLS terminates in front of the cluster, not at the Ingress.** Checked in September 2026: port
443 on the host presents the `*.slac.stanford.edu` certificate issued by InCommon, and plain HTTP
gets a `302` to `https://` from a server identifying itself as `BigIP`. So an F5 load balancer
owns the certificate and forwards to ingress-nginx, which is why neither the old Ingress nor
`ingress.yaml` has a `tls:` block and why none is needed. Two things follow. The pods see plain
HTTP, so the image starts uvicorn with `--proxy-headers --forwarded-allow-ips='*'` to trust the
`X-Forwarded-Proto` the controller adds, otherwise any absolute URL the app builds (FastAPI's
trailing-slash redirects, for instance) would say `http://` and bounce through the BigIP. And
the certificate is renewed by whoever runs the BigIP, not by this repo. To re-check:

```bash
openssl s_client -connect ard-modeling-service.slac.stanford.edu:443 \
  -servername ard-modeling-service.slac.stanford.edu </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
curl -sI http://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models | head -3
```

**The allowlist is on, and it works.** `ingress.yaml` carries a `whitelist-source-range`
annotation covering SLAC and a few other ranges. Because TLS terminates on the BigIP, there was
a real question whether ingress-nginx sees the client's address or the load balancer's. Tested in
the same session, against the old monolith's serving Ingress: an allowlist of a single off-cluster
client IP admitted exactly that client (`200`) and an allowlist excluding it refused it (`403`),
so the controller evaluates the forwarded client address and the annotation is effective. A
client that cannot reach the URL from off-site is seeing the allowlist, not an outage. Remove
the annotation when the service is meant to go public.

Two things learned about the old deployment while testing, for whoever maintains it: its serving
Ingress `lume-monitor` has no allowlist at all (the annotation there is commented out), and its
`lume-monitor-redirect` Ingress never matches, because the serving Ingress's `rewrite-target`
turns every path on the host into a regex and its longer `^/live-monitor(/|$)(.*)` wins over the
redirect's `^/live-monitor$`. So the bare `/live-monitor` URL serves `index.html` from the wrong
base instead of redirecting. Neither affects this service.

**Nothing answers this prefix yet.** `/live-monitor` on the same host is the old monolith, whose
`GET /live-monitor/api/config` returns the pre-split shape (`scalars`, `screens[].has_image`, no
`outputs`, and no `scan_pv`) on a path this service does not serve at all.

## Running without Kubernetes

For a real model on a workstation, `scripts/setup-dev-env.sh` builds the pinned conda env
(`lume-webapp` by default) with Bmad, pytao, CPU torch and virtual-accelerator at the same
`VA_REF` as the image, then installs this package with the `[epics]` extra. It prints the run
commands when it finishes.

```bash
bash scripts/setup-dev-env.sh
conda run -n lume-webapp env \
  LUME_MODELS=cu_hxr_staged LCLS_LATTICE=$HOME/SLAC/lcls-lattice \
  KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \
  python -m uvicorn lume_model_api.api.main:app --host 0.0.0.0 --port 8000
```

Allow 25 to 30 seconds before `/api/v1/models/cu_hxr_staged/config` returns 200. For anything that
does not need the real model, the demo model needs none of that env:

```bash
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```
