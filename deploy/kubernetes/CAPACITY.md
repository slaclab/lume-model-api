# Capacity & performance findings

Measured 2026-08-04 against the deployed `cu_hxr_staged` model on the S3DF cluster
(`ad-accel-online-ml`). Purpose: pin per-eval latency `L`, find the right worker
count `K` per pod, and estimate how many concurrent users we can serve.

## TL;DR

- **`L ≈ 5s` per eval** (p50), and it's **independent of thread count**. This is the
  real bottleneck, not pod count.
- **Current pod tuning is already near-optimal.** Keep ~2 CPU cores per worker
  (`K = cores/2`). Do **not** pack more workers per pod — it makes things worse.
- **Concurrent no-added-latency evals ≈ `replicas × cores/2`.** Reaching ~100
  concurrent users at today's `L` needs ~16 pods, so **cutting `L` is higher-leverage
  than scaling.**

## How it was measured

- Port-forwarded straight to the running pod (bypassing the ingress) and timed
  `POST /api/evaluate` — sequential for clean `L`, then small concurrent bursts.
- For the K/threads A/B: one throwaway Deployment (`K=4, threads=1`, 4-core limit)
  compared against prod (`K=2, threads=2`, same 4 cores). Throwaway pod deleted after.
- Idle pod memory ≈ 2 GiB total with `K=2` (so **CPU, not memory, is the constraint**;
  per-worker RSS is well under 1 GiB).

## Latency `L`

Sequential, n=20, prod config (`K=2`, `threads=2`, 4 cores):

| min | p50 | p95 | mean |
|---|---|---|---|
| 4.48s | **5.03s** | 6.75s | 5.18s |

## K / threads A/B (same 4-core budget)

| config | c=1 | c=2 | c=4 | c=8 | max throughput |
|---|---|---|---|---|---|
| `K=2 × 2 threads` (prod) | 5.0s | 6.3s | 8.2s | — | **~0.37 eval/s** |
| `K=4 × 1 thread` (test) | 5.0s | — | 11.8s | 16.2s | ~0.33 eval/s |

**Findings:**
1. A single eval is ~5s regardless of `threads` → no useful intra-eval parallelism.
2. `K=4` was **worse** than `K=2` on the same cores (higher latency, lower
   throughput) → a single eval effectively consumes **~2 cores** even with
   `threads=1` (library threads leak past the pins and/or the CPU limit throttles).
3. Sweet spot ≈ **1 worker per 2 cores**. `K=2` on a 4-core pod (current) is right.

## Capacity estimate

"No-added-latency" concurrent evals ≈ `replicas × cores/2`. Interactive users assume
each waits ~`L` then thinks ~10s (`users ≈ concurrent × (1 + T/L)`, ~3×):

| replicas (K=2, 4-core) | concurrent evals | interactive users |
|---|---|---|
| 2 | ~4 | ~12 |
| 8 | ~16 | ~48 |

At `L ≈ 5s` there is no snappy real-time interaction — every slider change costs ~5s.
Scaling adds *more* concurrent 5s-evals; it doesn't make them faster.

## Caveats

- Numbers come from one node, one screen (OTR3), and baseline inputs (`inputs={}`).
  Real `L` will vary with track range, particle count, and beam-loss cases — treat
  p95 here as indicative, not final.
- `L` is model/lattice/hardware-version dependent; re-measure after upgrades.

## TODOs / follow-ups

- [ ] **Test with the CPU *limit* removed** (keep the request). The `c=4 → 11.8s`
      blowup looks like CFS throttling, and nodes are only ~11% utilized — letting
      pods burst past their limit may raise throughput with no config change. Low-risk,
      potentially significant.
- [ ] **Reduce `L` — the biggest lever.** Options: shorter track range, fewer tracked
      particles, cache results for repeated inputs, GPU, or a lighter surrogate.
- [ ] **Proper load test** with realistic, *varied* inputs (different screens, track
      ranges, beam-loss cases) and read `lume_evaluate_seconds` p50/p95 from `/metrics`
      rather than the ad-hoc timings here.
- [ ] **Test the `K=2, threads=1` combo** (untested) to confirm whether `threads=2`
      actually helps under contention or is just wasted.
- [ ] **Right-size pod memory** from real per-worker RSS under load (idle ≈ 2 GiB for
      `K=2`); current 5 GiB request is likely generous.
- [ ] **Least-request load balancing** on the eval Service if a load test shows the
      round-robin LB skews bursts unevenly across replicas.
- [ ] **Revisit the live-view cadence.** With the poll period removed it runs
      as-fast-as-possible ≈ one frame / `L` (~1 frame / 5s per screen) — confirm that's
      acceptable, or add a floor.
- [ ] **Re-run these measurements** after any model / lattice / hardware change.

Related: [`SCALING.md`](./SCALING.md) (how to scale, KEDA/Prometheus setup).
