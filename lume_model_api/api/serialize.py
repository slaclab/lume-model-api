"""Wire serialization for evaluate results.

Runs inside the pool worker, so arrays cross the process boundary already base64-encoded
little-endian float32 rather than as pickled numpy. Decoded in the browser via
``new Float32Array(bytes.buffer)`` and in Python via
``numpy.frombuffer(base64.b64decode(s), dtype="<f4")``.

Units travel with the data in every kind, so no client hard-codes them.
"""

from __future__ import annotations

import base64
import math
import os
import time

import numpy as np

# 2-D arrays are usually screen images, rendered onto a panel a few hundred pixels wide, so
# full sensor resolution (e.g. 1392x1040) is ~10x more than is visible and dominates the
# payload (~7.7MB base64). Downsample so the longest side is at most this many pixels.
MAX_IMAGE_DIM = int(os.environ.get("LUME_MAX_IMAGE_DIM", "512"))


def encode_f32(array) -> str:
    """Base64-encode an array as little-endian float32 bytes."""
    arr = np.ascontiguousarray(np.asarray(array, dtype="<f4"))
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _downsample_image(arr: np.ndarray) -> np.ndarray:
    """Block-mean downsample a 2D image so its longest side <= MAX_IMAGE_DIM.

    Kept as float32 with raw intensities, so a client's robust/fixed/auto scaling is
    unchanged and only the resolution drops. Block-mean (area averaging) preserves the
    intensity distribution and avoids the aliasing that plain subsampling causes.

    EVERY input pixel reaches the output. An earlier version trimmed the image to a whole
    multiple of the block factor, which discarded up to `factor - 1` rows and columns off the
    trailing edge with nothing on the wire saying so: a 1040x1392 sensor lost 2 rows, a 513x513
    one lost a row and a column, and a beam or a defect sitting there simply vanished. The
    trailing partial block is padded instead, and each block is averaged over the real pixels it
    actually covers, so a short edge block is still a true mean and the intensity-preserving
    block-mean semantics the docstring promises hold everywhere, edge included.
    """
    if MAX_IMAGE_DIM <= 0:
        return arr
    rows, cols = arr.shape
    factor = int(np.ceil(max(rows, cols) / MAX_IMAGE_DIM))
    if factor <= 1:
        return arr
    out_rows = -(-rows // factor)  # ceiling division: the partial block gets its own output row
    out_cols = -(-cols // factor)
    pad = ((0, out_rows * factor - rows), (0, out_cols * factor - cols))
    # Padded with zeros for the sums and with zeros in a parallel weight array, which is what
    # keeps the padding out of the average: it contributes nothing to either the numerator or
    # the denominator, so it cannot dim an edge block the way a plain zero-padded mean would.
    blocked = (out_rows, factor, out_cols, factor)
    # Accumulated in float64 even though the input is float32, because a block sum of many
    # single-count pixels is exactly where float32 rounding shows up as lost intensity.
    sums = np.pad(arr, pad).reshape(blocked).sum(axis=(1, 3), dtype=np.float64)
    counts = np.pad(np.ones_like(arr, dtype=np.float64), pad).reshape(blocked).sum(axis=(1, 3))
    means = sums / counts
    # `np.mean` on an integer array promotes to float64, so preserve that rather than casting an
    # average back into an integer dtype and truncating it. The only caller hands this float32.
    return means.astype(arr.dtype, copy=False) if arr.dtype.kind == "f" else means


def _smooth(arr: np.ndarray, sigma_px: float) -> np.ndarray:
    """Convolve a 2-D array with a Gaussian.

    The filter conserves the array's sum, so the result is still in the declared unit and a
    client's own intensity scaling keeps working. No renormalization on purpose.

    scipy.ndimage is imported here rather than at module top because only this opt-in path
    needs it, and this module is imported by every worker on every startup.
    """
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(np.asarray(arr, dtype=float), sigma=sigma_px)


def serialize_array(entry: dict) -> dict:
    arr = np.asarray(entry["array"])
    # Smoothing and downsampling only make sense for images. Everything else (Twiss curves,
    # 1-D scans, n-D tensors) ships at full resolution with its shape declared, and the
    # client reshapes the flat float32 buffer.
    if arr.ndim == 2:
        sigma = entry.get("smooth_sigma_px")
        if sigma:
            arr = _smooth(arr, float(sigma))
        arr = _downsample_image(np.asarray(arr, dtype="<f4"))
    return {
        "kind": "array",
        "shape": [int(dim) for dim in arr.shape],
        "dtype": "float32",
        "data_b64": encode_f32(arr),
        "unit": entry.get("unit", ""),
    }


def serialize_particles(entry: dict) -> dict:
    return {
        "kind": "particles",
        "n": int(entry["n"]),
        "units": dict(entry["units"]),
        "coords": {name: encode_f32(values) for name, values in entry["coords"].items()},
        "stats": {name: float(value) for name, value in entry["stats"].items()},
        "stats_units": dict(entry["stats_units"]),
    }


def serialize_output(entry: dict) -> dict:
    kind = entry["kind"]
    if kind == "scalar":
        wire = {"kind": "scalar", "value": float(entry["value"]), "unit": entry.get("unit", "")}
    elif kind == "array":
        wire = serialize_array(entry)
    elif kind == "particles":
        wire = serialize_particles(entry)
    else:
        wire = {"kind": "value", "value": entry.get("value")}
    # Added here rather than in each branch, so a kind cannot ship without it. Unconditional and
    # defaulted for the same reason every other key is: the SSE path has no response_model, so an
    # absent key reaches a streaming client genuinely missing and no consumer can detect it.
    wire["variable_class"] = entry.get("variable_class", "")
    return wire


def serialize_outputs(outputs: dict) -> dict:
    return {name: serialize_output(entry) for name, entry in outputs.items()}


def json_safe(value):
    """Replace non-finite floats with None, recursively, so `json.dumps` emits valid JSON.

    NaN and infinity are routine here: a solver that did not converge, `norm_emit_x` on a
    degenerate beam, a CA record that reads back as NaN. `json.dumps` renders them as the
    literals `NaN` and `Infinity`, which are not JSON, so a browser's `JSON.parse` throws and
    the whole frame is lost rather than one field.

    Applied on the SSE path only. The HTTP route's `response_model` already maps a non-finite
    float to `null`, so doing this here is what makes the two paths agree byte for byte. It
    must NOT move into `result_to_wire`: that runs on both paths, and `ScalarOutput.value` is a
    required `float`, so feeding None to the response model would turn a NaN into a 500.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def result_to_wire(result, frame_index: int = 0) -> dict:
    """Serialize an `EvaluateResult` to the evaluate wire dict.

    The ONE wire shape, used by every UI, the SSE live stream and programmatic callers
    alike. `model` and `version` are added by the sender, because the SSE stream bypasses
    the HTTP endpoint (see live_hub).

    EVERY KEY MUST BE PRESENT ON EVERY CALL, and every requested output id must appear in
    `outputs`, never omitted. The SSE stream json.dumps this dict without validating it
    against the response model (see main.py live_stream), so a conditionally-omitted key
    reaches the client genuinely missing. Clients generate their stream types from
    EvaluateV1Response and treat every key as present, because nothing on that path can tell
    them otherwise, and a dropped key does not change openapi.json so no consumer can detect
    it by refetching the schema. tests/test_wire_shape.py enforces this.
    """
    return {
        "timestamp": time.time(),
        "frame_index": int(frame_index),
        "inputs": dict(result.inputs),
        "outputs": serialize_outputs(result.outputs),
    }
