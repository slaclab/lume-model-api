"""LUME_WORKER_THREADS has to win over the image's own *_NUM_THREADS defaults."""

from __future__ import annotations

import os

import pytest

THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "TORCH_NUM_THREADS")


def _init_threads_only(monkeypatch):
    from lume_model_api.api import pool

    # Stop _init_worker before it builds a model: the thread setup is all this tests.
    monkeypatch.setattr(os, "chdir", lambda path: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        pool._init_worker("unused:unused", {}, "unused")


def test_worker_threads_overrides_preset_thread_vars(monkeypatch) -> None:
    for var in THREAD_VARS:
        monkeypatch.setenv(var, "2")
    monkeypatch.setenv("LUME_WORKER_THREADS", "3")
    _init_threads_only(monkeypatch)
    assert {os.environ[var] for var in THREAD_VARS} == {"3"}


def test_unset_worker_threads_keeps_preset_values(monkeypatch) -> None:
    for var in THREAD_VARS:
        monkeypatch.setenv(var, "2")
    monkeypatch.delenv("LUME_WORKER_THREADS", raising=False)
    _init_threads_only(monkeypatch)
    assert {os.environ[var] for var in THREAD_VARS} == {"2"}


def test_unset_everything_falls_back_to_one(monkeypatch) -> None:
    for var in THREAD_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("LUME_WORKER_THREADS", raising=False)
    _init_threads_only(monkeypatch)
    assert {os.environ[var] for var in THREAD_VARS} == {"1"}
