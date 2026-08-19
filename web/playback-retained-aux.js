(() => {
  'use strict';

  const bridge = window.oriredrawProjectBridge;
  const panel = document.querySelector('.oriredraw-playback');
  const stage = panel?.querySelector('.oriredraw-playback-stage');
  const baseCanvas = panel?.querySelector('canvas');
  const range = panel?.querySelector('.oriredraw-playback-range');
  if (!bridge || !panel || !stage || !baseCanvas || !range) return;

  const canvas = document.createElement('canvas');
  canvas.className = 'oriredraw-playback-retained-aux';
  Object.assign(canvas.style, {
    position: 'absolute',
    inset: '0',
    width: '100%',
    height: '100%',
    pointerEvents: 'none',
    zIndex: '3',
  });
  stage.append(canvas);
  const context = canvas.getContext('2d');
  if (!context) return;

  function trace(root) {
    return (Array.isArray(root?.playback_trace) ? root.playback_trace : [])
      .filter(anchor =>
        Number.isFinite(Number(anchor?.angle))
        && Number.isFinite(Number(anchor?.line_offset_px))
        && Array.isArray(anchor?.anchor_point_px)
        && anchor.anchor_point_px.length >= 2
        && Number.isFinite(Number(anchor?.generation))
      );
  }

  function analysisSize(root, anchors) {
    const value = Number(root?.stats?.analysis_size_used);
    if (Number.isFinite(value) && value > 1) return value - 1;
    return Math.max(1, ...anchors.flatMap(anchor => [
      Number(anchor.anchor_point_px?.[0]) || 0,
      Number(anchor.anchor_point_px?.[1]) || 0,
    ]));
  }

  function clipped(anchor, size) {
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
      const a = (0 - p) / u;
      const b = (size - p) / u;
      low = Math.max(low, Math.min(a, b));
      high = Math.min(high, Math.max(a, b));
    }
    if (!(low <= high)) return null;
    return {
      start: [px + ux * low, py + uy * low],
      end: [px + ux * high, py + uy * high],
    };
  }

  function resize() {
    if (canvas.width !== baseCanvas.width || canvas.height !== baseCanvas.height) {
      canvas.width = baseCanvas.width;
      canvas.height = baseCanvas.height;
    }
  }

  function drawSegment(start, end, size) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const margin = 16 * dpr;
    const inner = Math.max(1, canvas.width - margin * 2);
    const point = value => [
      margin + Number(value[0]) / size * inner,
      margin + Number(value[1]) / size * inner,
    ];
    const a = point(start);
    const b = point(end);
    context.save();
    context.globalAlpha = 0.62;
    context.strokeStyle = '#a9aaa6';
    context.lineWidth = 0.9 * dpr;
    context.lineCap = 'round';
    context.setLineDash([5 * dpr, 5 * dpr]);
    context.beginPath();
    context.moveTo(a[0], a[1]);
    context.lineTo(b[0], b[1]);
    context.stroke();
    context.restore();
  }

  function frame() {
    resize();
    context.clearRect(0, 0, canvas.width, canvas.height);
    const root = bridge.result;
    const active = panel.closest('.preview')?.classList.contains('playback-active');
    if (root && active) {
      const anchors = trace(root);
      const generations = [...new Set(anchors.map(anchor => Number(anchor.generation)))].sort((a, b) => a - b);
      const step = Number(range.value || 0);
      if (step < generations.length) {
        const currentGeneration = generations[step];
        const size = analysisSize(root, anchors);
        for (const anchor of anchors) {
          const generation = Number(anchor.generation);
          if (!(generation < currentGeneration)) continue;
          const segments = Array.isArray(anchor.formed_segments_px) ? anchor.formed_segments_px : [];
          if (segments.length || anchor.forms_output === true) continue;
          const rawLast = Number(anchor.last_used_generation);
          const lastUse = Number.isFinite(rawLast) ? rawLast : generation;
          if (!(currentGeneration > lastUse)) continue;
          const line = clipped(anchor, size);
          if (line) drawSegment(line.start, line.end, size);
        }
      }
    }
    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
})();
