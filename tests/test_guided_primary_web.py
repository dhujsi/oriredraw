from pathlib import Path


def _source(name: str) -> str:
    return (Path(__file__).parents[1] / name).read_text(encoding="utf-8")


def test_upload_uses_fast_raw_entry_and_legacy_reconstruction_is_explicit():
    app = _source("web/app.js")
    html = _source("web/index.html")
    worker = _source("web/pyodide-worker.js")

    assert "runImageFlow('analyze-raw')" in app
    assert "legacyReconstructButton?.addEventListener('click', () => runImageFlow('reconstruct'))" in app
    assert "分析原图并选择起点" in html
    assert "运行旧版严格重建" in html
    assert "type === 'analyze-raw'" in worker
    assert "analyze_raw_primary_json" in worker


def test_intermediate_raw_result_cannot_be_downloaded_or_saved_as_final_cp():
    app = _source("web/app.js")
    project = _source("web/project-core.js")
    playback = _source("web/playback.js")

    assert "return data?.mode === 'guided_raw_primary_v1';" in app
    assert "syncGuidedOutputState(data, guided)" in app
    assert "root.cp = ready ? report.cp : null" in app
    assert "typeof currentVariant.cp !== 'string'" in app
    assert "oriredraw:result-state" in project
    assert "saveButton.disabled = !outputReady" in project
    assert "exportButton.disabled = !outputReady" in project
    assert "message?.type === 'analyze-raw'" in playback


def test_raw_primary_bridge_is_packaged_for_preview_and_pages():
    preview = _source("scripts/preview.py")
    pages = _source(".github/workflows/pages.yml")

    assert '"raw_primary_bridge.py"' in preview
    assert "raw_primary_bridge.py" in pages


def test_guided_boundary_ui_keeps_multiple_rounds_in_one_reversible_chain():
    app = _source("web/app.js")
    html = _source("web/index.html")

    assert "selection_steps: selectionSteps" in app
    assert "segment_line_types: assignments" in app
    assert "guidedSelectionSteps" in app
    assert "guidedSelectionIds" in app
    assert "guided.next_relation_candidates" in app
    assert "undoGuidedBoundary" in app
    assert "delete root.shadow_search.guided_boundary" in app
    assert "root.phase = 'awaiting_boundary_relation'" in app
    assert 'id="boundary-relation-history"' in html
    assert 'id="boundary-relation-undo"' in html


def test_guided_mv_editor_is_manual_segment_scoped_and_exports_only_after_apply():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")

    assert "cp_output_contract?.candidate_segments" in app
    assert "source: 'user_confirmed'" in app
    assert "paintGuidedMvSegment" in app
    assert "paintGuidedMvAtPointer" in app
    assert "focusNextGuidedMvUnassigned" in app
    assert "paintGuidedMvAtPointer" in app
    assert "focusNextGuidedMvUnassigned" in app
    assert "guidedMvUndoStack" in app
    assert "invalidateGuidedOutput(root)" in app
    assert "requestGuidedBoundary(guidedSelectionSteps(report), assignments)" in app
    assert "系统不会自动猜测 M/V" in app
    assert 'id="mv-segment-layer"' in html
    assert 'id="mv-editor"' in html
    assert 'id="mv-brush-mountain"' in html
    assert 'id="mv-brush-valley"' in html
    assert 'id="mv-next-unassigned"' in html
    assert 'id="mv-apply"' in html
    assert 'id="mv-next-unassigned"' in html
    assert ".mv-segment[data-line-type=\"2\"]" in style
    assert ".mv-segment[data-line-type=\"3\"]" in style


def test_guided_mv_editor_keeps_source_color_evidence_read_only_and_manual_only_for_ambiguous_segments():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")

    assert "GUIDED_MV_AUTOMATIC_SOURCE = 'source_image_color_evidence'" in app
    assert "guidedMvSegmentIsAutomatic" in app
    assert "guidedMvEditableSegments" in app
    assert "guidedMvResolvedAssignments" in app
    assert "不可手动修改" in app
    assert "value?.source !== GUIDED_MV_AUTOMATIC_SOURCE" in app
    assert "原图中红蓝颜色明确的有限折痕会自动判定" in html
    assert "明确红蓝的线段只展示、不接受修改" in html
    assert ".mv-segment.automatic" in style


def test_unresolved_topology_points_are_hoverable_but_need_explicit_confirmation():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")

    assert "next_topology_point_candidates" in app
    assert "renderTopologyPointOverlay" in app
    assert "showTopologyPointTooltip" in app
    assert "stageTopologyPointConfirmation" in app
    assert "confirmGuidedTopologyPoint" in app
    assert "{ kind: 'topology_point', id: pointId }" in app
    assert "光标本身不会被拟合成新点" in app
    assert 'id="topology-point-layer"' in html
    assert 'id="topology-point-tooltip"' in html
    assert 'id="topology-point-confirmation"' in html
    assert 'id="topology-point-confirm"' in html
    assert ".topology-point-marker" in style


def test_boundary_relation_points_share_the_guided_coordinate_tooltip_layer():
    app = _source("web/app.js")
    style = _source("web/style.css")

    assert "buildBoundaryRelationPointCandidates" in app
    assert "boundary_relation_point" in app
    assert "observed_point_px" in app
    assert "guidedPointCoordinateExpressions" in app
    assert "renderTopologyPointOverlay(guided, candidates, root)" in app
    assert "下方选择对应取线关系" in app
    assert ".topology-point-marker.boundary-relation-point" in style
    assert "width: 13px; height: 13px" in style
