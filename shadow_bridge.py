"""Browser wrapper that appends v2 shadow diagnostics without changing v1 output."""

from __future__ import annotations

import json
from typing import Any, Callable

from shadow_evidence import attach_observed_offsets
from shadow_geometry_v2 import build_geometry_shadow_report_v2
from shadow_variant import (
    build_shadow_candidate_variant,
    refine_trace_offsets_from_cp,
)
from web_bridge import reconstruct_for_web, rectify_for_web_json


def reconstruct_for_web_shadow_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    settings_mapping = json.loads(settings_json or "{}")
    payload = reconstruct_for_web(
        image_bytes,
        settings_mapping,
        progress_callback=progress_callback,
    )
    try:
        refined_offsets = refine_trace_offsets_from_cp(payload)
        estimates = attach_observed_offsets(
            image_bytes,
            settings_mapping,
            payload,
        )
        report = build_geometry_shadow_report_v2(payload)
        report["ridge_estimates"] = len(estimates)
        report["precision_rebound_output_rays"] = refined_offsets
        payload["shadow_search"] = report
    except Exception as error:  # Shadow diagnostics must never break production output.
        payload["shadow_search"] = {
            "enabled": False,
            "mode": "shadow_geometry_v2",
            "output_unchanged": True,
            "reason": "shadow_error",
            "error": str(error),
        }
    else:
        if settings_mapping.get("construction_variants", True):
            try:
                variant = build_shadow_candidate_variant(
                    image_bytes,
                    settings_mapping,
                    payload,
                    report,
                )
            except Exception as error:  # Candidate rendering is even more isolated.
                report["candidate_variant_emitted"] = False
                report["candidate_variant_error"] = str(error)
            else:
                if variant is not None:
                    payload.setdefault("variants", []).append(variant)
                    report["candidate_variant_emitted"] = True
                    report["candidate_variant_id"] = variant["id"]
                else:
                    report["candidate_variant_emitted"] = False
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
