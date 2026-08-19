from __future__ import annotations

from typing import Any, Mapping

from shadow_variant_v4 import build_shadow_candidate_variant_v4
from shadow_variant_v5 import build_shadow_candidate_variant_v5


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(anchor.get("trace_id", fallback))
    except (TypeError, ValueError):
        return fallback


def _focused_report(
    result: Mapping[str, Any],
    report: Mapping[str, Any],
    target_id: int,
) -> dict[str, Any] | None:
    selected_target = None
    for operation in list(report.get("selected_operations") or []):
        if not isinstance(operation, Mapping):
            continue
        try:
            current = int(operation.get("target_trace_id"))
        except (TypeError, ValueError):
            continue
        if current != target_id:
            continue
        try:
            candidate = float(operation["candidate_offset_px"])
        except (KeyError, TypeError, ValueError):
            continue
        trace = next(
            (
                item for item in list(result.get("playback_trace") or [])
                if isinstance(item, Mapping)
                and _trace_id(item, -1) == target_id
            ),
            None,
        )
        if trace is None or trace.get("line_offset_px") is None:
            continue
        if abs(candidate - float(trace["line_offset_px"])) < 0.12:
            continue
        selected_target = dict(operation)
        break
    if selected_target is None:
        return None

    operations: list[dict[str, Any]] = []
    for index, anchor in enumerate(list(result.get("playback_trace") or [])):
        if not isinstance(anchor, Mapping):
            continue
        trace_id = _trace_id(anchor, index)
        if trace_id == target_id:
            operations.append(selected_target)
            continue
        if anchor.get("line_offset_px") is None:
            continue
        operations.append(
            {
                "id": f"focused-legacy-{trace_id}",
                "kind": "legacy",
                "provenance": "legacy",
                "target_trace_id": trace_id,
                "candidate_offset_px": float(anchor["line_offset_px"]),
            }
        )
    return {
        "enabled": True,
        "mode": "focused_core_point_free_v6",
        "selected_operations": operations,
        "selected_alternatives": [selected_target],
        "selected_material_alternatives": [selected_target],
        "selected_offsets_px": {
            str(item["target_trace_id"]): item["candidate_offset_px"]
            for item in operations
            if item.get("target_trace_id") is not None and item.get("candidate_offset_px") is not None
        },
        "unexplained_observations": report.get("unexplained_observations", 0),
        "route_changed": True,
        "material_geometry_changed": True,
        "focused_core_point_target": target_id,
    }


def build_shadow_candidate_variant_v6(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any] | None:
    # First try the full quality-aware route.
    variant = build_shadow_candidate_variant_v5(
        image_bytes,
        settings_mapping,
        result,
        report,
    )
    if variant is not None:
        variant.setdefault("stats", {})["shadow_candidate_provenance_mode"] = "quality_aware_v6"
        return variant

    # If the full route contains many simultaneous relocations, topology rebuild
    # may reject the whole batch even though the suspect high-coefficient core
    # has a genuine alternative.  Emit a focused A/B branch that changes only
    # that core method and lets legacy descendants be recomputed from it.
    target = report.get("core_point_free_selected_target")
    if target is None:
        attempts = [
            item for item in list(report.get("core_point_free_attempts") or [])
            if isinstance(item, Mapping) and item.get("material_replacement_found")
        ]
        if attempts:
            target = attempts[0].get("target_trace_id")
    try:
        target_id = int(target)
    except (TypeError, ValueError):
        return None

    focused = _focused_report(result, report, target_id)
    if focused is None:
        return None
    variant = build_shadow_candidate_variant_v4(
        image_bytes,
        settings_mapping,
        result,
        focused,
    )
    if variant is None:
        return None
    variant["id"] = "construction-v2-core-free"
    variant["label"] = "构造搜索：放弃大系数核心点"
    variant.setdefault("stats", {})["shadow_candidate_provenance_mode"] = "focused_core_point_free_v6"
    variant["stats"]["shadow_candidate_core_point_target"] = target_id
    variant["warnings"] = [
        "这是显式 core-point-free A/B：暂时放弃可疑的大系数核心点取线法，但保留该处图像折痕 evidence。",
        "只把该核心点换成其他参考点产生的几何，再沿旧依赖关系重新传播；strict 仍保留作对照。",
        *(list(variant.get("warnings") or [])),
    ]
    return variant


__all__ = ["build_shadow_candidate_variant_v6"]
