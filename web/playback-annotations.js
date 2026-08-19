(() => {
  'use strict';

  const bridge = window.oriredrawProjectBridge;
  const panel = document.querySelector('.oriredraw-playback');
  const stage = panel?.querySelector('.oriredraw-playback-stage');
  const baseCanvas = panel?.querySelector('canvas');
  const range = panel?.querySelector('.oriredraw-playback-range');
  const toggle = panel?.querySelector('.oriredraw-playback-toggle');
  const options = panel?.querySelector('.oriredraw-playback-options');
  if (!bridge || !panel || !stage || !baseCanvas || !range || !toggle || !options) return;

  const STORAGE_COLOR = 'oriredraw-playback-highlight-color';
  const DEFAULT_COLOR = '#e53935';
  const WARNING_COLOR = '#ff9800';
  const ANIMATION_MS = 430;

  function validColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || ''));
  }

  let highlightColor = localStorage.getItem(STORAGE_COLOR) || DEFAULT_COLOR;
  if (!validColor(highlightColor)) highlightColor = DEFAULT_COLOR;

  const style = document.createElement('style');
  style.textContent = `
    .oriredraw-playback-annotation-canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2; }
    .oriredraw-playback-highlight-picker { display: inline-flex; align-items: center; gap: 6px; color: var(--muted, #6f706a); font: 700 10px/1.3 ui-monospace, Consolas, monospace; user-select: none; }
    .oriredraw-playback-highlight-picker input { width: 25px; height: 18px; padding: 0; border: 1px solid var(--line, #d7d5cc); background: transparent; cursor: pointer; }
    .oriredraw-playback-highlight-picker input::-webkit-color-swatch-wrapper { padding: 1px; }
    .oriredraw-playback-highlight-picker input::-webkit-color-swatch { border: 0; }
    .oriredraw-playback-options { gap: 13px; flex-wrap: wrap; }
  `;
  document.head.append(style);

  const overlay = document.createElement('canvas');
  overlay.className = 'oriredraw-playback-annotation-canvas';
  stage.append(overlay);
  const context = overlay.getContext('2d');
  if (!context) return;

  const pickerLabel = document.createElement('label');
  pickerLabel.className = 'oriredraw-playback-highlight-picker';
  const pickerText = document.createElement('span');
  const picker = document.createElement('input');
  picker.type = 'color';
  picker.value = highlightColor;
  picker.setAttribute('aria-label', '最终 CP 高亮颜色');
  pickerLabel.append(pickerText, picker);
  options.prepend(pickerLabel);

  function isEnglish() {
    return document.documentElement.lang.toLowerCase().startsWith('en');
  }

  function updateLanguage() {
    const english = isEnglish();
    pickerText.textContent = english ? 'CP highlight' : '最终 CP 高亮';
    picker.setAttribute('aria-label', english ? 'Final CP highlight color' : '最终 CP 高亮颜色');
  }

  new MutationObserver(updateLanguage).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['lang'],
  });
  updateLanguage();

  picker.addEventListener('input', () => {
    if (!validColor(picker.value)) return;
    highlightColor = picker.value;
    localStorage.setItem(STORAGE_COLOR, highlightColor);
  });

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
        values.push({ start: start.slice(0, 2).map(Number), end: end.slice(0, 2).map(Number) });
      }
    }
    return values;
  }

  function buildGroups(root, version) {
    const byGeneration = new Map();
    for (const anchor of traceAnchors(root)) {
      const generation = Number(anchor.generation);
      if (!byGeneration.has(generation)) byGeneration.set(generation, []);
      byGeneration.get(generation).push(anchor);
    }
    const groups = [...byGeneration.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([generation, lines]) => ({ kind: 'rays', generation, lines }));
    const additions = variantSegments(version);
    if (additions.length) groups.push({ kind: 'segments', segments: additions });
    return groups;
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

  function strokeSegment(start, end, geo, color, width) {
    const first = pointToCanvas(start, geo);
    const second = pointToCanvas(end, geo);
    context.strokeStyle = color;
    context.lineWidth = width * geo.dpr;
    context.lineCap = 'round';
    context.beginPath();
    context.moveTo(first[0], first[1]);
    context.lineTo(second[0], second[1]);
    context.stroke();
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

  function pointOnSegment(point, segment, tolerance = 0.5) {
    const ax = Number(segment.start[0]);
    const ay = Number(segment.start[1]);
    const bx = Number(segment.end[0]);
    const by = Number(segment.end[1]);
    const px = Number(point[0]);
    const py = Number(point[1]);
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared <= 1e-12) return Math.hypot(px - ax, py - ay) <= tolerance;
    const t = ((px - ax) * dx + (py - ay) * dy) / lengthSquared;
    if (t < -1e-5 || t > 1.00001) return false;
    const qx = ax + Math.max(0, Math.min(1, t)) * dx;
    const qy = ay + Math.max(0, Math.min(1, t)) * dy;
    return Math.hypot(px - qx, py - qy) <= tolerance;
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
        for (const anchor of group.lines) {
          if (!formedSegments(anchor).some(segment => pointOnSegment(violation.point, segment))) continue;
          thresholds.push(rayProgressToPoint(anchor, violation.point, size));
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

  let cacheRoot = null;
  let cacheVersion = null;
  let cacheGroups = [];
  let cacheMarkers = [];

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
    }
    return { root, version, groups: cacheGroups, markers: cacheMarkers };
  }

  function drawHistoricalHighlights(groups, step, geo) {
    for (let index = 0; index < step && index < groups.length; index += 1) {
      const group = groups[index];
      if (group.kind === 'rays') {
        for (const anchor of group.lines) {
          for (const segment of formedSegments(anchor)) {
            strokeSegment(segment.start, segment.end, geo, highlightColor, 2.05);
          }
        }
      } else {
        for (const segment of group.segments) {
          strokeSegment(segment.start, segment.end, geo, highlightColor, 2.05);
        }
      }
    }
  }

  function drawCurrentHighlight(group, progress, geo) {
    if (!group) return;
    if (group.kind === 'segments') {
      for (const segment of group.segments) {
        const target = [
          segment.start[0] + (segment.end[0] - segment.start[0]) * progress,
          segment.start[1] + (segment.end[1] - segment.start[1]) * progress,
        ];
        strokeSegment(segment.start, target, geo, highlightColor, 3.55);
      }
      return;
    }
    for (const anchor of group.lines) {
      const clipped = clippedEndpoints(anchor, geo.size);
      if (!clipped) continue;
      for (const segment of formedSegments(anchor)) {
        const revealed = revealedFormedSegment(segment, clipped, progress);
        if (revealed) strokeSegment(revealed.start, revealed.end, geo, highlightColor, 3.55);
      }
    }
  }

  function drawCamvMarker(marker, step, progress, geo) {
    if (step < marker.appearanceIndex) return;
    if (step === marker.appearanceIndex && progress + 1e-6 < marker.threshold) return;

    const [x, y] = pointToCanvas(marker.point, geo);
    const appearing = step === marker.appearanceIndex;
    const radius = (appearing ? 8.5 : 5.0) * geo.dpr;

    context.save();
    context.fillStyle = 'rgba(255,255,255,.92)';
    context.strokeStyle = WARNING_COLOR;
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
      context.textBaseline = 'middle';
      const metrics = context.measureText(label);
      const padding = 3 * geo.dpr;
      const labelWidth = metrics.width + padding * 2;
      const labelHeight = 13 * geo.dpr;
      let labelX = x + 11 * geo.dpr;
      if (labelX + labelWidth > overlay.width - geo.margin) labelX = x - 11 * geo.dpr - labelWidth;
      let labelY = y - labelHeight / 2;
      labelY = Math.max(geo.margin, Math.min(overlay.height - geo.margin - labelHeight, labelY));
      context.fillStyle = 'rgba(255,255,255,.96)';
      context.strokeStyle = WARNING_COLOR;
      context.lineWidth = geo.dpr;
      context.fillRect(labelX, labelY, labelWidth, labelHeight);
      context.strokeRect(labelX, labelY, labelWidth, labelHeight);
      context.fillStyle = '#6a3b00';
      context.fillText(label, labelX + padding, labelY + labelHeight / 2 + .2 * geo.dpr);
    }
    context.restore();
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
      const geo = geometry(model.root);

      if (step < model.groups.length) {
        drawHistoricalHighlights(model.groups, step, geo);
        drawCurrentHighlight(model.groups[step], progress, geo);
      }
      for (const marker of model.markers) {
        drawCamvMarker(marker, step, progress, geo);
      }
    }

    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
})();