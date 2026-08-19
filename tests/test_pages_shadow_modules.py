from pathlib import Path


def test_pages_packages_every_shadow_worker_module():
    root = Path(__file__).parents[1]
    worker = (root / "web" / "pyodide-worker.js").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    required = [
        "shadow_search.py",
        "shadow_evidence.py",
        "shadow_geometry.py",
        "shadow_geometry_v2.py",
        "shadow_variant.py",
        "provenance_v3.py",
        "provenance_v4.py",
        "provenance_v5.py",
        "quality_v5.py",
        "selected_geometry_v4.py",
        "shadow_variant_v3.py",
        "isolated_ratio.py",
        "shadow_variant_v4.py",
        "shadow_variant_v5.py",
        "shadow_bridge.py",
    ]
    for name in required:
        assert name in worker, f"worker does not load {name}"
        assert name in workflow, f"Pages does not package {name}"
