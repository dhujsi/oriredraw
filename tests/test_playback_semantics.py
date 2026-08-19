from pathlib import Path


def test_derivation_playback_uses_semantic_line_styles():
    root = Path(__file__).parents[1]
    source = (root / "web" / "playback-annotations.js").read_text(encoding="utf-8")

    assert "underlayToggle.checked = false" in source
    assert "foldType === 2 ? DASH_DOT : DASH" in source
    assert "geo, RED, 1.35, DASH" in source
    assert "geo, GREY, 1.0, AUX_DASH" in source
    assert "oriredraw-playback-underlay-hint-v1" in source
    assert "highlight-picker" not in source


def test_playback_keeps_expired_auxiliary_lines_in_grey():
    root = Path(__file__).parents[1]
    source = (root / "web" / "playback-retained-aux.js").read_text(encoding="utf-8")
    assert "currentGeneration > lastUse" in source
    assert "#a9aaa6" in source
    assert "setLineDash" in source


def test_variant_uses_its_own_playback_trace():
    root = Path(__file__).parents[1]
    source = (root / "web" / "playback-variant-trace.js").read_text(encoding="utf-8")
    assert "version.playback_trace" in source
    assert "root.playback_trace" in source
    assert "true" in source  # capture phase: swap before playback.js rebuilds groups


def test_playback_layers_are_loaded():
    root = Path(__file__).parents[1]
    project_loader = (root / "web" / "project.js").read_text(encoding="utf-8")
    assert "import './playback-variant-trace.js';" in project_loader
    assert "import './playback-annotations.js';" in project_loader
    assert "import './playback-retained-aux.js';" in project_loader
