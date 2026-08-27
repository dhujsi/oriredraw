"""Browser wrapper that appends construction-search diagnostics and variants."""

from __future__ import annotations

import json
from typing import Any, Callable

from guided_construction import (
    build_boundary_relation_catalog,
    build_boundary_relation_catalog_from_points,
)
from provenance_v6 import build_provenance_report_v6
from quality_v5 import build_quality_report_v5
from raw_boundary_evidence import extract_raw_boundary_contacts
from raw_crease_evidence import extract_raw_crease_entities
from raw_crease_topology import build_raw_crease_topology_graph
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
        try:
            raw_crease_evidence = extract_raw_crease_entities(
                image_bytes,
                settings_mapping,
            )
        except Exception as raw_crease_error:
            raw_crease_evidence = {
                "enabled": False,
                "mode": "raw_finite_crease_entities_v1",
                "source": "raw_image_finite_line_evidence",
                "reason": "raw_crease_evidence_error",
                "error": str(raw_crease_error),
                "lines": [],
            }
        try:
            _, _, raw_crease_topology = build_raw_crease_topology_graph(
                raw_crease_evidence
            )
        except Exception as raw_topology_error:
            raw_crease_topology = {
                "enabled": False,
                "mode": "raw_finite_crease_topology_v1",
                "source": "raw_image_finite_line_evidence",
                "reason": "raw_crease_topology_error",
                "error": str(raw_topology_error),
                "boundary_contacts": [],
            }
        try:
            topology_contacts = (
                raw_crease_topology.get("boundary_contacts")
                if raw_crease_topology.get("enabled")
                else None
            )
            if isinstance(topology_contacts, list) and topology_contacts:
                raw_maximum = float(
                    raw_crease_topology["maximum_coordinate_px"]
                )
                candidate_points = topology_contacts
                candidate_evidence_source = "raw_image_finite_topology"
                raw_boundary_evidence = {
                    "enabled": False,
                    "mode": "raw_boundary_directional_scan_v1",
                    "source": "raw_image_directional_scan",
                    "reason": "superseded_by_finite_crease_topology",
                    "maximum_coordinate_px": raw_maximum,
                    "points": [],
                }
            else:
                raw_boundary_evidence = extract_raw_boundary_contacts(
                    image_bytes,
                    settings_mapping,
                )
                raw_maximum = float(
                    raw_boundary_evidence["maximum_coordinate_px"]
                )
                candidate_points = raw_boundary_evidence.get("points") or []
                candidate_evidence_source = "raw_image_directional_scan"
            boundary_candidates = build_boundary_relation_catalog_from_points(
                candidate_points,
                raw_maximum,
                evidence_source=candidate_evidence_source,
                tolerance_px=max(5.0, raw_maximum * 0.012),
                fit_algebraic_geometry=True,
            )
            boundary_candidate_source = candidate_evidence_source
        except Exception as raw_boundary_error:
            # Boundary guidance is optional and must not hide the rest of the
            # diagnostics.  The fallback is explicitly labelled as playback.
            raw_boundary_evidence = {
                "enabled": False,
                "mode": "raw_boundary_directional_scan_v1",
                "source": "raw_image_directional_scan",
                "reason": "raw_boundary_evidence_error",
                "error": str(raw_boundary_error),
                "points": [],
            }
            boundary_candidates = build_boundary_relation_catalog(payload)
            boundary_candidate_source = "strict_playback_trace_boundary_contacts_fallback"
        report(84, "正在检查局部几何与结构…")
        local_report = build_geometry_shadow_report_v2(payload)
        quality_report = build_quality_report_v5(payload)
        # This only lists user-selectable hypotheses. It never changes the
        # strict output or starts a construction route on its own.
        local_report["raw_boundary_evidence"] = raw_boundary_evidence
        local_report["raw_crease_evidence"] = raw_crease_evidence
        local_report["raw_crease_topology"] = raw_crease_topology
        local_report["boundary_relation_candidates"] = boundary_candidates
        local_report["boundary_relation_candidate_source"] = boundary_candidate_source
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
