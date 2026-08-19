"""Browser wrapper that appends construction-search diagnostics and variants."""

from __future__ import annotations

import json
from typing import Any, Callable

from provenance_v6 import build_provenance_report_v6
from quality_v5 import build_quality_report_v5
from shadow_evidence import attach_observed_offsets
from shadow_geometry_v2 import build_geometry_shadow_report_v2
from shadow_variant import refine_trace_offsets_from_cp
from shadow_variant_v6 import build_shadow_candidate_variant_v6
from web_bridge import reconstruct_for_web, rectify_for_web_json


_STRICT_PROGRESS_MAX = 72


def _map_strict_progress(percent: int | float) -> int:
    value = max(0.0, min(100.0, float(percent)))
    return int(round(value * _STRICT_PROGRESS_MAX / 100.0))


def reconstruct_for_web_shadow_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    settings_mapping = json.loads(settings_json or "{}")

    def report(percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(percent), str(message))

    def strict_progress(percent: int, message: str) -> None:
        mapped = _map_strict_progress(percent)
        if float(percent) >= 100.0:
            report(mapped, "基础重建完成，正在整理构造轨迹…")
        else:
            report(mapped, message)

    payload = reconstruct_for_web(
        image_bytes,
        settings_mapping,
        progress_callback=strict_progress,
    )
    report(74, "正在整理构造轨迹…")
    try:
        report(76, "正在精化输出几何…")
        refined_offsets = refine_trace_offsets_from_cp(payload)
        report(80, "正在读取原图折痕证据…")
        estimates = attach_observed_offsets(
            image_bytes,
            settings_mapping,
            payload,
        )
        report(84, "正在检查局部几何与结构…")
        local_report = build_geometry_shadow_report_v2(payload)
        quality_report = build_quality_report_v5(payload)
        report(88, "正在搜索替代构造与去核心参考点…")
        provenance_report = build_provenance_report_v6(
            payload,
            quality_report=quality_report,
            geometry_report=local_report,
        )
        local_report["ridge_estimates"] = len(estimates)
        local_report["precision_rebound_output_rays"] = refined_offsets
        local_report["quality"] = quality_report
        local_report["global_provenance"] = provenance_report
        payload["shadow_search"] = local_report
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
                report(94, "正在生成构造备选与比例补线…")
                variant = build_shadow_candidate_variant_v6(
                    image_bytes,
                    settings_mapping,
                    payload,
                    provenance_report,
                )
            except Exception as error:  # Candidate rendering is even more isolated.
                local_report["candidate_variant_emitted"] = False
                local_report["candidate_variant_error"] = str(error)
            else:
                if variant is not None:
                    report(98, "正在复核构造备选结构…")
                    variant_quality = build_quality_report_v5(variant)
                    variant.setdefault("stats", {})["quality_v5"] = variant_quality
                    payload.setdefault("variants", []).append(variant)
                    local_report["candidate_variant_emitted"] = True
                    local_report["candidate_variant_id"] = variant["id"]
                    local_report["candidate_variant_provenance_mode"] = variant["stats"].get(
                        "shadow_candidate_provenance_mode",
                        "quality_aware_v6",
                    )
                    local_report["candidate_variant_quality"] = variant_quality
                else:
                    local_report["candidate_variant_emitted"] = False
                    local_report["candidate_variant_reason"] = "no_material_core_point_free_geometry_change"
    report(100, "重绘完成")
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


__all__ = [
    "reconstruct_for_web_shadow_json",
    "rectify_for_web_json",
    "_map_strict_progress",
]
