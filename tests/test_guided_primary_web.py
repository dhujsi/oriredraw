from pathlib import Path


def _source(name: str) -> str:
    return (Path(__file__).parents[1] / name).read_text(encoding="utf-8")


def test_upload_uses_fast_raw_entry_without_exposing_the_legacy_flow():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")
    worker = _source("web/pyodide-worker.js")

    assert "callWorker('analyze-raw'" in app
    assert "分析原图并选起点" in html
    assert 'id="legacy-reconstruct-panel"' not in html
    assert "legacyReconstruct" not in app
    assert "runImageFlow('reconstruct')" not in app
    assert ".legacy-reconstruct" not in style
    assert "type === 'analyze-raw'" in worker
    assert "analyze_raw_primary_json" in worker


def test_current_cp_download_does_not_depend_on_output_checks():
    app = _source("web/app.js")
    project = _source("web/project-core.js")
    playback = _source("web/playback.js")

    assert "return data?.mode === 'guided_raw_primary_v1';" in app
    assert "syncGuidedOutputState(data, guided)" in app
    assert "root.cp = typeof report.cp === 'string'" in app
    assert "currentVariant = cpAvailable ? root : null" in app
    assert "buildDownloadableCurrentCp(root, report)" in app
    assert "buildDownloadableCurrentCp(root, null)" in app
    assert "typeof currentVariant.cp !== 'string'" in app
    assert "setResultAvailability(cpAvailable)" in app
    assert "cpAvailable: Boolean(cpAvailable)" in app
    assert "report?.output_ready\n    &&" not in app
    assert "oriredraw:result-state" in project
    assert "saveButton.disabled = !projectAvailable" in project
    assert "exportButton.disabled = !projectAvailable" in project
    assert "downloadButton.disabled = !cpAvailable" in project
    assert "isGuidedRaw" in project
    assert "projectAvailable: Boolean(projectAvailable)" in app
    assert "outputReady" not in project
    assert "message?.type === 'analyze-raw'" in playback


def test_guided_raw_project_round_trip_preserves_result_without_requiring_cp():
    project = _source("web/project-core.js")
    playback = _source("web/playback.js")

    # Guided raw projects remain valid independently of completeness diagnostics.
    assert "const isGuidedRaw = root?.mode === 'guided_raw_primary_v1';" in project
    assert "const hasCp = typeof root?.cp === 'string' && root.cp.length > 0;" in project
    assert "result: root" in project
    assert "typeof root.reconstruction_data_uri !== 'string'" in project
    assert "(!isGuidedRaw && !hasCp)" in project

    # Opening an exported file must restore the saved result, not start analysis again.
    assert "const isGuidedRaw = project?.result?.mode === 'guided_raw_primary_v1';" in project
    assert "typeof project.result.reconstruction_data_uri !== 'string'" in project
    assert "bridge.prepareRestore(project.result);" in project
    assert "form.requestSubmit();" in project
    assert project.index("bridge.prepareRestore(project.result);") < project.index("form.requestSubmit();")
    assert "state.projectRestorePayload = payload;" in playback
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


def test_guided_mv_output_is_read_only_and_has_no_gray_line_editor():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")
    i18n = _source("web/i18n.js")

    assert "cp_output_contract?.candidate_segments" in app
    assert "guidedMvDisplaySegments" in app
    assert "observed_raw_topology" in app
    assert "const topology = report?.raw_topology;" in app
    assert "maekawa_single_unknown_propagation" in app
    assert "source_image_default_mountain" in app
    assert "renderGuidedMvOverlay(root, guided);" in app
    assert 'id="mv-segment-layer"' in html
    assert 'id="mv-editor"' not in html
    assert 'id="mv-brush-mountain"' not in html
    assert 'id="mv-brush-valley"' not in html
    assert 'id="mv-next-unassigned"' not in html
    assert 'id="mv-apply"' not in html
    assert 'line-legend' in html
    assert '红线：原图为红色，或无色线按红色输出' in html
    assert ".mv-segment[data-line-type=\"2\"]" in style
    assert ".mv-segment[data-line-type=\"3\"]" in style
    assert ".mv-segment { pointer-events: none; }" in style
    for source in (app, html, i18n):
        assert "灰线" not in source
    assert "paintGuidedMvSegment" not in app
    assert "guidedMvEditableSegments" not in app
    assert "guidedMvResolvedAssignments" not in app


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
    assert "黄色点" in app
    assert "focus({ preventScroll: true })" in app
    assert 'id="topology-point-layer"' in html
    assert 'id="topology-point-tooltip"' in html
    assert 'id="topology-point-confirmation"' in html
    assert 'id="topology-point-confirm"' in html
    assert html.index('id="topology-point-confirmation"') < html.index('id="boundary-relations"')
    assert ".topology-point-marker" in style


def test_boundary_relation_points_share_the_guided_coordinate_tooltip_layer():
    app = _source("web/app.js")
    style = _source("web/style.css")

    assert "buildBoundaryRelationPointCandidates" in app
    assert "boundary_relation_point" in app
    assert "observed_point_px" in app
    assert "guidedPointCoordinateExpressions" in app
    assert "renderTopologyPointOverlay(guided, candidates, root)" in app
    assert "showBoundaryPointPopover" in app
    assert "fitTopologyPointPopup" in app
    assert "boundary_range_px" in app
    assert "范围约" in app
    assert "这个点怎么开始？" in app
    assert "下面每个按钮是一种开始方式，选一个就行。" in app
    assert "已选起点" in app
    assert "不需要再选点" in app
    assert "boundary-relation-coordinates" in app
    assert ".topology-point-marker.boundary-relation-point" in style
    assert "width: 15px; height: 15px" in style


def test_guided_copy_states_the_current_action_and_keeps_optional_steps_optional():
    app = _source("web/app.js")
    html = _source("web/index.html")

    assert "第一步：点一个绿色点。点旁边会出现开始方式，选一个就行。" in app
    assert "点一个绿色点，在点旁边选择开始方式。" in app
    assert "请直接点图上的绿色点；开始方式会出现在点旁边。" not in app
    assert "程序已经根据这个起点把能确定的线处理完了，不用再选点。" not in app
    assert "自动取线已结束" in app
    assert "initialRawSelection" in app
    assert "下面是可选的补充方式，不选也可以" in app
    assert "红线：原图为红色，或无色线按红色输出" in html
    assert "灰线" not in html
    assert "可以下载当前 .cp" in app
    assert "finite_segment_direction_mismatch" in app
    assert "有些线的方向和原图对不上" in app
    assert "internal_dangling_segment_endpoints" in app
    assert "有线在图内突然断开" in app
    assert "条折痕已写入当前 .cp，可以下载" in app
    assert "不能下载 .cp" not in app
    assert "暂时不能下载" not in app
    assert "blockerCodes.join('、')" not in app
    assert "window.scrollTo(0, previousScrollY)" in app
