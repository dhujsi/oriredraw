from pathlib import Path


def test_derivation_playback_uses_semantic_line_styles():
    root = Path(__file__).parents[1]
    source = (root / "web" / "playback-annotations.js").read_text(encoding="utf-8")

    assert "underlayToggle.checked = false" in source
    assert "foldType === 2 ? DASH_DOT : DASH" in source
    assert "geo, RED, 1.35, DASH" in source
    assert "geo, GREY, 1.0, AUX_DASH" in source
    assert "if (currentGeneration > lastUse) return;" in source
    assert "oriredraw-playback-underlay-hint-v1" in source
    assert "highlight-picker" not in source


def test_playback_annotation_layer_is_loaded():
    root = Path(__file__).parents[1]
    project_loader = (root / "web" / "project.js").read_text(encoding="utf-8")
    assert "import './playback-annotations.js';" in project_loader
