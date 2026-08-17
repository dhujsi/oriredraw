from __future__ import annotations

import argparse
import json
from pathlib import Path

from cp_audit import (
    audit_runtime_reliability,
    compare_cp_data,
    difference_svg,
    write_json,
)


def _types(value: str) -> set[int]:
    try:
        return {int(item) for item in value.split(",") if item.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("类型必须是逗号分隔的整数") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=".cp 数据评测与运行可靠性审计")
    commands = root.add_subparsers(dest="command", required=True)

    compare = commands.add_parser(
        "compare", help="开发评测：预测 .cp 对正确答案 .cp"
    )
    compare.add_argument("--prediction", required=True)
    compare.add_argument("--reference", required=True)
    compare.add_argument("--json", required=True)
    compare.add_argument("--svg")
    compare.add_argument("--prediction-types", type=_types, default={2, 3, 4})
    compare.add_argument("--reference-types", type=_types, default={2, 3})
    compare.add_argument("--ray-tolerance", type=float, default=0.5)
    compare.add_argument("--interval-tolerance", type=float, default=0.05)
    compare.add_argument("--node-tolerance", type=float, default=0.5)

    reliability = commands.add_parser(
        "reliability", help="实践审计：预测 .cp 对输入图片"
    )
    reliability.add_argument("--prediction", required=True)
    reliability.add_argument("--image", required=True)
    reliability.add_argument("--json", required=True)
    reliability.add_argument("--support-threshold", type=float, default=0.72)
    return root


def main() -> None:
    arguments = parser().parse_args()
    prediction_text = Path(arguments.prediction).read_text(encoding="utf-8")
    if arguments.command == "compare":
        reference_text = Path(arguments.reference).read_text(encoding="utf-8")
        report = compare_cp_data(
            prediction_text,
            reference_text,
            prediction_types=arguments.prediction_types,
            reference_types=arguments.reference_types,
            ray_tolerance=arguments.ray_tolerance,
            interval_tolerance=arguments.interval_tolerance,
            node_tolerance=arguments.node_tolerance,
        )
        write_json(arguments.json, report)
        if arguments.svg:
            Path(arguments.svg).write_text(
                difference_svg(reference_text, report), encoding="utf-8"
            )
        summary = {
            "report_kind": report["report_kind"],
            "exact_geometry_match": report["exact_geometry_match"],
            "ray_metrics": report["ray_metrics"],
            "finite_geometry_metrics": report["finite_geometry_metrics"],
            "mv_assignment_metrics": report["mv_assignment_metrics"],
            "node_metrics": report["node_metrics"],
        }
    else:
        report = audit_runtime_reliability(
            prediction_text,
            Path(arguments.image).read_bytes(),
            evidence_support_threshold=arguments.support_threshold,
        )
        write_json(arguments.json, report)
        summary = {
            "report_kind": report["report_kind"],
            "does_not_measure": report["does_not_measure"],
            "evidence": {
                key: value
                for key, value in report["evidence"].items()
                if key != "unsupported_edges"
            },
            "constraints": {
                "illegal_angle_edge_count": report["constraints"][
                    "illegal_angle_edge_count"
                ],
                "internal_degree_one_node_count": report["constraints"][
                    "internal_degree_one_node_count"
                ],
                "camv_structure_violation_count": report["constraints"][
                    "camv_structure"
                ]["violation_count"],
                "camv_structural_completeness_score": report["constraints"][
                    "camv_structure"
                ]["structural_completeness_score"],
                "camv_full_violation_count": report["constraints"][
                    "camv_full"
                ]["violation_count"],
                "camv_full_passes": report["constraints"]["camv_full"][
                    "passes_camv"
                ],
            },
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
