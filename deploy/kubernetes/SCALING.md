# Scaling the model service

## Current setup

- **Eval pool** (`lume-model-api-eval`): stateless, EPICS-free, safe to run many
  replicas. Baseline is `replicas: 2` for HA (a single-pod restart is ~25-30s of
  model cold start).
- **Live producer** (`lume-model-api-live`): the EPICS reader + broadcast hub.
  **A singleton that must stay `replicas: 1`.** Do not scale it.

There is no autoscaler installed in the cluster (no KEDA, no Prometheus), so scaling
is **manual**. Given the load pattern (scheduled classes on a low steady baseline),
that's usually enough: bump replicas before a class, drop them after.

Capacity numbers in [`CAPACITY.md`](./CAPACITY.md) were measured for `cu_hxr_staged`, so
other hosted models will differ.

## Manual scaling

Cold start is ~25-30s, so scale up a few minutes **before** a class starts:

```bash
# Before a class or demo, more eval capacity
kubectl -n lume-model-api scale deployment/lume-model-api-eval --replicas=4

# After, back to baseline
kubectl -n lume-model-api scale deployment/lume-model-api-eval --replicas=2
```

Never scale `lume-model-api-live` above 1 (see above).

## Inspecting load (/metrics)

Each pod exposes Prometheus-format metrics at `/metrics`, even with no Prometheus
installed, which is useful for eyeballing saturation before deciding to scale:

```bash
kubectl -n lume-model-api port-forward deploy/lume-model-api-eval 8000:8000
# in another shell:
curl -s localhost:8000/metrics | grep '^lume_'
```

Measured latency, worker-count tuning, and capacity estimates live in
[`CAPACITY.md`](./CAPACITY.md).

Key series, with the full list in [`../../docs/API.md`](../../docs/API.md#get-metrics):

- `lume_pool_inflight` and `lume_pool_max_inflight`: current versus max in-flight, one series per
  hosted model per pod (the `model` label). Sum over `model` to get the pod's load.
  Sustained `inflight` near `max_inflight` (with 503s) means you need more replicas.
- `lume_evaluate_seconds`: evaluate latency histogram.
- `lume_evaluate_total{outcome=...}` and `lume_pool_rejected_total`: throughput and 503 rejections.
  `outcome="cancelled"` is a client that disconnected mid-evaluate, not a failure.
- `lume_live_inputs_readable` and `lume_live_inputs_total` on the live pod: how much of the machine
  the live view is actually reading. `readable` below `total` is normal for a model whose inputs are
  not all real PVs, so watch for a **drop** from this pod's own steady state. The ids that stopped
  reading are being served from the model's design values instead of from the machine.
- `lume_live_streams` on the live pod: distinct producer loops running. It is capped at
  `lume_pool_max_inflight`, and at the cap a viewer asking for a new output set is refused with a
  503, so this sitting at the cap means the live pool is too small for how many different views
  people are opening.

## Alert on `lume_pool_dead`, because saturation queries cannot see it

`lume_pool_dead` goes to 1 when a model's pool can no longer serve an evaluate. That pool can never
recover in place, so every request for that model returns 503 until the pod restarts.

The trap is that a dead pool reports `lume_pool_inflight` **0**. A pod that can serve nothing
therefore looks perfectly idle to the KEDA query below, and drags the average down rather than up.
Alert on `lume_pool_dead` directly and do not try to infer death from the other gauges.

**Sum over the `reason` label when you alert.** It says whether the pool lost a worker process, had
a model break itself, or hit `LUME_EVALUATE_TIMEOUT_S`, which matters for the investigation and not
for the alert, since all three need the same pod restart. An alert written against the unlabelled
series matches nothing.

The probes handle the recovery by themselves, since `/healthz` fails as soon as any pool is dead, so
the alert is about noticing a crash loop rather than about reacting to a single event.

## When to graduate to KEDA / Prometheus

Manual scaling breaks down when:
- classes become **frequent** enough that manual toil is annoying, or
- load becomes **unpredictable** (not just scheduled windows), or
- you want **automatic scale-down** so you're not paying for idle pods.

Two levels, depending on what you need:

| Need | Install | Why |
|---|---|---|
| Automated **scheduled** pre-scale (no reactive metrics) | **KEDA only** | KEDA's `cron` trigger needs no Prometheus. Replaces the manual `kubectl scale` with a schedule. |
| **Reactive** scaling on saturation | **KEDA + Prometheus** | KEDA's `prometheus` trigger scales on `avg(sum by (pod) (lume_pool_inflight))`, summing the per-model series first so a pod hosting several models is not under-counted. Needs Prometheus scraping the pods with a `pod` label. |

Both require **cluster-admin** to install the controllers/CRDs. A ready-to-use
`ScaledObject` (cron + prometheus triggers) is already in
[`keda-scaledobject.yaml`](./keda-scaledobject.yaml). It just needs KEDA present and
its TODO values filled in.

### Install steps

**KEDA** (Helm):

```bash
helm repo add kedacore https://kedacore.github.io/charts && helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace
kubectl get pods -n keda
kubectl get crd | grep keda.sh   # confirm the ScaledObject CRD exists
```

For **cron-only** scaling, edit `keda-scaledobject.yaml` to keep just the `cron`
trigger (delete the `prometheus` one), set the real timezone + class windows, then:

```bash
kubectl apply -f keda-scaledobject.yaml
```

**Prometheus** (only for the reactive trigger; Helm kube-prometheus-stack):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```

Then make it scrape the eval pods. With the Prometheus Operator, add a
`ServiceMonitor` selecting the eval Service's `http` port at path `/metrics`:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: lume-model-api-eval
  namespace: lume-model-api
spec:
  selector:
    matchLabels:
      app: lume-model-api-eval   # add this label to service.yaml's eval Service first
  endpoints:
    - port: http
      path: /metrics
```

Verify the metric is queryable, then set the `serverAddress` in
`keda-scaledobject.yaml` to the in-cluster Prometheus Service
(e.g. `http://prometheus-operated.monitoring.svc.cluster.local:9090`) and apply it.

> Finding the address and checking cluster headroom (node allocatable, namespace
> ResourceQuota) is covered by `kubectl get svc -A | grep -i prometheus` and
> `kubectl describe resourcequota -n lume-model-api`.
