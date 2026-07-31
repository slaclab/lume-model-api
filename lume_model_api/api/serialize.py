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
