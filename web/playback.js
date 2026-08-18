(() => {
  'use strict';

  const NativeWorker = window.Worker;
  const state = {
    root: null,
    version: null,
    versionIndex: 0,
    groups: [],
    step: 0,
    timer: null,
    animationFrame: null,
    finalImage: null,
    finalImageUri: '',
    active: false,
    showUnderlay: true,
    worker: null,
    projectRestorePayload: null,
    restoring: false,
  };

  class ObservedWorker extends NativeWorker {
    constructor(...args) {
      super(...args);
      state.worker = this;
      this.addEventListener('message', event => {
        const payload = event.data?.payload;
        if (
          event.data?.type === 'result'
          && payload
          && Array.isArray(payload.anchors)
          && typeof payload.reconstruction_data_uri === 'string'
        ) {
          state.root = payload;
          state.versionIndex = 0;
          state.version = payload;
          rebuildTrace();
          state.restoring = false;
        }
      });
    }

    postMessage(message, transfer) {
      if (message?.type === 'reconstruct' && state.projectRestorePayload) {
        const payload = state.projectRestorePayload;
        state.projectRestorePayload = null;
        queueMicrotask(() => {
          this.dispatchEvent(new MessageEvent('message', {
            data: { type: 'result', id: message.id, payload },
          }));
        });
        return;
      }
      super.postMessage(message, transfer);
    }
  }
  window.Worker = ObservedWorker;

  window.oriredrawProjectBridge = {
    get result() {
      return state.root;
    },
    get restoring() {
      return state.restoring;
    },
    prepareRestore(payload) {
      state.projectRestorePayload = payload;
      state.restoring = true;
      if (state.worker) {
        state.worker.dispatchEvent(new MessageEvent('message', {
          data: {
            type: 'status',
            stage: 'ready',
            message: '浏览器识别引擎已就绪',
          },
        }));
      }
    },
  };
  void import('./project.js').catch(error => {
    console.warn('Project module failed to load', error);
  });

  const style = document.createElement('style');
  style.textContent = `
    .preview.playback-active #preview-image { display: none; }
    .oriredraw-playback { display: none; width: 100%; }
    .preview.playback-active .oriredraw-playback { display: block; }
    .oriredraw-playback-stage { position: relative; width: 100%; aspect-ratio: 1; background: #fff; border: 1px solid var(--line, #d7d5cc); overflow: hidden; }
    .oriredraw-playback canvas { display: block; width: 100%; height: 100%; }
    .oriredraw-playback-controls { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: center; gap: 10px; margin-top: 10px; }
    .oriredraw-playback-toggle { width: 34px; height: 30px; min-height: 30px; padding: 0; border: 1px solid var(--ink, #171714); background: var(--acid, #c7ff2f); color: var(--ink, #171714); font: 700 13px/1 ui-monospace, Consolas, monospace; cursor: pointer; }
    .oriredraw-playback-toggle:disabled { cursor: default; opacity: .45; }
    .oriredraw-playback-range { width: 100%; min-width: 0; accent-color: var(--ink, #171714); }
    .oriredraw-playback-step { color: var(--muted, #6f706a); font: 700 10px/1 ui-monospace, Consolas, monospace; white-space: nowrap; }
    .oriredraw-playback-options { display: flex; justify-content: flex-end; margin-top: 7px; }
    .oriredraw-playback-underlay { display: inline-flex; align-items: center; gap: 6px; color: var(--muted, #6f706a); font: 700 10px/1.3 ui-monospace, Consolas, monospace; cursor: pointer; user-select: none; }
    .oriredraw-playback-underlay input { margin: 0; accent-color: var(--green, #1f8d55); }
    .oriredraw-playback-caption { min-height: 18px; margin-top: 6px; color: var(--muted, #6f706a); font-size: 10px; line-height: 1.5; }
    @media (max-width: 520px) {
      .oriredraw-playback-controls { grid-template-columns: 32px minmax(0, 1fr); }
      .oriredraw-playback-step { grid-column: 1 / -1; justify-self: end; }
    }
  `;
  document.head.append(style);

  const viewTabs = document.querySelector('.view-tabs');
  const previewFigure = document.querySelector('.preview');
  const previewImage = document.querySelector('#preview-image');
  if (!viewTabs || !previewFigure || !previewImage) return;

  const playbackTab = document.createElement('button');
  playbackTab.type = 'button';
  playbackTab.dataset.view = 'playback';
  playbackTab.setAttribute('role', 'tab');
  playbackTab.setAttribute('aria-selected', 'false');
  viewTabs.append(playbackTab);

  const panel = document.createElement('div');
  panel.className = 'oriredraw-playback';
  panel.innerHTML = `
    <div class="oriredraw-playback-stage">
      <canvas aria-label="最终重绘结果的构造推演"></canvas>
    </div>
    <div class="oriredraw-playback-controls">
      <button class="oriredraw-playback-toggle" type="button" aria-label="播放推演">▶</button>
      <input class="oriredraw-playback-range" type="range" min="0" max="0" step="1" value="0" aria-label="推演步骤">
      <span class="oriredraw-playback-step">0 / 0</span>
    </div>
    <div class="oriredraw-playback-options">
      <label class="oriredraw-playback-underlay">
        <input type="checkbox" checked>
        <span></span>
      </label>
    </div>
    <div class="oriredraw-playback-caption" aria-live="polite"></div>
  `;
  previewFigure.append(panel);

  const canvas = panel.querySelector('canvas');
  const toggle = panel.querySelector('.oriredraw-playback-toggle');
  const range = panel.querySelector('.oriredraw-playback-range');
  const stepLabel = panel.querySelector('.oriredraw-playback-step');
  const underlayToggle = panel.querySelector('.oriredraw-playback-underlay input');
  const underlayLabel = panel.querySelector('.oriredraw-playback-underlay span');
  const caption = panel.querySelector('.oriredraw-playback-caption');
  const context = canvas.getContext('2d');

  function isEnglish() {
    return document.documentElement.lang.toLowerCase().startsWith('en');
  }

  function copy() {
    return isEnglish()
      ? {
          tab: 'Derivation',
          play: 'Play derivation',
          pause: 'Pause derivation',
          underlay: 'CP reference',
          noTrace: 'No derivation trace is available for this result.',
          seeds: 'Initial construction seeds',
          generation: generation => `Construction generation ${generation}`,
          variant: 'Additional exact constructions in this variant',
          final: 'Final redrawn CP',
          step: (current, total) => `${current + 1} / ${total + 1}`,
        }
      : {
          tab: '推演播放',
          play: '播放推演',
          pause: '暂停推演',
          underlay: '重构 CP 底图',
          noTrace: '这个结果没有可播放的构造轨迹。',
          seeds: '初始构造种子',
          generation: generation => `第 ${generation} 代构造`,
          variant: '本版本新增精确构造',
          final: '最终重绘结果',
          step: (current, total) => `${current + 1} / ${total + 1}`,
        };
  }

  function updateLanguage() {
    const text = copy();
    playbackTab.textContent = text.tab;
    toggle.setAttribute('aria-label', state.timer ? text.pause : text.play);
    underlayLabel.textContent = text.underlay;
    renderStep(false);
  }

  new MutationObserver(updateLanguage).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['lang'],
  });
  updateLanguage();

  function isValidAnchor(anchor) {
    return Number.isFinite(Number(anchor?.angle))
      && Number.isFinite(Number(anchor?.line_offset_px))
      && Array.isArray(anchor?.anchor_point_px)
      && anchor.anchor_point_px.length >= 2
      && Number.isFinite(Number(anchor.anchor_point_px[0]))
      && Number.isFinite(Number(anchor.anchor_point_px[1]))
      && Number.isFinite(Number(anchor?.generation))
      && Number(anchor.generation) >= 0;
  }

  function traceAnchors() {
    const trace = Array.isArray(state.root?.playback_trace)
      ? state.root.playback_trace
      : (state.root?.anchors || []);
    return trace.filter(isValidAnchor);
  }

  function formedSegments(anchor) {
    const values = Array.isArray(anchor?.formed_segments_px)
      ? anchor.formed_segments_px
      : [];
    return values
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

  function variantSegments() {
    const items = state.version?.constructions || [];
    const segments = [];
    for (const item of items) {
      const start = item.start_px || item.start;
      const end = item.end_px || item.end;
      if (
        Array.isArray(start) && start.length >= 2
        && Array.isArray(end) && end.length >= 2
        && [...start.slice(0, 2), ...end.slice(0, 2)].every(value => Number.isFinite(Number(value)))
      ) {
        segments.push({
          start: start.slice(0, 2).map(Number),
          end: end.slice(0, 2).map(Number),
        });
      }
    }
    return segments;
  }

  function rebuildTrace() {
    stopPlayback();
    const anchors = traceAnchors();
    const byGeneration = new Map();
    for (const anchor of anchors) {
      const generation = Number(anchor.generation);
      if (!byGeneration.has(generation)) byGeneration.set(generation, []);
      byGeneration.get(generation).push(anchor);
    }
    state.groups = [...byGeneration.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([generation, lines]) => ({ kind: 'rays', generation, lines }));

    const additions = variantSegments();
    if (additions.length) state.groups.push({ kind: 'segments', segments: additions });

    state.step = state.groups.length ? state.groups.length : 0;
    range.min = '0';
    range.max = String(state.groups.length);
    range.value = String(state.step);
    toggle.disabled = state.groups.length === 0;
    loadFinalImage();
    renderStep(false);
  }

  function loadFinalImage() {
    const uri = state.version?.reconstruction_data_uri || state.root?.reconstruction_data_uri || '';
    if (!uri || uri === state.finalImageUri) return;
    state.finalImageUri = uri;
    const image = new Image();
    image.onload = () => {
      if (uri !== state.finalImageUri) return;
      state.finalImage = image;
      if (state.active) renderStep(false);
    };
    image.src = uri;
  }

  function selectVersion(index) {
    if (!state.root) return;
    const versions = [state.root, ...(state.root.variants || [])];
    state.versionIndex = Math.max(0, Math.min(versions.length - 1, Number(index) || 0));
    state.version = versions[state.versionIndex] || state.root;
    state.finalImage = null;
    state.finalImageUri = '';
    rebuildTrace();
  }

  document.addEventListener('click', event => {
    const versionButton = event.target.closest?.('#version-tabs button[data-version]');
    if (versionButton) {
      selectVersion(Number(versionButton.dataset.version));
      return;
    }

    const viewButton = event.target.closest?.('.view-tabs button[data-view]');
    if (!viewButton) return;
    const playback = viewButton.dataset.view === 'playback';
    state.active = playback;
    previewFigure.classList.toggle('playback-active', playback);
    if (!playback) stopPlayback();
    else {
      resizeCanvas();
      renderStep(false);
    }
  });

  range.addEventListener('input', () => {
    stopPlayback();
    state.step = Number(range.value);
    renderStep(false);
  });

  underlayToggle.addEventListener('change', () => {
    state.showUnderlay = underlayToggle.checked;
    renderStep(false);
  });

  toggle.addEventListener('click', () => {
    if (!state.groups.length) return;
    if (state.timer) {
      stopPlayback();
      return;
    }
    if (state.step >= state.groups.length) {
      state.step = 0;
      range.value = '0';
      renderStep(true);
    }
    toggle.textContent = 'Ⅱ';
    toggle.setAttribute('aria-label', copy().pause);
    state.timer = window.setInterval(() => {
      if (state.step >= state.groups.length) {
        stopPlayback();
        return;
      }
      state.step += 1;
      range.value = String(state.step);
      renderStep(true);
      if (state.step >= state.groups.length) {
        window.setTimeout(stopPlayback, 520);
      }
    }, 920);
  });

  function stopPlayback() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = null;
    if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
    state.animationFrame = null;
    toggle.textContent = '▶';
    toggle.setAttribute('aria-label', copy().play);
  }

  function resizeCanvas() {
    const stage = panel.querySelector('.oriredraw-playback-stage');
    const cssSize = Math.max(240, Math.round(stage.clientWidth || 640));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pixelSize = Math.round(cssSize * dpr);
    if (canvas.width !== pixelSize || canvas.height !== pixelSize) {
      canvas.width = pixelSize;
      canvas.height = pixelSize;
      canvas.style.width = `${cssSize}px`;
      canvas.style.height = `${cssSize}px`;
    }
  }

  new ResizeObserver(() => {
    if (!state.active) return;
    resizeCanvas();
    renderStep(false);
  }).observe(panel.querySelector('.oriredraw-playback-stage'));

  function analysisSize() {
    const value = Number(state.root?.stats?.analysis_size_used);
    if (Number.isFinite(value) && value > 1) return value - 1;
    const anchors = traceAnchors();
    const maximum = anchors.reduce((best, anchor) => Math.max(
      best,
      Number(anchor.anchor_point_px?.[0]) || 0,
      Number(anchor.anchor_point_px?.[1]) || 0,
    ), 0);
    return Math.max(1, maximum);
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
      start: [px + ux * low, py + uy * low],
      end: [px + ux * high, py + uy * high],
    };
  }

  function renderStep(animateCurrent) {
    if (!context) return;
    resizeCanvas();
    if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
    state.animationFrame = null;

    const total = state.groups.length;
    range.max = String(total);
    range.value = String(Math.min(state.step, total));
    stepLabel.textContent = copy().step(Math.min(state.step, total), total);

    if (!total) {
      drawConstruction(0, 1);
      caption.textContent = copy().noTrace;
      return;
    }

    if (state.step >= total) {
      caption.textContent = copy().final;
      drawFinal();
      return;
    }

    const current = state.groups[state.step];
    caption.textContent = current.kind === 'segments'
      ? copy().variant
      : (current.generation === 0 ? copy().seeds : copy().generation(current.generation));

    if (!animateCurrent) {
      drawConstruction(state.step, 1);
      return;
    }

    const start = performance.now();
    const duration = 430;
    const frame = now => {
      const progress = Math.min(1, (now - start) / duration);
      drawConstruction(state.step, 1 - Math.pow(1 - progress, 3));
      if (progress < 1) state.animationFrame = requestAnimationFrame(frame);
      else state.animationFrame = null;
    };
    state.animationFrame = requestAnimationFrame(frame);
  }

  function setupCanvas(withUnderlay = false) {
    const width = canvas.width;
    const height = canvas.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const margin = 16 * dpr;
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = '#fff';
    context.fillRect(0, 0, width, height);

    if (withUnderlay && state.showUnderlay && state.finalImage) {
      context.save();
      context.globalAlpha = 0.13;
      context.drawImage(
        state.finalImage,
        margin,
        margin,
        Math.max(1, width - margin * 2),
        Math.max(1, height - margin * 2),
      );
      context.restore();
    }

    context.strokeStyle = '#171714';
    context.lineWidth = Math.max(1, dpr);
    context.strokeRect(
      margin + .5,
      margin + .5,
      width - margin * 2 - 1,
      height - margin * 2 - 1,
    );
    return {
      width,
      height,
      margin,
      inner: Math.max(1, width - margin * 2),
      size: analysisSize(),
    };
  }

  function pointToCanvas(point, geometry) {
    return [
      geometry.margin + Number(point[0]) / geometry.size * geometry.inner,
      geometry.margin + Number(point[1]) / geometry.size * geometry.inner,
    ];
  }

  function strokeSegment(start, end, geometry, color, width) {
    const first = pointToCanvas(start, geometry);
    const second = pointToCanvas(end, geometry);
    context.strokeStyle = color;
    context.lineWidth = width;
    context.lineCap = 'round';
    context.beginPath();
    context.moveTo(first[0], first[1]);
    context.lineTo(second[0], second[1]);
    context.stroke();
  }

  function drawHistoricalAnchor(anchor, currentGeneration, geometry, dpr) {
    const clipped = clippedEndpoints(anchor, geometry.size);
    if (!clipped) return;

    const segments = formedSegments(anchor);
    const generation = Number(anchor.generation);
    const rawLastUse = Number(anchor.last_used_generation);
    const lastUse = Number.isFinite(rawLastUse) ? rawLastUse : generation;
    const stillNeeded = segments.length > 0 && currentGeneration <= lastUse;

    if (!segments.length) {
      strokeSegment(
        clipped.start,
        clipped.end,
        geometry,
        '#c5c6c1',
        .9 * dpr,
      );
      return;
    }

    if (stillNeeded) {
      strokeSegment(
        clipped.start,
        clipped.end,
        geometry,
        '#b7b8b2',
        1.0 * dpr,
      );
      return;
    }

    for (const segment of segments) {
      strokeSegment(
        segment.start,
        segment.end,
        geometry,
        '#aaaca6',
        1.05 * dpr,
      );
    }
  }

  function drawCurrentRay(anchor, progress, geometry, dpr) {
    const clipped = clippedEndpoints(anchor, geometry.size);
    if (!clipped) return;
    const left = [
      clipped.anchor[0] + (clipped.start[0] - clipped.anchor[0]) * progress,
      clipped.anchor[1] + (clipped.start[1] - clipped.anchor[1]) * progress,
    ];
    const right = [
      clipped.anchor[0] + (clipped.end[0] - clipped.anchor[0]) * progress,
      clipped.anchor[1] + (clipped.end[1] - clipped.anchor[1]) * progress,
    ];
    strokeSegment(left, right, geometry, '#171714', 1.9 * dpr);
    const marker = pointToCanvas(clipped.anchor, geometry);
    context.fillStyle = '#8bb900';
    context.beginPath();
    context.arc(marker[0], marker[1], 2.5 * dpr, 0, Math.PI * 2);
    context.fill();
  }

  function drawConstruction(step, progress) {
    const geometry = setupCanvas(true);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const currentGroup = state.groups[step] || null;
    const currentGeneration = currentGroup?.kind === 'rays'
      ? Number(currentGroup.generation)
      : Infinity;

    for (let index = 0; index < step && index < state.groups.length; index += 1) {
      const group = state.groups[index];
      if (group.kind === 'segments') {
        for (const segment of group.segments) {
          strokeSegment(
            segment.start,
            segment.end,
            geometry,
            '#aaaca6',
            1.05 * dpr,
          );
        }
        continue;
      }
      for (const anchor of group.lines) {
        drawHistoricalAnchor(anchor, currentGeneration, geometry, dpr);
      }
    }

    if (!currentGroup) return;
    if (currentGroup.kind === 'segments') {
      for (const segment of currentGroup.segments) {
        const target = [
          segment.start[0] + (segment.end[0] - segment.start[0]) * progress,
          segment.start[1] + (segment.end[1] - segment.start[1]) * progress,
        ];
        strokeSegment(segment.start, target, geometry, '#8bb900', 2.2 * dpr);
      }
      return;
    }

    for (const anchor of currentGroup.lines) {
      drawCurrentRay(anchor, progress, geometry, dpr);
    }
  }

  function drawFinal() {
    setupCanvas(false);
    if (!state.finalImage) {
      loadFinalImage();
      return;
    }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const margin = 16 * dpr;
    const inner = canvas.width - margin * 2;
    context.drawImage(state.finalImage, margin, margin, inner, inner);
  }

  rebuildTrace();
})();