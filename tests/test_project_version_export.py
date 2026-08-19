from pathlib import Path


def test_project_export_injects_current_app_version():
    root = Path(__file__).parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    source = (root / "web" / "project-version.js").read_text(encoding="utf-8")
    loader = (root / "web" / "project.js").read_text(encoding="utf-8")

    assert f"const APP_VERSION = '{version}';" in source
    assert "value.format === 'oriredraw-project'" in source
    assert "app_version: APP_VERSION" in source
    assert loader.index("project-version.js") < loader.index("project-core.js")
