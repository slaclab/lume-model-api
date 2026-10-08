#!/usr/bin/env python3
"""Acceptance test for the poisoned-worker failure in docs/POISONED_WORKER.md.

Drives a deployment through the reproduction and reports whether it behaves as the broken service
does today or as a fixed one should. Run it before and after the fix.

    python scripts/check_poisoned_worker.py                       # against dev
    LUME_API=http://localhost:8000 python scripts/check_poisoned_worker.py

Exit status is 0 when the service behaves correctly, 1 when it shows the bug. That is deliberately
inverted relative to "the test passed": today this script is expected to exit 1.

THIS BREAKS THE DEPLOYMENT IT RUNS AGAINST. The poisoned worker cannot be recovered in-process, so
a pod restart is required afterwards unless the fix is in place and the probes do it for you:

    kubectl rollout restart deploy/lume-model-api-eval -n lume-model-api
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

API = os.environ.get(
    "LUME_API", "https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api"
)
MODEL = os.environ.get("LUME_MODEL_NAME", "cu_hxr_staged")

# Setting a bend ~2% or more off its startup value poisons exactly one worker. This value is inside
# the range the service publishes for it ([0.11, 0.33], i.e. default +/- 50%), so validating the
# published range would not filter it. The magnet's own startup value does NOT poison, and neither
# do large in-range excursions on quads, so the trigger is specific to moving a bend.
POISON = {"BEND:LI21:215:BCTRL": 0.209}
# A readback on one of those magnets. Any output would do; this one makes the log readable.
PROBE_OUTPUT = "QUAD:IN20:525:BACT"

TIMEOUT = 300


def evaluate(inputs: dict) -> requests.Response:
    return requests.post(
        f"{API}/api/v1/models/{MODEL}/evaluate",
        json={"inputs": inputs, "outputs": [PROBE_OUTPUT], "max_particles": 5},
        timeout=TIMEOUT,
    )


def describe(response: requests.Response) -> str:
    if response.status_code == 200:
        try:
            return f"200 {PROBE_OUTPUT}={response.json()['outputs'][PROBE_OUTPUT]['value']:.6g}"
        except Exception:
            return "200"
    try:
        return f"{response.status_code} {response.json().get('detail', '')[:88]}"
    except Exception:
        return f"{response.status_code} {response.text[:88]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeats",
        type=int,
        default=12,
        help="empty-input probes per round. One poisoned worker of K fails only 1/K of requests, so "
        "a handful of attempts can miss it entirely. Keep this well above the worker count.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="poison-then-probe rounds. Each round takes out one more worker, so the failing "
        "fraction should climb in steps. A single round understates the damage.",
    )
    args = parser.parse_args()

    print(f"API   {API}")
    print(f"model {MODEL}\n")

    print("1. healthy baseline, empty inputs")
    before = evaluate({})
    print(f"   {describe(before)}")
    if before.status_code != 200:
        print("\n   Already failing before the poison step, so this run proves nothing.")
        print("   Restart the deployment and try again:")
        print("     kubectl rollout restart deploy/lume-model-api-eval -n lume-model-api")
        return 2

    print(f"\n   baseline: {args.repeats} empty-input requests")
    clean = [evaluate({}) for _ in range(args.repeats)]
    clean_bad = sum(1 for r in clean if r.status_code != 200)
    print(f"   {clean_bad}/{args.repeats} failing")
    if clean_bad:
        print("\n   Workers were already poisoned before this run, so it proves nothing.")
        print("     kubectl rollout restart deploy/lume-model-api-eval -n lume-model-api")
        return 2

    # Each round poisons one more worker. The failing fraction should climb in discrete steps and
    # never recover, which is what distinguishes a per-worker latch from a transient fault.
    print(f"\n2. poison, then probe, {args.rounds} rounds. Poison: {POISON}")
    after: list[requests.Response] = []
    poison_statuses = []
    fractions = []
    for round_index in range(args.rounds):
        poisoned = evaluate(POISON)
        poison_statuses.append(poisoned.status_code)
        probes = [evaluate({}) for _ in range(args.repeats)]
        after.extend(probes)
        bad = sum(1 for r in probes if r.status_code != 200)
        fractions.append(bad / len(probes))
        print(f"   round {round_index + 1}: poison -> {describe(poisoned)}")
        print(f"            empty-input probes failing: {bad}/{len(probes)}")

    print(f"\n   failing fraction by round: "
          f"{', '.join(f'{f:.0%}' for f in fractions)}  (0% before any poison)")

    print("\n3. health")
    health = requests.get(f"{API}/healthz", timeout=60)
    print(f"   {health.status_code} {health.text[:88]}")

    # A 400 on a request with no inputs is the defect: the service is blaming the caller for its own
    # broken state. 503 is correct, whether or not the model recovered.
    wrongly_blamed = [r for r in after if r.status_code == 400]
    print("\n--- verdict ---")
    broken = False

    if len(set(poison_statuses)) > 1:
        print(f"NOTE: the poisoning request returned {poison_statuses} across identical requests. "
              "That alternation is itself the bug: the status depends on whether the worker it "
              "landed on was already poisoned.")

    if wrongly_blamed:
        print(f"BUG: {len(wrongly_blamed)}/{len(after)} empty-input requests returned 400.")
        print("     A request that sets nothing cannot be a client error. Expected 503.")
        broken = True
    elif all(r.status_code == 200 for r in after):
        print("OK:  every empty-input request succeeded, so no worker was left broken.")
    else:
        codes = sorted({r.status_code for r in after})
        print(f"OK:  empty-input requests returned {codes}, not 400, so the fault is not "
              "being attributed to the caller.")

    if 500 in poison_statuses:
        print("BUG: a poisoning request returned 500, an unhandled exception. Expected 503 "
              "naming the control that failed to apply.")
        broken = True
    elif 400 in poison_statuses:
        print("NOTE: the poisoning request returned 400. Defensible on its own, since these values "
              "do destabilize the model, but it must not leave later requests failing.")
    elif set(poison_statuses) == {503}:
        print("OK:  the poisoning request returned 503, so the fault was reported as a server "
              "fault.")

    if health.status_code == 200 and (wrongly_blamed or any(r.status_code >= 500 for r in after)):
        print("BUG: /healthz reports ok while evaluates fail, so the k8s probes will not restart "
              "the pod and this state persists until a human notices.")
        broken = True
    elif health.status_code != 200:
        print(f"OK:  /healthz reports {health.status_code}, so the probes will restart the pod.")

    print()
    if broken:
        print("This deployment shows the bug in docs/POISONED_WORKER.md.")
        print("Restart it: kubectl rollout restart deploy/lume-model-api-eval -n lume-model-api")
        return 1
    print("This deployment handles the failure correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
