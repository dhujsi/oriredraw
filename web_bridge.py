"""Small JSON-safe bridge shared by Flask-free browser builds."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

import numpy as np

from reconstructor import (
    Settings,
    _decode_image,
    _png_data_uri,
    prepare_paper_square,
    reconstruct,
)


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
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Run the normal reconstructor and omit native image arrays from the result."""
    settings = Settings.from_mapping(dict(settings_mapping or {}))
    result = reconstruct(
        image_bytes,
        settings=settings,
        progress_callback=progress_callback,
    )
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


def reconstruct_for_web_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    payload = reconstruct_for_web(
        image_bytes,
        json.loads(settings_json or "{}"),
        progress_callback=progress_callback,
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def rectify_for_web(image_bytes: bytes, corners: list[list[float]]) -> dict[str, Any]:
    """Return a high-resolution square PNG from four ordered corner points."""
    image = _decode_image(image_bytes)
    points = np.asarray(corners, dtype=np.float32)
    if points.shape == (4, 2) and float(np.max(np.abs(points))) <= 1.000001:
        source_points = points * np.array(
            [image.shape[1] - 1, image.shape[0] - 1], dtype=np.float32
        )
    else:
        source_points = points
    if source_points.shape != (4, 2):
        output_size = 1024
    else:
        sides = [
            float(np.linalg.norm(source_points[(index + 1) % 4] - source_points[index]))
            for index in range(4)
        ]
        output_size = int(
            np.clip(
                round(max((sides[0] + sides[2]) / 2.0, (sides[1] + sides[3]) / 2.0)),
                256,
                4096,
            )
        )
    square, bounds, stats = prepare_paper_square(
        image,
        output_size,
        paper_corners=corners,
    )
    return {
        "image_data_uri": _png_data_uri(square),
        "width": int(square.shape[1]),
        "height": int(square.shape[0]),
        "paper_bbox": list(bounds),
        "stats": stats,
    }


def rectify_for_web_json(image_bytes: bytes, corners_json: str) -> str:
    payload = rectify_for_web(image_bytes, json.loads(corners_json))
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
