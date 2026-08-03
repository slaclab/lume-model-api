"""Wire serialization for beam frames.

Large numeric arrays (image, phase-space scatter) are sent as base64-encoded
little-endian float32 bytes — the same shape the ai-lab frontend already decodes
via ``new Float32Array(bytes.buffer)``. Small arrays (Twiss, scalars) go as plain
JSON lists.
"""

from __future__ import annotations

import base64
from typing import Optional

import numpy as np


def encode_f32(array) -> str:
    """Base64-encode an array as little-endian float32 bytes."""
    arr = np.ascontiguousarray(np.asarray(array, dtype="<f4"))
    return base64.b64encode(arr.tobytes()).decode("ascii")


def encode_image(image) -> tuple[Optional[str], Optional[list[int]]]:
    """Return (base64 float32, [rows, cols]) for a 2D image, or (None, None)."""
    if image is None:
        return None, None
    arr = np.asarray(image, dtype="<f4")
    if arr.ndim != 2:
        arr = arr.reshape(arr.shape[0], -1)
    return encode_f32(arr), [int(arr.shape[0]), int(arr.shape[1])]


def to_list(array) -> Optional[list[float]]:
    if array is None:
        return None
    return [float(v) for v in np.asarray(array, dtype=float).ravel()]


def frame_to_wire(frame) -> dict:
    """Serialize a BeamFrame to the FrameResponse wire dict (JSON/pickle-safe).

    Done in the pool worker so large arrays are encoded once and cross the process
    boundary as compact base64 strings rather than raw numpy.
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
        "scatter_x_b64": None if frame.beam_x_um is None else encode_f32(frame.beam_x_um),
        "scatter_px_b64": None if frame.beam_px_evc is None else encode_f32(frame.beam_px_evc),
        "twiss_s": to_list(frame.twiss_s),
        "twiss_a_beta": to_list(frame.twiss_a_beta),
        "twiss_b_beta": to_list(frame.twiss_b_beta),
        "frame_index": int(frame.frame_index),
        "title_suffix": frame.title_suffix,
        "timestamp": float(frame.timestamp),
    }
