#!/usr/bin/env python3
"""Scan QUAD:IN20:525:BCTRL and watch OTR3 respond, against a running deployment.

An example of driving the API from a notebook or a script: one POST per setpoint, reading the
beam image, the emittances, the beam sizes and the x-px phase space out of each response.

Usage:
    python scripts/quad_scan_demo.py                 # scan, print a table, write a PNG
    python scripts/quad_scan_demo.py --steps 9
    LUME_API=http://localhost:8000 python scripts/quad_scan_demo.py

Needs requests, numpy and (for the figure) matplotlib. Nothing from this package: it talks to
the service over HTTP exactly as any other client would.
"""

from __future__ import annotations

import argparse
import base64
import os

import numpy as np
import requests

API = os.environ.get(
    "LUME_API", "https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api"
)
MODEL = "cu_hxr_staged"
KNOB = "QUAD:IN20:525:BCTRL"
SCREEN = "OTR3"


def decode(b64: str) -> np.ndarray:
    """Every large array on the wire is base64 little-endian float32."""
    return np.frombuffer(base64.b64decode(b64), dtype="<f4")


def evaluate(value: float, max_particles: int = 3000) -> dict:
    """One setpoint. `screen` appends this screen's beam id and its image id."""
    response = requests.post(
        f"{API}/api/v1/models/{MODEL}/evaluate",
        json={
            "inputs": {KNOB: value},
            "screen": SCREEN,
            "max_particles": max_particles,
            # A screen image built from ~1000 macroparticles is single-count noise at pixel
            # resolution, so convolve with the detector PSF to get something camera-like.
            "smooth_images_sigma_px": 2.0,
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--out", default="quad_scan.png")
    args = parser.parse_args()

    # Read the knob's real limits rather than hard-coding them, and scan the middle half of the
    # range so no setpoint is rejected at the boundary.
    config = requests.get(f"{API}/api/v1/models/{MODEL}/config", timeout=60).json()
    knob = next(item for item in config["inputs"] if item["id"] == KNOB)
    screen = next(item for item in config["screens"] if item["key"] == SCREEN)
    span = knob["max"] - knob["min"]
    setpoints = np.linspace(knob["min"] + span / 4, knob["max"] - span / 4, args.steps)

    print(f"{KNOB}  default {knob['default']:.3f} {knob['unit']}, "
          f"range [{knob['min']:.3f}, {knob['max']:.3f}]")
    print(f"{SCREEN}: beam={screen['particles']}  image={screen['image']}\n")
    header = f"{KNOB} [{knob['unit']}]"
    print(f"{header:>22}  {'sigma_x [um]':>12}  {'sigma_y [um]':>12}"
          f"  {'nemit_x [um]':>12}  {'nemit_y [um]':>12}")

    frames = []
    for value in setpoints:
        body = evaluate(float(value))
        beam = body["outputs"][screen["particles"]]
        image = body["outputs"][screen["image"]]
        stats = beam["stats"]  # computed on the FULL beam, before subsampling to max_particles
        # The API reports the model's own units, metres here, and converts nothing. Scale for
        # display only, and read the unit off the payload rather than assuming it.
        frames.append(
            {
                "value": float(value),
                "sigma_x": stats["sigma_x"] * 1e6,
                "sigma_y": stats["sigma_y"] * 1e6,
                "nemit_x": stats["norm_emit_x"] * 1e6,
                "nemit_y": stats["norm_emit_y"] * 1e6,
                "x": decode(beam["coords"]["x"]) * 1e6,
                "px": decode(beam["coords"]["px"]),
                # Reshape to the shape in the RESPONSE: 2-D arrays are downsampled, so it is
                # smaller than the shape the config declares.
                "image": decode(image["data_b64"]).reshape(image["shape"]),
            }
        )
        row = frames[-1]
        print(f"{row['value']:>22.4f}  {row['sigma_x']:>12.2f}  {row['sigma_y']:>12.2f}"
              f"  {row['nemit_x']:>12.4f}  {row['nemit_y']:>12.4f}")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib not installed, skipping the figure.")
        return

    columns = len(frames)
    figure, axes = plt.subplots(3, columns, figsize=(3.1 * columns, 8.6), squeeze=False)
    for column, row in enumerate(frames):
        axes[0][column].imshow(row["image"], cmap="inferno", aspect="auto")
        axes[0][column].set_title(f"{row['value']:.3f} {knob['unit']}", fontsize=9)
        axes[0][column].set_xticks([])
        axes[0][column].set_yticks([])

        axes[1][column].scatter(row["x"], row["px"], s=1, alpha=0.3, linewidths=0)
        axes[1][column].set_xlabel("x [um]", fontsize=8)

        axes[2][column].bar(
            ["sig_x", "sig_y", "ne_x", "ne_y"],
            [row["sigma_x"], row["sigma_y"], row["nemit_x"], row["nemit_y"]],
        )
        axes[2][column].tick_params(labelsize=8)

    axes[0][0].set_ylabel(f"{SCREEN} image")
    axes[1][0].set_ylabel("px [eV/c]")
    axes[2][0].set_ylabel("um, um-rad")
    # Shared limits, so the columns are comparable rather than each autoscaled to itself.
    for plot_row, key in ((axes[1], "px"), ):
        low = min(frame[key].min() for frame in frames)
        high = max(frame[key].max() for frame in frames)
        for plot in plot_row:
            plot.set_ylim(low, high)
    x_low = min(frame["x"].min() for frame in frames)
    x_high = max(frame["x"].max() for frame in frames)
    for plot in axes[1]:
        plot.set_xlim(x_low, x_high)
    scalar_high = max(max(frame[k] for k in ("sigma_x", "sigma_y")) for frame in frames)
    for plot in axes[2]:
        plot.set_ylim(0, scalar_high * 1.1)

    figure.tight_layout()
    figure.savefig(args.out, dpi=110)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
