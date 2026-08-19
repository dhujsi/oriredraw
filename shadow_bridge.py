"""Browser wrapper that appends v2 shadow-search diagnostics without changing v1 output."""

from __future__ import annotations

import json
from typing import Any, Callable

from shadow_search import build_shadow_report
from web_bridge import reconstruct_for_web, rectify_for_web_json


def reconstruct_for_web_shadow_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    payload = reconstruct_for_web(
        image_bytes,
        json.loads(settings_json or "{}"),
        progress_callback=progress_callback,
    )
    try:
        payload["shadow_search"] = build_shadow_report(payload)
    except Exception as error:  # Shadow mode must never break the production result.
        payload["shadow_search"] = {
            "enabled": False,
            "mode": "shadow",
            "output_unchanged": True,
            "reason": "shadow_error",
            "error": str(error),
        }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


__all__ = ["reconstruct_for_web_shadow_json", "rectify_for_web_json"]
