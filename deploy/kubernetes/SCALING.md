# Scaling the LUME monitor

## Current setup

- **Eval pool** (`lume-monitor-eval`): stateless, EPICS-free, safe to run many
  replicas. Baseline is `replicas: 2` for HA (a single-pod restart is ~25-30s of
  model cold start).
- **Live producer** (`lume-monitor-live`): the EPICS reader + broadcast hub.
  **Singleton — must stay `replicas: 1`.** Do not scale it.

There is no autoscaler installed in the cluster (no KEDA, no Prometheus), so scaling
is **manual**. Given the load pattern (scheduled classes on a low steady baseline),
that's usually enough: bump replicas before a class, drop them after.

## Manual scaling

Cold start is ~25-30s, so scale up a few minutes **before** a class starts:

```bash
# Before a class / demo — more eval capacity
kubectl -n lume-visualizations scale deployment/lume-monitor-eval --replicas=4

# After — back to baseline
kubectl -n lume-visualizations scale deployment/lume-monitor-eval --replicas=2
```

Never scale `lume-monitor-live` above 1 (see above).

## Inspecting load (/metrics)

Each pod exposes Prometheus-format metrics at `/metrics`, even with no Prometheus
installed — useful for eyeballing saturation before deciding to scale:

```bash
kubectl -n lume-visualizations port-forward deploy/lume-monitor-eval 8000:8000
# in another shell:
curl -s localhost:8000/metrics | grep '^lume_'
```

Measured latency, worker-count tuning, and capacity estimates live in
[`CAPACITY.md`](./CAPACITY.md).

Key series:
- `lume_pool_inflight` / `lume_pool_max_inflight` — current vs max in-flight per pod.
  Sustained `inflight` near `max_inflight` (with 503s) means you need more replicas.
- `lume_evaluate_seconds` — evaluate latency histogram.
- `lume_evaluate_total{outcome=...}` and `lume_pool_rejected_total` — throughput and
  503 rejections.

## When to graduate to KEDA / Prometheus

Manual scaling breaks down when:
- classes become **frequent** enough that manual toil is annoying, or
- load becomes **unpredictable** (not just scheduled windows), or
- you want **automatic scale-down** so you're not paying for idle pods.

Two levels, depending on what you need:

| Need | Install | Why |
|---|---|---|
| Automated **scheduled** pre-scale (no reactive metrics) | **KEDA only** | KEDA's `cron` trigger needs no Prometheus. Replaces the manual `kubectl scale` with a schedule. |
| **Reactive** scaling on saturation | **KEDA + Prometheus** | KEDA's `prometheus` trigger scales on `lume_pool_inflight`; needs Prometheus scraping the pods. |

Both require **cluster-admin** to install the controllers/CRDs. A ready-to-use
`ScaledObject` (cron + prometheus triggers) is already in
[`keda-scaledobject.yaml`](./keda-scaledobject.yaml) — it just needs KEDA present and
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
  name: lume-monitor-eval
  namespace: lume-visualizations
spec:
  selector:
    matchLabels:
      app: lume-monitor-eval   # add this label to service.yaml's eval Service first
  endpoints:
    - port: http
      path: /metrics
```

Verify the metric is queryable, then set the `serverAddress` in
`keda-scaledobject.yaml` to the in-cluster Prometheus Service
(e.g. `http://prometheus-operated.monitoring.svc.cluster.local:9090`) and apply it.

> Finding the address and checking cluster headroom (node allocatable, namespace
> ResourceQuota) is covered by `kubectl get svc -A | grep -i prometheus` and
> `kubectl describe resourcequota -n lume-visualizations`.
