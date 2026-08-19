(() => {
  'use strict';

  const bridge = window.oriredrawProjectBridge;
  const panel = document.querySelector('.oriredraw-playback');
  const stage = panel?.querySelector('.oriredraw-playback-stage');
  const baseCanvas = panel?.querySelector('canvas');
  const range = panel?.querySelector('.oriredraw-playback-range');
  const toggle = panel?.querySelector('.oriredraw-playback-toggle');
  const options = panel?.querySelector('.oriredraw-playback-options');
  const underlayToggle = panel?.querySelector('.oriredraw-playback-underlay input');
  if (!bridge || !panel || !stage || !baseCanvas || !range || !toggle || !options || !underlayToggle) return;

  const BLUE = '#2563eb';
  const RED = '#d94a45';
  const GREY = '#a9aaa6';
  const WARNING = '#ff9800';
  const ANIMATION_MS = 430;
  const HINT_KEY = 'oriredraw-playback-underlay-hint-v1';
  const DASH = [7, 5];
  const DASH_DOT = [8, 4, 1.5, 4];
  const AUX_DASH = [5, 5];

  const style = document.createElement('style');
  style.textContent = `
    .oriredraw-playback-annotation-canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2; }
    .oriredraw-playback-options { position: relative; gap: 9px; flex-wrap: wrap; align-items: center; }
    .oriredraw-playback-underlay-hint { max-width: 245px; padding: 6px 8px; border: 1px solid var(--line, #d7d5cc); background: rgba(255,255,255,.97); color: var(--muted, #6f706a); box-shadow: 0 4px 16px rgba(0,0,0,.08); font: 600 10px/1.45 system-ui, sans-serif; }
    .oriredraw-playback-underlay-hint[hidden] { display: none; }
  `;
  document.head.append(style);

  const overlay = document.createElement('canvas');
  overlay.className = 'oriredraw-playback-annotation-canvas';
  stage.append(overlay);
  const context = overlay.getContext('2d');
  if (!context) return;

  const hint = document.createElement('div');
  hint.className = 'oriredraw-playback-underlay-hint';
  hint.hidden = true;
  options.prepend(hint);

  // CP reference is deliberately opt-in. The base playback owns the checkbox
  // state, so update it through the existing change handler rather than a
  // second private preference.
  underlayToggle.checked = false;
  underlayToggle.dispatchEvent(new Event('change', { bubbles: true }));

  function isEnglish() {
    return document.documentElement.lang.toLowerCase().startsWith('en');
  }

  function hintCopy() {
    return isEnglish()
      ? 'Tip: turn on “CP reference” here if you want the final CP faintly underneath the derivation.'
      : '提示：需要时可在这里打开「重构 CP 底图」，把最终 CP 淡淡垫在推导下面。';
  }

  function updateLanguage() {
    hint.textContent = hintCopy();
  }

  new MutationObserver(updateLanguage).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['lang'],
  });
  updateLanguage();

  let hintTimer = null;
  function dismissHint() {
    if (hint.hidden) return;
    hint.hidden = true;
    if (hintTimer) window.clearTimeout(hintTimer);
    hintTimer = null;
    try { localStorage.setItem(HINT_KEY, '1'); } catch (_) { /* storage can be blocked */ }
  }

  function maybeShowHint() {
    try {
      if (localStorage.getItem(HINT_KEY)) return;
    } catch (_) { /* still show the one-time hint for this session */ }
    hint.textContent = hintCopy();
    hint.hidden = false;
    if (hintTimer) window.clearTimeout(hintTimer);
    hintTimer = window.setTimeout(dismissHint, 7000);
  }

  document.addEventListener('click', event => {
    const button = event.target.closest?.('.view-tabs button[data-view="playback"]');
    if (button) queueMicrotask(maybeShowHint);
  });
  underlayToggle.addEventListener('change', dismissHint);
  hint.addEventListener('click', dismissHint);

  function activeVersion(root) {
    if (!root) return null;
    const button = document.querySelector(
      '#version-tabs button.active[data-version], #version-tabs button[aria-selected="true"][data-version]'
    );
    const index = Math.max(0, Number(button?.dataset.version || 0));
    return [root, ...(root.variants || [])][index] || root;
  }

  function isValidAnchor(anchor) {
    return Number.isFinite(Number(anchor?.angle))
      && Number.isFinite(Number(anchor?.line_offset_px))
      && Array.isArray(anchor?.anchor_point_px)
      && anchor.anchor_point_px.length >= 2
      && Number.isFinite(Number(anchor.anchor_point_px[0]))
      && Number.isFinite(Number(anchor.anchor_point_px[1]))
      && Number.isFinite(Number(anchor?.generation));
  }

  function traceAnchors(root) {
    const trace = Array.isArray(root?.playback_trace)
      ? root.playback_trace
      : (root?.anchors || []);
    return trace.filter(isValidAnchor);
  }

  function formedSegments(anchor) {
    return (Array.isArray(anchor?.formed_segments_px) ? anchor.formed_segments_px : [])
      .map(segment => ({
        start: Array.isArray(segment?.start) ? segment.start.slice(0, 2).map(Number) : [],
        end: Array.isArray(segment?.end) ? segment.end.slice(0, 2).map(Number) : [],
      }))
      .filter(segment =>
        segment.start.length === 2
        && segment.end.length === 2
        && [...segment.start, ...segment.end].every(Number.isFinite)
      );
  }

  function variantSegments(version) {
    const values = [];
    for (const item of version?.constructions || []) {
      const start = item.start_px || item.start;
      const end = item.end_px || item.end;
      if (
        Array.isArray(start) && start.length >= 2
        && Array.isArray(end) && end.length >= 2
        && [...start.slice(0, 2), ...end.slice(0, 2)].every(value => Number.isFinite(Number(value)))
      ) {
        values.push({ start: start.slice(0, 2).map(Number), end: end.slice(0, 2).map(Number), foldType: 0 });
      }
    }
    return values;
  }

  function analysisSize(root) {
    const value = Number(root?.stats?.analysis_size_used);
    if (Number.isFinite(value) && value > 1) return value - 1;
    return Math.max(
      1,
      ...traceAnchors(root).flatMap(anchor => [
        Number(anchor.anchor_point_px?.[0]) || 0,
        Number(anchor.anchor_point_px?.[1]) || 0,
      ]),
    );
  }

  function cpToPixel(value, size) {
    return (Number(value) + 200) * size / 400;
  }

  function parseCp(version, root, size) {
    const cp = String(version?.cp || root?.cp || '');
    const rows = [];
    for (const raw of cp.split(/\r?\n/)) {
      const parts = raw.trim().split(/\s+/);
      if (parts.length !== 5) continue;
      const type = Number(parts[0]);
      const values = parts.slice(1).map(Number);
      if (![type, ...values].every(Number.isFinite) || type === 1) continue;
      rows.push({
        type,
        start: [cpToPixel(values[0], size), cpToPixel(values[1], size)],
        end: [cpToPixel(values[2], size), cpToPixel(values[3], size)],
      });
    }
    return rows;
  }

  function pointSegmentDistance(point, segment) {
    const ax = Number(segment.start[0]);
    const ay = Number(segment.start[1]);
    const bx = Number(segment.end[0]);
    const by = Number(segment.end[1]);
    const px = Number(point[0]);
    const py = Number(point[1]);
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared <= 1e-12) return Math.hypot(px - ax, py - ay);
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSquared));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  function foldTypeForSegment(segment, cpRows) {
    let best = null;
    for (const row of cpRows) {
      const error = Math.max(
        pointSegmentDistance(segment.start, row),
        pointSegmentDistance(segment.end, row),
      );
      if (error <= 0.85 && (best === null || error < best.error)) {
        best = { type: row.type, error };
      }
    }
    return best?.type || 0;
  }

  function buildGroups(root, version) {
    const size = analysisSize(root);
    const cpRows = parseCp(version, root, size);
    const byGeneration = new Map();
    for (const anchor of traceAnchors(root)) {
      const generation = Number(anchor.generation);
      if (!byGeneration.has(generation)) byGeneration.set(generation, []);
      const segments = formedSegments(anchor).map(segment => ({
        ...segment,
        foldType: foldTypeForSegment(segment, cpRows),
      }));
      byGeneration.get(generation).push({ anchor, segments });
    }
    const groups = [...byGeneration.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([generation, lines]) => ({ kind: 'rays', generation, lines }));
    const additions = variantSegments(version);
    if (additions.length) groups.push({ kind: 'segments', segments: additions });
    return groups;
  }

  function resizeOverlay() {
    if (overlay.width !== baseCanvas.width || overlay.height !== baseCanvas.height) {
      overlay.width = baseCanvas.width;
      overlay.height = baseCanvas.height;
    }
  }

  function geometry(root) {
    resizeOverlay();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const margin = 16 * dpr;
    return {
      dpr,
      margin,
      inner: Math.max(1, overlay.width - margin * 2),
      size: analysisSize(root),
    };
  }

  function pointToCanvas(point, geo) {
    return [
      geo.margin + Number(point[0]) / geo.size * geo.inner,
      geo.margin + Number(point[1]) / geo.size * geo.inner,
    ];
  }

  function strokeSegment(start, end, geo, color, width, dash = []) {
    const first = pointToCanvas(start, geo);
    const second = pointToCanvas(end, geo);
    context.save();
    context.strokeStyle = color;
    context.lineWidth = width * geo.dpr;
    context.lineCap = 'round';
    context.setLineDash(dash.map(value => value * geo.dpr));
    context.beginPath();
    context.moveTo(first[0], first[1]);
    context.lineTo(second[0], second[1]);
    context.stroke();
    context.restore();
  }

  function clippedEndpoints(anchor, size) {
    const angle = Number(anchor.angle) * Math.PI / 180;
    const ux = Math.cos(angle);
    const uy = Math.sin(angle);
    const px = Number(anchor.anchor_point_px[0]);
    const py = Number(anchor.anchor_point_px[1]);
    let low = -Infinity;
    let high = Infinity;
    for (const [p, u] of [[px, ux], [py, uy]]) {
      if (Math.abs(u) < 1e-9) {
        if (p < 0 || p > size) return null;
        continue;
      }
      const first = (0 - p) / u;
      const second = (size - p) / u;
      low = Math.max(low, Math.min(first, second));
      high = Math.min(high, Math.max(first, second));
    }
    if (!(low <= high)) return null;
    return {
      anchor: [px, py],
      unit: [ux, uy],
      low,
      high,
      start: [px + ux * low, py + uy * low],
      end: [px + ux * high, py + uy * high],
    };
  }

  function revealedFormedSegment(segment, clipped, progress) {
    const project = point =>
      (Number(point[0]) - clipped.anchor[0]) * clipped.unit[0]
      + (Number(point[1]) - clipped.anchor[1]) * clipped.unit[1];
    const lowRay = Math.min(clipped.low * progress, clipped.high * progress);
    const highRay = Math.max(clipped.low * progress, clipped.high * progress);
    const first = project(segment.start);
    const second = project(segment.end);
    const low = Math.max(Math.min(first, second), lowRay);
    const high = Math.min(Math.max(first, second), highRay);
    if (high <= low + 1e-7) return null;
    return {
      start: [clipped.anchor[0] + clipped.unit[0] * low, clipped.anchor[1] + clipped.unit[1] * low],
      end: [clipped.anchor[0] + clipped.unit[0] * high, clipped.anchor[1] + clipped.unit[1] * high],
    };
  }

  function drawAnimatedRay(anchor, progress, geo, color, width, dash) {
    const clipped = clippedEndpoints(anchor, geo.size);
    if (!clipped) return;
    const left = [
      clipped.anchor[0] + (clipped.start[0] - clipped.anchor[0]) * progress,
      clipped.anchor[1] + (clipped.start[1] - clipped.anchor[1]) * progress,
    ];
    const right = [
      clipped.anchor[0] + (clipped.end[0] - clipped.anchor[0]) * progress,
      clipped.anchor[1] + (clipped.end[1] - clipped.anchor[1]) * progress,
    ];
    strokeSegment(left, right, geo, color, width, dash);
  }

  function currentDash(foldType) {
    return foldType === 2 ? DASH_DOT : DASH;
  }

  function drawHistoricalLine(item, currentGeneration, geo) {
    const { anchor, segments } = item;
    const direct = segments.length > 0 || anchor.forms_output === true;
    if (direct) {
      if (segments.length) {
        for (const segment of segments) {
          strokeSegment(segment.start, segment.end, geo, RED, 1.35, DASH);
        }
      } else {
        const clipped = clippedEndpoints(anchor, geo.size);
        if (clipped) strokeSegment(clipped.start, clipped.end, geo, RED, 1.35, DASH);
      }
      return;
    }

    const generation = Number(anchor.generation);
    const rawLastUse = Number(anchor.last_used_generation);
    const lastUse = Number.isFinite(rawLastUse) ? rawLastUse : generation;
    if (currentGeneration > lastUse) return;
    const clipped = clippedEndpoints(anchor, geo.size);
    if (clipped) strokeSegment(clipped.start, clipped.end, geo, GREY, 1.0, AUX_DASH);
  }

  function drawCurrentLine(item, progress, geo) {
    const { anchor, segments } = item;
    const direct = segments.length > 0 || anchor.forms_output === true;
    if (!direct || !segments.length) {
      drawAnimatedRay(anchor, progress, geo, BLUE, 1.55, DASH);
      return;
    }

    const clipped = clippedEndpoints(anchor, geo.size);
    if (!clipped) return;
    for (const segment of segments) {
      const revealed = revealedFormedSegment(segment, clipped, progress);
      if (!revealed) continue;
      strokeSegment(revealed.start, revealed.end, geo, BLUE, 1.65, currentDash(segment.foldType));
    }
  }

  function drawHistorical(groups, step, geo) {
    const current = groups[step] || null;
    const currentGeneration = current?.kind === 'rays' ? Number(current.generation) : Infinity;
    for (let index = 0; index < step && index < groups.length; index += 1) {
      const group = groups[index];
      if (group.kind === 'segments') {
        for (const segment of group.segments) {
          strokeSegment(segment.start, segment.end, geo, RED, 1.35, DASH);
        }
      } else {
        for (const item of group.lines) drawHistoricalLine(item, currentGeneration, geo);
      }
    }
  }

  function drawCurrent(group, progress, geo) {
    if (!group) return;
    if (group.kind === 'segments') {
      for (const segment of group.segments) {
        const target = [
          segment.start[0] + (segment.end[0] - segment.start[0]) * progress,
          segment.start[1] + (segment.end[1] - segment.start[1]) * progress,
        ];
        strokeSegment(segment.start, target, geo, BLUE, 1.65, DASH);
      }
      return;
    }
    for (const item of group.lines) drawCurrentLine(item, progress, geo);
  }

  function pointOnSegment(point, segment, tolerance = 0.5) {
    return pointSegmentDistance(point, segment) <= tolerance;
  }

  function rayProgressToPoint(anchor, point, size) {
    const clipped = clippedEndpoints(anchor, size);
    if (!clipped) return 1;
    const q =
      (Number(point[0]) - clipped.anchor[0]) * clipped.unit[0]
      + (Number(point[1]) - clipped.anchor[1]) * clipped.unit[1];
    const denominator = q >= 0 ? clipped.high : clipped.low;
    if (Math.abs(denominator) <= 1e-9) return 0;
    return Math.max(0, Math.min(1, q / denominator));
  }

  function segmentProgressToPoint(segment, point) {
    const dx = segment.end[0] - segment.start[0];
    const dy = segment.end[1] - segment.start[1];
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared <= 1e-12) return 1;
    const t = (
      (Number(point[0]) - segment.start[0]) * dx
      + (Number(point[1]) - segment.start[1]) * dy
    ) / lengthSquared;
    return Math.max(0, Math.min(1, t));
  }

  function camvViolations(version) {
    const full = version?.stats?.camv_full?.violations;
    const structural = version?.stats?.camv_structure?.violations;
    const source = Array.isArray(full) ? full : (Array.isArray(structural) ? structural : []);
    const merged = new Map();
    for (const violation of source) {
      const point = Array.isArray(violation?.point) ? violation.point.slice(0, 2).map(Number) : [];
      if (point.length !== 2 || !point.every(Number.isFinite)) continue;
      const key = `${point[0].toFixed(5)},${point[1].toFixed(5)}`;
      if (!merged.has(key)) merged.set(key, { point, rules: [] });
      const rule = String(violation.rule || 'cAMV');
      if (!merged.get(key).rules.includes(rule)) merged.get(key).rules.push(rule);
    }
    return [...merged.values()];
  }

  function markerSchedule(groups, violation, size) {
    const incidents = [];
    groups.forEach((group, index) => {
      const thresholds = [];
      if (group.kind === 'rays') {
        for (const item of group.lines) {
          if (!item.segments.some(segment => pointOnSegment(violation.point, segment))) continue;
          thresholds.push(rayProgressToPoint(item.anchor, violation.point, size));
        }
      } else {
        for (const segment of group.segments) {
          if (!pointOnSegment(violation.point, segment)) continue;
          thresholds.push(segmentProgressToPoint(segment, violation.point));
        }
      }
      if (thresholds.length) incidents.push({ index, threshold: Math.max(...thresholds) });
    });
    if (!incidents.length) return null;
    const appearanceIndex = Math.max(...incidents.map(item => item.index));
    const threshold = Math.max(
      ...incidents.filter(item => item.index === appearanceIndex).map(item => item.threshold),
    );
    return { ...violation, appearanceIndex, threshold };
  }

  function drawCamvMarker(marker, step, progress, geo) {
    if (step < marker.appearanceIndex) return;
    if (step === marker.appearanceIndex && progress + 1e-6 < marker.threshold) return;

    const [x, y] = pointToCanvas(marker.point, geo);
    const appearing = step === marker.appearanceIndex;
    const radius = (appearing ? 8.5 : 5.0) * geo.dpr;
    context.save();
    context.fillStyle = 'rgba(255,255,255,.92)';
    context.strokeStyle = WARNING;
    context.lineWidth = (appearing ? 2.3 : 1.6) * geo.dpr;
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.fillStyle = '#6a3b00';
    context.font = `700 ${Math.max(7, 7 * geo.dpr)}px ui-monospace, Consolas, monospace`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText('!', x, y + .2 * geo.dpr);
    if (appearing) {
      const label = 'cAMV';
      context.font = `700 ${Math.max(8, 8 * geo.dpr)}px ui-monospace, Consolas, monospace`;
      context.textAlign = 'left';
      const metrics = context.measureText(label);
      const padding = 3 * geo.dpr;
      const labelWidth = metrics.width + padding * 2;
      const labelHeight = 13 * geo.dpr;
      let labelX = x + 11 * geo.dpr;
      if (labelX + labelWidth > overlay.width - geo.margin) labelX = x - 11 * geo.dpr - labelWidth;
      const labelY = Math.max(geo.margin, Math.min(overlay.height - geo.margin - labelHeight, y - labelHeight / 2));
      context.fillStyle = 'rgba(255,255,255,.96)';
      context.strokeStyle = WARNING;
      context.lineWidth = geo.dpr;
      context.fillRect(labelX, labelY, labelWidth, labelHeight);
      context.strokeRect(labelX, labelY, labelWidth, labelHeight);
      context.fillStyle = '#6a3b00';
      context.textBaseline = 'middle';
      context.fillText(label, labelX + padding, labelY + labelHeight / 2 + .2 * geo.dpr);
    }
    context.restore();
  }

  let cacheRoot = null;
  let cacheVersion = null;
  let cacheGroups = [];
  let cacheMarkers = [];
  let finalImage = null;
  let finalImageUri = '';

  function ensureFinalImage(root, version) {
    const uri = version?.reconstruction_data_uri || root?.reconstruction_data_uri || '';
    if (!uri || uri === finalImageUri) return;
    finalImageUri = uri;
    finalImage = null;
    const image = new Image();
    image.onload = () => {
      if (finalImageUri === uri) finalImage = image;
    };
    image.src = uri;
  }

  function playbackModel() {
    const root = bridge.result;
    const version = activeVersion(root);
    if (!root || !version) return { root: null, version: null, groups: [], markers: [] };
    if (root !== cacheRoot || version !== cacheVersion) {
      cacheRoot = root;
      cacheVersion = version;
      cacheGroups = buildGroups(root, version);
      const size = analysisSize(root);
      cacheMarkers = camvViolations(version)
        .map(violation => markerSchedule(cacheGroups, violation, size))
        .filter(Boolean);
      ensureFinalImage(root, version);
    }
    return { root, version, groups: cacheGroups, markers: cacheMarkers };
  }

  function setupOverlay(root) {
    const geo = geometry(root);
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, overlay.width, overlay.height);
    context.fillStyle = '#fff';
    context.fillRect(0, 0, overlay.width, overlay.height);

    if (underlayToggle.checked && finalImage) {
      context.save();
      context.globalAlpha = 0.12;
      context.drawImage(finalImage, geo.margin, geo.margin, geo.inner, geo.inner);
      context.restore();
    }

    context.strokeStyle = '#171714';
    context.lineWidth = Math.max(1, geo.dpr);
    context.strokeRect(
      geo.margin + .5,
      geo.margin + .5,
      overlay.width - geo.margin * 2 - 1,
      overlay.height - geo.margin * 2 - 1,
    );
    return geo;
  }

  let lastStep = Number(range.value || 0);
  let transitionStarted = performance.now() - ANIMATION_MS;
  let manualUntil = 0;

  range.addEventListener('input', () => {
    lastStep = Number(range.value || 0);
    transitionStarted = performance.now() - ANIMATION_MS;
    manualUntil = performance.now() + 80;
  });

  toggle.addEventListener('click', () => {
    queueMicrotask(() => {
      lastStep = Number(range.value || 0);
      transitionStarted = performance.now();
    });
  });

  function frame(now) {
    const model = playbackModel();
    resizeOverlay();
    context.clearRect(0, 0, overlay.width, overlay.height);

    const playbackActive = panel.closest('.preview')?.classList.contains('playback-active');
    if (playbackActive && model.root) {
      const step = Number(range.value || 0);
      if (step !== lastStep) {
        lastStep = step;
        transitionStarted = now;
      }
      const playing = toggle.textContent.trim() === 'Ⅱ' && now > manualUntil;
      const progress = playing
        ? Math.max(0, Math.min(1, (now - transitionStarted) / ANIMATION_MS))
        : 1;

      // At the final step the overlay becomes transparent so the original
      // final-result renderer remains the source of truth.
      if (step < model.groups.length) {
        const geo = setupOverlay(model.root);
        drawHistorical(model.groups, step, geo);
        drawCurrent(model.groups[step], progress, geo);
        for (const marker of model.markers) drawCamvMarker(marker, step, progress, geo);
      }
    }

    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
})();
