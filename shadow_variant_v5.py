from __future__ import annotations

from typing import Any, Mapping

from shadow_variant_v4 import build_shadow_candidate_variant_v4


def _operation_by_target(report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    output: dict[int, Mapping[str, Any]] = {}
    for operation in list(report.get("selected_operations") or []):
        if not isinstance(operation, Mapping) or operation.get("target_trace_id") is None:
            continue
        try:
            output[int(operation["target_trace_id"])] = operation
        except (TypeError, ValueError):
            continue
    return output


def build_shadow_candidate_variant_v5(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any] | None:
    variant = build_shadow_candidate_variant_v4(
        image_bytes,
        settings_mapping,
        result,
        report,
    )
    if variant is None:
        return None

    stats = variant.setdefault("stats", {})
    stats["shadow_candidate_provenance_mode"] = "quality_aware_v5"
    operations = _operation_by_target(report)
    for anchor in list(variant.get("playback_trace") or []):
        if not isinstance(anchor, dict):
            continue
        try:
            trace_id = int(anchor.get("trace_id"))
        except (TypeError, ValueError):
            continue
        operation = operations.get(trace_id)
        if operation is None:
            continue
        provenance = str(operation.get("provenance") or "")
        if provenance == "geometry_reroot":
            anchor["source"] = "低质量旧几何已隔离：从其他可靠参考点重新取线"
        elif provenance == "paper_corner_symmetry_ray":
            corner = str(operation.get("source_corner") or "paper_corner")
            names = {
                "top_left": "左上角",
                "top_right": "右上角",
                "bottom_right": "右下角",
                "bottom_left": "左下角",
            }
            anchor["source"] = f"纸角对称取点：{names.get(corner, corner)}"
        elif provenance == "segment_ratio_ray":
            ratio = str(operation.get("ratio") or "")
            anchor["source"] = f"已有线段比例取点：{ratio}"

    warnings = [
        "这是 quality-aware construction search 的影子候选；strict 仍保留作对照。",
        "图像 evidence 与几何 hypothesis 已分离：坏几何会被隔离并继续寻找其他参考点，未解释的强线证据仍保留为待解目标。",
        "cAMV 是高权重结构先验；只有与几何错位/重复线共同出现时才强烈归因于重建错误，干净的局部异常不会一票否决。",
        "线段比例补线不依赖正方形或纸边区域，候选必须通过原图线脊验证。",
    ]
    warnings.extend(
        item for item in list(variant.get("warnings") or [])
        if "正方形" not in str(item) and "纸边" not in str(item)
    )
    variant["warnings"] = warnings
    return variant


__all__ = ["build_shadow_candidate_variant_v5"]
