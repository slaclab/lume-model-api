"""Wire serialization for beam frames.

Large numeric arrays (image, phase-space scatter) are sent as base64-encoded
little-endian float32 bytes — the same shape the ai-lab frontend already decodes
via ``new Float32Array(bytes.buffer)``. Small arrays (Twiss, scalars) go as plain
JSON lists.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

import numpy as np

from lume_visualizations.beam_monitor import SCATTER_DISPLAY_UNITS

# Beam images render onto a ~420px panel canvas, so full sensor resolution
# (e.g. 1392x1040) is ~10x more than is visible and dominates the frame payload
# (~7.7MB base64). Downsample so the longest side is at most this many pixels.
MAX_IMAGE_DIM = int(os.environ.get("LUME_MAX_IMAGE_DIM", "512"))


def encode_f32(array) -> str:
    """Base64-encode an array as little-endian float32 bytes."""
    arr = np.ascontiguousarray(np.asarray(array, dtype="<f4"))
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _downsample_image(arr: np.ndarray) -> np.ndarray:
    """Block-mean downsample a 2D image so its longest side <= MAX_IMAGE_DIM.

    Kept as float32 with raw intensities, so the client's robust/fixed/auto scaling
    is unchanged — only the resolution drops. Block-mean (area averaging) preserves
    the intensity distribution and avoids the aliasing that plain subsampling causes.
    """
    if MAX_IMAGE_DIM <= 0:
        return arr
    rows, cols = arr.shape
    factor = int(np.ceil(max(rows, cols) / MAX_IMAGE_DIM))
    if factor <= 1:
        return arr
    r = (rows // factor) * factor
    c = (cols // factor) * factor
    trimmed = arr[:r, :c]
    return trimmed.reshape(r // factor, factor, c // factor, factor).mean(axis=(1, 3))


def encode_image(image) -> tuple[Optional[str], Optional[list[int]]]:
    """Return (base64 float32, [rows, cols]) for a 2D image, or (None, None).

    The image is downsampled to display resolution (see MAX_IMAGE_DIM) before
    encoding to keep the frame payload small.
    """
    if image is None:
        return None, None
    arr = np.asarray(image, dtype="<f4")
    if arr.ndim != 2:
        arr = arr.reshape(arr.shape[0], -1)
    arr = _downsample_image(arr)
    return encode_f32(arr), [int(arr.shape[0]), int(arr.shape[1])]


def to_list(array) -> Optional[list[float]]:
    if array is None:
        return None
    return [float(v) for v in np.asarray(array, dtype=float).ravel()]


def frame_to_wire(frame) -> dict:
    """Serialize a BeamFrame to the FrameResponse wire dict (JSON/pickle-safe).

    Done in the pool worker so large arrays are encoded once and cross the process
    boundary as compact base64 strings rather than raw numpy.

    KEEP THIS UNCONDITIONAL. Every FrameResponse key must be present on every call. The
    SSE stream json.dumps this dict without validating it against FrameResponse (see
    main.py live_stream), so a conditionally-omitted key would reach the browser absent,
    and the frontend types it as Required<FrameResponse> in api/client.ts. Adding a
    conditional key here breaks that silently, with no type error to catch it. Use the
    frame_to_v1_wire pattern below only for the v1 endpoint, which FastAPI does validate.
    """
    image_b64, image_shape = encode_image(frame.image)
    return {
        "screen_key": frame.screen_key,
        "screen_label": frame.screen_label,
        "image_b64": image_b64,
        "image_shape": image_shape,
        "image_message": frame.image_message,
        "image_caption": frame.image_caption,
        "scalars": {
            "xrms_um": float(frame.xrms_um),
            "yrms_um": float(frame.yrms_um),
            "sigma_z_um": float(frame.sigma_z_um),
            "norm_emit_x_um_rad": float(frame.norm_emit_x_um_rad),
            "norm_emit_y_um_rad": float(frame.norm_emit_y_um_rad),
        },
        "scatter_b64": (
            None
            if frame.scatter is None
            else {k: encode_f32(v) for k, v in frame.scatter.items()}
        ),
        "scatter_units": (
            None
            if frame.scatter is None
            else {k: SCATTER_DISPLAY_UNITS.get(k, "") for k in frame.scatter}
        ),
        "twiss_s": to_list(frame.twiss_s),
        "twiss_a_beta": to_list(frame.twiss_a_beta),
        "twiss_b_beta": to_list(frame.twiss_b_beta),
        "frame_index": int(frame.frame_index),
        "title_suffix": frame.title_suffix,
        "timestamp": float(frame.timestamp),
    }


def frame_to_v1_wire(
    frame,
    include_image: bool = False,
    include_distribution: bool = False,
    include_twiss: bool = False,
) -> dict:
    """Serialize a BeamFrame to the /api/v1/evaluate wire dict.

    Scalars are always included; the heavy outputs are opt-in. Model + version are
    added by the endpoint. Done in the pool worker so arrays cross the process
    boundary already base64-encoded.
    """
    out: dict = {
        "screen": frame.screen_key,
        "frame_index": int(frame.frame_index),
        "timestamp": float(frame.timestamp),
        "scalars": {
            "xrms_um": float(frame.xrms_um),
            "yrms_um": float(frame.yrms_um),
            "sigma_z_um": float(frame.sigma_z_um),
            "norm_emit_x_um_rad": float(frame.norm_emit_x_um_rad),
            "norm_emit_y_um_rad": float(frame.norm_emit_y_um_rad),
        },
        "image": None,
        "distribution": None,
        "twiss": None,
    }
    if include_image and frame.image is not None:
        image_b64, image_shape = encode_image(frame.image)
        out["image"] = {"shape": image_shape, "dtype": "float32", "data_b64": image_b64}
    if include_distribution and frame.distribution:
        out["distribution"] = {
            "n": int(frame.distribution["n"]),
            "units": frame.distribution["units"],
            "coords": {k: encode_f32(v) for k, v in frame.distribution["coords"].items()},
        }
    if include_twiss and frame.twiss_s is not None:
        out["twiss"] = {
            "s": to_list(frame.twiss_s),
            "beta_x": to_list(frame.twiss_a_beta),
            "beta_y": to_list(frame.twiss_b_beta),
        }
    return out
