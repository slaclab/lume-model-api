# Capacity and performance findings

All numbers here are for `cu_hxr_staged` on the S3DF cluster (`ad-accel-online-ml`). Another hosted
model will differ, so re-measure from `lume_evaluate_seconds`, which carries a `model` label.
Purpose: pin per-eval latency `L`, find the right worker count `K` per pod, and estimate how many
concurrent users can be served.

## Current numbers

Read off production `/metrics` on 2026-08-24, image `n6`.

- **`L` is about 2.5s per eval (p50).** Eval pod (interactive) mean 2.52s over 48 evals, live pod
  mean 2.56s over 18k evals, with essentially the entire histogram in the 2 to 4s bucket on both.
- **Live and interactive evals cost the same.** The live view is not a cheaper path. An apparent
  "live 2s versus interactive 6s" is the interactive client's round trip (request, response,
  full-frame transfer and parse), which an SSE-pushed frame skips.
- **`L` is the bottleneck, not pod count, and it is independent of thread count.**
- **Pod tuning is already near-optimal at about 2 CPU cores per worker (`K = cores/2`).** Do not
  pack more workers per pod, which measurably makes things worse.
- **Concurrent no-added-latency evals are about `replicas x cores/2`.** At this `L`, roughly 100
  interactive users would need about 10 pods, so cutting `L` is higher leverage than scaling.
- **Live cadence is one frame per `L` (about 2.5s) per output set,** and a producer loop runs only
  while that set has a subscriber.
- Idle pod memory is about 2 GiB total with `K=2`, so per-worker RSS is well under 1 GiB and CPU
  rather than memory is the constraint.

Capacity, taking "no-added-latency" concurrent evals as `replicas x cores/2` and assuming an
interactive user waits about `L` then thinks about 10s (`users = concurrent x (1 + T/L)`):

| replicas (K=2, 4-core) | concurrent evals | interactive users |
|---|---|---|
| 2 | about 4 | about 20 |
| 8 | about 16 | about 80 |

## Caveats

- Measured on one node, one screen (OTR3), with baseline inputs (`inputs={}`). Real `L` varies with
  track range, particle count and beam-loss cases.
- `L` depends on the model, the lattice and the hardware version. Re-measure after upgrades and
  when switching to a different hosted model.

## TODOs and follow-ups

- [ ] **Test with the CPU *limit* removed** (keep the request). The `c=4` blow-up in the history
      below looks like CFS throttling, and nodes are only about 11% utilized, so letting pods burst
      past their limit may raise throughput with no config change. Low risk, potentially
      significant.
- [ ] **Reduce `L`, the biggest lever.** Options: shorter track range, fewer tracked particles,
      caching results for repeated inputs, GPU, or a lighter surrogate.
- [ ] **Proper load test** with realistic, varied inputs (different screens, track ranges,
      beam-loss cases), reading `lume_evaluate_seconds` p50 and p95 from `/metrics`.
- [ ] **Test the `K=2, threads=1` combo** (untested) to confirm whether `threads=2` actually helps
      under contention or is just wasted.
- [ ] **Right-size pod memory** from real per-worker RSS under load. Idle is about 2 GiB for `K=2`,
      so the current 5 GiB request is likely generous.
- [ ] **Least-request load balancing** on the eval Service if a load test shows the round-robin LB
      skews bursts unevenly across replicas.
- [x] **Revisit the live-view cadence.** Answered by the 2026-08-24 measurement above: with no poll
      period the loop runs as fast as it can, which is one frame per `L` (about 2.5s) per output set,
      and only while someone is watching. No floor needed.
- [ ] **Re-run these measurements** after any model, lattice or hardware change.

## History: the superseded 2026-08-04 measurement

These numbers came from ad-hoc timings against an older image and are kept only to explain the
A/B conclusions above, which still hold. **The latency figures are superseded by the 2026-08-24
production numbers: they are roughly 2x too slow, so any capacity estimate derived from them is
about half of what the current model delivers.**

Method: port-forwarded straight to the running pod, bypassing the ingress, timing what was then
`POST /api/v1/evaluate` (now `POST /api/v1/models/cu_hxr_staged/evaluate`), sequential for a clean
`L` and then in small concurrent bursts. For the K and threads A/B, one throwaway Deployment
(`K=4`, `threads=1`, 4-core limit) against prod (`K=2`, `threads=2`, same 4 cores). The throwaway
pod was deleted afterwards.

Latency, sequential, n=20, prod config:

| min | p50 | p95 | mean |
|---|---|---|---|
| 4.48s | 5.03s | 6.75s | 5.18s |

K and threads A/B on the same 4-core budget:

| config | c=1 | c=2 | c=4 | c=8 | max throughput |
|---|---|---|---|---|---|
| `K=2 x 2 threads` (prod) | 5.0s | 6.3s | 8.2s | n/a | about 0.37 eval/s |
| `K=4 x 1 thread` (test) | 5.0s | n/a | 11.8s | 16.2s | about 0.33 eval/s |

What that established, and what still stands:

1. A single eval takes the same time regardless of `threads`, so there is no useful intra-eval
   parallelism.
2. `K=4` was worse than `K=2` on the same cores, in both latency and throughput, so a single eval
   effectively consumes about 2 cores even at `threads=1`. Library threads leak past the pins and
   the CPU limit throttles.
3. The sweet spot is about one worker per 2 cores, so `K=2` on a 4-core pod is right.

Related: [`SCALING.md`](./SCALING.md) for how to scale and the KEDA and Prometheus setup, and
[`../../docs/DEPLOY.md`](../../docs/DEPLOY.md) for pod sizing.
