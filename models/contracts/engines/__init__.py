"""
Engine adapter registry.

Maps an engine name (e.g. "ollama") to its `InferenceEngine` implementation,
so callers pick an engine by config alone rather than importing a concrete
adapter directly. New engines register themselves here at import time.
"""
from __future__ import annotations

from models.contracts.engines.base import InferenceEngine
from models.contracts.engines.comfyui import ComfyUIEngine
from models.contracts.engines.ollama import OllamaEngine
from models.contracts.engines.whisper import WhisperEngine

ENGINES: dict[str, InferenceEngine] = {}


def register(engine: InferenceEngine) -> None:
    """Register `engine` under its `.name`, replacing any existing entry."""
    ENGINES[engine.name] = engine


def get_engine(name: str) -> InferenceEngine:
    """Return the registered engine for `name`.

    Raises `ValueError` if no engine is registered under that name.
    """
    try:
        return ENGINES[name]
    except KeyError:
        raise ValueError(f"Unknown inference engine: {name!r}") from None


register(OllamaEngine())
register(ComfyUIEngine())
register(WhisperEngine())
