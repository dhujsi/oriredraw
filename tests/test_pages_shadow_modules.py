import re
from pathlib import Path


def test_pages_packages_every_shadow_worker_module():
    root = Path(__file__).parents[1]
    worker = (root / "web" / "pyodide-worker.js").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    preview = (root / "scripts" / "preview.py").read_text(encoding="utf-8")

    required = [
        "shadow_search.py",
        "boundary_relations.py",
        "exact_qsqrt2.py",
        "qsqrt2_coordinates.py",
        "exact_graph_propagation.py",
        "finite_endpoint_closure.py",
        "guided_cp_output.py",
        "constrained_angle_candidates.py",
        "transactional_angle_repair.py",
        "guided_construction.py",
        "raw_boundary_evidence.py",
        "raw_crease_evidence.py",
        "raw_crease_topology.py",
        "raw_primary_bridge.py",
        "shadow_evidence.py",
        "shadow_geometry.py",
        "shadow_geometry_v2.py",
        "shadow_variant.py",
        "provenance_v3.py",
        "provenance_v4.py",
        "provenance_v5.py",
        "provenance_v6.py",
        "quality_v5.py",
        "selected_geometry_v4.py",
        "shadow_variant_v3.py",
        "isolated_ratio.py",
        "shadow_variant_v4.py",
        "shadow_variant_v5.py",
        "shadow_variant_v6.py",
        "shadow_bridge.py",
    ]
    for name in required:
        assert name in worker, f"worker does not load {name}"
        assert name in workflow, f"Pages does not package {name}"
        assert name in preview, f"local preview does not package {name}"

    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    pattern = re.compile(r"^const WEB_ENGINE_VERSION = '([^']+)';$", re.MULTILINE)
    assert pattern.search(app).group(1) == pattern.search(worker).group(1)
