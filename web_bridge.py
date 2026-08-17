"""Small JSON-safe bridge shared by Flask-free browser builds."""

from __future__ import annotations

import json
from typing import Any, Mapping

import numpy as np

from reconstructor import Settings, reconstruct


_PUBLIC_RESULT_KEYS = (
    "cp",
    "stats",
    "anchors",
    "warnings",
    "id",
    "label",
    "constructions",
    "variants",
    "overlay_data_uri",
    "reconstruction_data_uri",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def reconstruct_for_web(
    image_bytes: bytes, settings_mapping: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Run the normal reconstructor and omit native image arrays from the result."""
    settings = Settings.from_mapping(dict(settings_mapping or {}))
    result = reconstruct(image_bytes, settings=settings)
    optional_defaults = {
        "id": "strict",
        "label": "严格 22.5°",
        "constructions": [],
        "variants": [],
    }
    return {
        key: result.get(key, optional_defaults[key])
        if key in optional_defaults
        else result[key]
        for key in _PUBLIC_RESULT_KEYS
    }


def reconstruct_for_web_json(image_bytes: bytes, settings_json: str) -> str:
    payload = reconstruct_for_web(image_bytes, json.loads(settings_json or "{}"))
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
