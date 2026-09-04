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


def test_current_cp_download_keeps_verification_separate_from_availability():
    app = _source("web/app.js")
    project = _source("web/project-core.js")
    playback = _source("web/playback.js")

    assert "return data?.mode === 'guided_raw_primary_v1';" in app
    assert "syncGuidedOutputState(data, guided)" in app
    assert "report.output_ready === true && report.checks_passed === true" in app
    assert "report.cp_available === true" in app
    assert "root.cp = cpAvailable" in app
    assert "currentVariant = cpAvailable ? root : null" in app
    assert "buildDownloadableCurrentCp(root, report)" not in app
    assert "buildDownloadableCurrentCp(root, null)" not in app
    assert "typeof currentVariant.cp !== 'string'" in app
    assert "setResultAvailability(cpAvailable)" in app
    assert "cpAvailable: Boolean(cpAvailable)" in app
    assert "root.output_ready = checksPassed" in app
    assert "'-unverified'" in app
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


def test_guided_boundary_ui_stops_after_one_reversible_initial_relation():
    app = _source("web/app.js")
    html = _source("web/index.html")

    assert "selection_steps: selectionSteps" in app
    assert "segment_line_types: assignments" in app
    assert "guidedSelectionSteps" in app
    assert "guidedSelectionIds" in app
    assert "const candidates = continuing ? [] : allCandidates;" in app
    assert "if (selectedSteps.length) return;" in app
    assert "undoGuidedBoundary" in app
    assert "delete root.shadow_search.guided_boundary" in app
    assert "root.phase = 'awaiting_boundary_relation'" in app
    assert 'id="boundary-relation-history"' in html
    assert 'id="boundary-relation-undo"' in html


def test_guided_boundary_reports_progress_while_calculating_after_start_selection():
    app = _source("web/app.js")
    html = _source("web/index.html")
    worker = _source("web/pyodide-worker.js")

    assert 'id="guided-progress"' in html
    assert 'id="guided-progress-track"' in html
    assert "updateGuidedProgress" in app
    assert "beginGuidedProgress();" in app
    assert "endGuidedProgress();" in app
    assert "data.stage === 'guided-boundary'" in app
    assert "async function guidedBoundaryInBrowser(result, selection, id)" in worker
    assert "announce('guided-boundary'" in worker
    assert "剩余时间无法预估" in worker


def test_guided_result_rebuilds_a_playable_observed_to_derived_trace():
    app = _source("web/app.js")
    playback = _source("web/playback.js")

    assert "oriredraw:guided-result" in app
    assert "guidedPlaybackTrace" in playback
    assert "selected_guided_operations" in playback
    assert "guided_observed_raw_topology" in playback
    assert "guided_boundary_relation_ray" in playback
    assert "rebuildTrace();" in playback


def test_guided_selection_has_a_persistent_guide_layer_and_hides_replay_until_start():
    app = _source("web/app.js")
    html = _source("web/index.html")
    playback = _source("web/playback.js")
    style = _source("web/style.css")

    assert 'id="layer-guidance"' in html
    assert "guidanceLayerToggle" in app
    assert "data-guidance-visible" in app
    assert "guidance-hidden" in style
    assert "syncPlaybackTabVisibility" in playback
    assert "guided_raw_primary_v1" in playback
    assert "playbackTab.classList.toggle('hidden', awaitingStart)" in playback


def test_guided_mv_output_is_read_only_and_has_no_gray_line_editor():
    app = _source("web/app.js")
    html = _source("web/index.html")
    style = _source("web/style.css")
    i18n = _source("web/i18n.js")

    assert "cp_output_contract?.candidate_segments" in app
    assert "guidedMvDisplaySegments" in app
    assert "report?.cp_available || report?.cp_output_contract?.cp_available" in app
    assert "const segments = report?.enabled" in app
    assert "observed_raw_topology" in app
    assert "const topology = report?.raw_topology;" in app
    assert "source_image_default_mountain" in app
    assert "renderGuidedMvOverlay(root, guided);" in app
    assert 'id="mv-segment-layer"' in html
    assert 'id="mv-editor"' not in html
    assert 'id="mv-brush-mountain"' not in html
    assert 'id="mv-brush-valley"' not in html
    assert 'id="mv-next-unassigned"' not in html
    assert 'id="mv-apply"' not in html
    assert 'line-legend' not in html
    assert '线条颜色说明' not in html
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
    assert 'id="topology-point-guide"' in html
    assert 'id="topology-point-tooltip"' in html
    assert 'id="topology-point-confirmation"' in html
    assert 'id="topology-point-confirm"' in html
    assert html.index('id="topology-point-confirmation"') < html.index('id="boundary-relations"')
    assert ".topology-point-marker" in style
    assert ".topology-point-guide-line" in style
    assert ".topology-point-guide-label" in style


def test_boundary_relation_points_share_the_guided_coordinate_tooltip_layer():
    app = _source("web/app.js")
    style = _source("web/style.css")

    assert "buildBoundaryRelationPointCandidates" in app
    assert "boundary_relation_point" in app
    assert "observed_point_px" in app
    assert "guidedPointCoordinateExpressions" in app
    assert "renderTopologyPointOverlay" in app
    assert "showBoundaryPointPopover" in app
    assert "fitTopologyPointPopup" in app
    assert "crossSegmentSummary" in app
    assert "topologyPointGuideDistances" in app
    assert "topologyPointGuideSegments" in app
    assert "showTopologyPointGuide" in app
    assert "hideTopologyPointGuide" in app
    assert "visible_sides" in app
    assert "topology_point_start" in app
    assert "到纸边" in app
    assert "这个点怎么开始？" in app
    assert "同一个纸边点对应" in app
    assert "boundaryAssignmentTitle" in app
    assert "observedPointKey" in app
    assert "boundaryPointKeys" in app
    assert "精确取点方案" in app
    assert "沿上边的精确取点关系" not in app
    assert "已选起点" in app
    assert "boundary-relation-coordinates" in app
    assert ".topology-point-marker.boundary-relation-point" in style
    assert "width: 15px; height: 15px" in style


def test_guided_copy_states_the_current_action_and_keeps_optional_steps_optional():
    app = _source("web/app.js")
    html = _source("web/index.html")

    assert "第一步：点一个绿色点。点旁边会出现开始方式，选一个就行。" in app
    assert "点一个绿色点，在点旁边选择开始方式。" in app
    assert "请直接点图上的绿色点；开始方式会出现在点旁边。" not in app
    assert "唯一的起点" in app
    assert "不再要求选择第二个起点" in app
    assert "重绘已结束" in app
    assert 'id="layer-redraw"' in html
    assert 'id="layer-source"' in html
    assert 'data-view="overlay"' not in html
    assert 'data-view="clean"' not in html
    assert "原图折痕" not in html
    assert "原图线条" not in html
    assert "initialRawSelection" in app
    assert "下面是可选的补充方式，不选也可以" not in app
    assert 'line-legend' not in html
    assert "灰线" not in html
    assert "选择开始方式后即可下载当前 .cp 草稿" in app
    assert "finite_segment_direction_mismatch" in app
    assert "有些线的方向和原图对不上" in app
    assert "internal_dangling_segment_endpoints" in app
    assert "有线在图内突然断开" in app
    assert "条折痕已通过全部检查，可以下载当前 .cp" in app
    assert "仍可下载未验证的 .cp 草稿" in app
    assert "不能下载 .cp" not in app
    assert "当前不能下载 .cp" not in app
    assert "blockerCodes.join('、')" not in app
    assert "window.scrollTo(0, previousScrollY)" in app
