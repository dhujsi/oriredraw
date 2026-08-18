const uploadForm = document.querySelector('#upload-form');
const input = document.querySelector('#image-input');
const fileName = document.querySelector('#file-name');
const dropZone = document.querySelector('#drop-zone');
const emptyState = document.querySelector('#empty-state');
const loading = document.querySelector('#loading');
const resultContent = document.querySelector('#result-content');
const preview = document.querySelector('#preview-image');
const warnings = document.querySelector('#warnings');
const stats = document.querySelector('#stats');
const anchorTable = document.querySelector('#anchor-table');
const anchorCount = document.querySelector('#anchor-count');
const downloadButton = document.querySelector('#download');
const submitButton = document.querySelector('#submit-button');
const engineStatus = document.querySelector('#engine-status');
const paperTool = document.querySelector('#paper-tool');
const paperModeLabel = document.querySelector('#paper-mode-label');
const rectifyInput = document.querySelector('#rectify-input');
const rectifiedResult = document.querySelector('#rectified-result');
const rectifiedPreview = document.querySelector('#rectified-preview');
const rectifiedMeta = document.querySelector('#rectified-meta');
const rectifiedDownload = document.querySelector('#rectified-download');
const cornerToggle = document.querySelector('#corner-toggle');
const cornerEditor = document.querySelector('#corner-editor');
const cornerCanvas = document.querySelector('#corner-canvas');
const cornerLoupe = document.querySelector('#corner-loupe');
const cornerLoupeCanvas = document.querySelector('#corner-loupe-canvas');
const cornerLoupeLabel = document.querySelector('#corner-loupe-label');
const cornerInstruction = document.querySelector('#corner-instruction');
const cornerReset = document.querySelector('#corner-reset');
const cornerDone = document.querySelector('#corner-done');
const cornerDisable = document.querySelector('#corner-disable');
const cornerCrop = document.querySelector('#corner-crop');
const cornerFull = document.querySelector('#corner-full');
const angleMode = document.querySelector('#angle-mode');
const angleInput = document.querySelector('#angle');
const versionTabs = document.querySelector('#version-tabs');
const constructionDetails = document.querySelector('#construction-details');
const constructionCount = document.querySelector('#construction-count');
const constructionList = document.querySelector('#construction-list');
const loadingStage = document.querySelector('#loading-stage');
const loadingProgress = document.querySelector('#loading-progress');
const loadingProgressValue = document.querySelector('#loading-progress-value');
const corePointCanvas = document.querySelector('#core-point-canvas');
const corePointTitle = document.querySelector('#core-point-title');
const corePointCoordinate = document.querySelector('#core-point-coordinate');

const WEB_ENGINE_VERSION = '20260818-white-lineart-progress-v1';
const worker = new Worker(`./pyodide-worker.js?v=${WEB_ENGINE_VERSION}`, { type: 'module' });
const pending = new Map();
let requestId = 0;
let currentResult = null;
let engineReady = false;
let sourceBitmap = null;
let rectifyFile = null;
let rectifiedDataUri = '';
let cornerPoints = [];
let draggedCorner = -1;
let activeCorner = 0;
let cornerView = { x0: 0, y0: 0, x1: 1, y1: 1 };
let currentVariant = null;

function callWorker(type, payload = {}, transfer = []) {
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ type, id, ...payload }, transfer);
  });
}

worker.addEventListener('message', event => {
  const data = event.data;
  if (data.type === 'status') {
    if (data.stage === 'reconstruct') {
      updateProgress(Number(data.percent ?? 0), data.message);
      return;
    }
    setEngineStatus(data.stage === 'ready' ? 'ready' : 'loading', data.message);
    return;
  }
  const waiter = pending.get(data.id);
  if (!waiter) return;
  pending.delete(data.id);
  if (data.type === 'error') waiter.reject(new Error(cleanWorkerError(data.message)));
  else waiter.resolve(data.payload);
});

worker.addEventListener('error', event => {
  setEngineStatus('error', '识别引擎加载失败，请刷新后重试');
  for (const waiter of pending.values()) waiter.reject(new Error(event.message));
  pending.clear();
});

function setEngineStatus(state, message) {
  engineReady = state === 'ready';
  engineStatus.dataset.state = state;
  engineStatus.querySelector('p').textContent = message;
  submitButton.disabled = !engineReady;
}

function updateProgress(percent, message) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  loadingProgress.setAttribute('aria-valuenow', String(value));
  loadingProgress.querySelector('i').style.width = `${value}%`;
  loadingProgressValue.textContent = `${value}%`;
  loadingStage.textContent = message || '正在重建…';
}

function cleanWorkerError(message) {
  const lines = String(message || '').split('\n').filter(Boolean);
  const last = lines.at(-1) || '识别失败';
  return last.replace(/^[^:]+:\s*/, '');
}

callWorker('init').catch(error => setEngineStatus('error', error.message));

function bindRange(selector, outputSelector, format) {
  const element = document.querySelector(selector);
  const output = document.querySelector(outputSelector);
  const update = () => { output.textContent = format(Number(element.value)); };
  element.addEventListener('input', update);
  update();
}

bindRange('#support', '#support-value', value => `${Math.round(value * 100)}%`);
bindRange('#algebraic', '#algebraic-value', value => `${value.toFixed(1)}px`);

function updateAngleControl() {
  const automatic = angleMode.value === 'auto';
  angleInput.classList.toggle('hidden', automatic);
  document.querySelector('#angle-value').textContent = automatic
    ? '自动'
    : `${Number(angleInput.value).toFixed(1)}°`;
}
angleMode.addEventListener('change', updateAngleControl);
angleInput.addEventListener('input', updateAngleControl);
updateAngleControl();

function resetCornerPoints() {
  cornerPoints = [[0.03, 0.03], [0.97, 0.03], [0.97, 0.97], [0.03, 0.97]];
  activeCorner = 0;
  showFullCornerView();
}

function sourceToView([x, y]) {
  return [
    (x - cornerView.x0) / Math.max(1e-9, cornerView.x1 - cornerView.x0),
    (y - cornerView.y0) / Math.max(1e-9, cornerView.y1 - cornerView.y0),
  ];
}

function viewToSource([x, y]) {
  return [
    cornerView.x0 + x * (cornerView.x1 - cornerView.x0),
    cornerView.y0 + y * (cornerView.y1 - cornerView.y0),
  ];
}

function configureCornerCanvas() {
  if (!sourceBitmap) return;
  const sourceWidth = Math.max(1, (cornerView.x1 - cornerView.x0) * sourceBitmap.width);
  const sourceHeight = Math.max(1, (cornerView.y1 - cornerView.y0) * sourceBitmap.height);
  const scale = Math.min(8, 1600 / Math.max(sourceWidth, sourceHeight));
  cornerCanvas.width = Math.max(1, Math.round(sourceWidth * scale));
  cornerCanvas.height = Math.max(1, Math.round(sourceHeight * scale));
  drawCornerEditor();
}

function showFullCornerView() {
  cornerView = { x0: 0, y0: 0, x1: 1, y1: 1 };
  cornerInstruction.textContent = '先在原图粗调四角，再裁剪放大继续精调';
  configureCornerCanvas();
}

function cropAndEnlargeCornerView() {
  if (!sourceBitmap || cornerPoints.length !== 4) return;
  const xs = cornerPoints.map(point => point[0]);
  const ys = cornerPoints.map(point => point[1]);
  const width = Math.max(0.01, Math.max(...xs) - Math.min(...xs));
  const height = Math.max(0.01, Math.max(...ys) - Math.min(...ys));
  const padX = Math.max(8 / sourceBitmap.width, width * 0.055);
  const padY = Math.max(8 / sourceBitmap.height, height * 0.055);
  cornerView = {
    x0: Math.max(0, Math.min(...xs) - padX),
    y0: Math.max(0, Math.min(...ys) - padY),
    x1: Math.min(1, Math.max(...xs) + padX),
    y1: Math.min(1, Math.max(...ys) + padY),
  };
  cornerInstruction.textContent = '已按当前四角裁剪放大；可继续拖动准星精调';
  configureCornerCanvas();
}

function drawCornerLoupe() {
  if (!sourceBitmap || !cornerPoints[activeCorner]) return;
  const point = cornerPoints[activeCorner];
  const context = cornerLoupeCanvas.getContext('2d');
  const width = cornerLoupeCanvas.width;
  const height = cornerLoupeCanvas.height;
  const sourceX = point[0] * sourceBitmap.width;
  const sourceY = point[1] * sourceBitmap.height;
  const radius = Math.max(6, Math.min(30, Math.min(sourceBitmap.width, sourceBitmap.height) * 0.025));
  const sampleSize = radius * 2;
  const scale = width / sampleSize;
  const sampleX = Math.max(0, sourceX - radius);
  const sampleY = Math.max(0, sourceY - radius);
  const sampleRight = Math.min(sourceBitmap.width, sourceX + radius);
  const sampleBottom = Math.min(sourceBitmap.height, sourceY + radius);
  context.save();
  context.fillStyle = '#d6d6d1';
  context.fillRect(0, 0, width, height);
  context.imageSmoothingEnabled = false;
  context.drawImage(
    sourceBitmap,
    sampleX,
    sampleY,
    sampleRight - sampleX,
    sampleBottom - sampleY,
    (sampleX - (sourceX - radius)) * scale,
    (sampleY - (sourceY - radius)) * scale,
    (sampleRight - sampleX) * scale,
    (sampleBottom - sampleY) * scale,
  );
  const centerX = width / 2;
  const centerY = height / 2;
  const arm = 28;
  const gap = 4;
  context.strokeStyle = draggedCorner === activeCorner ? '#ff4b3e' : '#c7ff2f';
  context.lineWidth = 1;
  context.shadowColor = '#171714';
  context.shadowBlur = 1;
  context.beginPath();
  context.moveTo(centerX - arm, centerY); context.lineTo(centerX - gap, centerY);
  context.moveTo(centerX + gap, centerY); context.lineTo(centerX + arm, centerY);
  context.moveTo(centerX, centerY - arm); context.lineTo(centerX, centerY - gap);
  context.moveTo(centerX, centerY + gap); context.lineTo(centerX, centerY + arm);
  context.stroke();
  context.restore();
  const names = ['左上', '右上', '右下', '左下'];
  cornerLoupeLabel.textContent = `${activeCorner + 1} ${names[activeCorner]} · 局部放大`;
  const [viewX] = sourceToView(point);
  cornerLoupe.classList.toggle('left', viewX > 0.58);
}

function drawCornerEditor() {
  if (!sourceBitmap) return;
  const context = cornerCanvas.getContext('2d');
  context.clearRect(0, 0, cornerCanvas.width, cornerCanvas.height);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = 'high';
  const sourceX = cornerView.x0 * sourceBitmap.width;
  const sourceY = cornerView.y0 * sourceBitmap.height;
  const sourceWidth = (cornerView.x1 - cornerView.x0) * sourceBitmap.width;
  const sourceHeight = (cornerView.y1 - cornerView.y0) * sourceBitmap.height;
  context.drawImage(sourceBitmap, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, cornerCanvas.width, cornerCanvas.height);
  const points = cornerPoints.map(point => {
    const [x, y] = sourceToView(point);
    return [x * cornerCanvas.width, y * cornerCanvas.height];
  });
  const markerScale = Math.max(1, Math.max(cornerCanvas.width, cornerCanvas.height) / 1200);
  const markerArm = 11 * markerScale;
  const markerGap = 2.5 * markerScale;
  context.save();
  context.strokeStyle = '#c7ff2f';
  context.lineWidth = 1.25 * markerScale;
  context.shadowColor = 'rgba(0,0,0,.9)';
  context.shadowBlur = 1.5 * markerScale;
  context.beginPath();
  points.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
  context.closePath();
  context.stroke();
  points.forEach(([x, y], index) => {
    const active = index === draggedCorner;
    context.beginPath();
    context.strokeStyle = active ? '#ff4b3e' : '#c7ff2f';
    context.lineWidth = 1.25 * markerScale;
    context.moveTo(x - markerArm, y);
    context.lineTo(x - markerGap, y);
    context.moveTo(x + markerGap, y);
    context.lineTo(x + markerArm, y);
    context.moveTo(x, y - markerArm);
    context.lineTo(x, y - markerGap);
    context.moveTo(x, y + markerGap);
    context.lineTo(x, y + markerArm);
    context.stroke();
    context.shadowBlur = 2 * markerScale;
    context.fillStyle = active ? '#ff4b3e' : '#c7ff2f';
    context.font = `700 ${10 * markerScale}px ui-monospace`;
    context.textAlign = 'left';
    context.textBaseline = 'bottom';
    context.fillText(String(index + 1), x + 7 * markerScale, y - 7 * markerScale);
  });
  context.restore();
  drawCornerLoupe();
}

async function prepareCornerEditor(file) {
  sourceBitmap?.close?.();
  sourceBitmap = await createImageBitmap(file);
  resetCornerPoints();
  paperTool.classList.remove('hidden');
}

function pointerPosition(event) {
  const box = cornerCanvas.getBoundingClientRect();
  return viewToSource([
    (event.clientX - box.left) / box.width,
    (event.clientY - box.top) / box.height,
  ]);
}

cornerCanvas.addEventListener('pointerdown', event => {
  const [x, y] = pointerPosition(event);
  const box = cornerCanvas.getBoundingClientRect();
  const nearest = cornerPoints.reduce((best, point, index) => {
    const [viewPointX, viewPointY] = sourceToView(point);
    const [viewX, viewY] = sourceToView([x, y]);
    const distance = Math.hypot((viewPointX - viewX) * box.width, (viewPointY - viewY) * box.height);
    return distance < best.distance ? { index, distance } : best;
  }, { index: -1, distance: Infinity });
  if (nearest.distance > 30) return;
  draggedCorner = nearest.index;
  activeCorner = nearest.index;
  cornerCanvas.setPointerCapture(event.pointerId);
  cornerPoints[draggedCorner] = [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  drawCornerEditor();
});
cornerCanvas.addEventListener('pointermove', event => {
  if (draggedCorner < 0) return;
  const [x, y] = pointerPosition(event);
  cornerPoints[draggedCorner] = [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  drawCornerEditor();
});
function releaseCorner(event) {
  if (draggedCorner < 0) return;
  draggedCorner = -1;
  try { cornerCanvas.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
  drawCornerEditor();
}
cornerCanvas.addEventListener('pointerup', releaseCorner);
cornerCanvas.addEventListener('pointercancel', releaseCorner);

function closeCornerEditor() {
  cornerEditor.classList.add('hidden');
  document.body.classList.remove('corner-editor-open');
}

cornerToggle.addEventListener('click', () => {
  if (!rectifyFile || !sourceBitmap) return;
  cornerEditor.classList.remove('hidden');
  document.body.classList.add('corner-editor-open');
  cornerToggle.classList.add('active');
  cornerToggle.textContent = '重新调整四角';
  paperModeLabel.textContent = '按四个锚点做透视还原';
  drawCornerEditor();
});
cornerReset.addEventListener('click', resetCornerPoints);
cornerCrop.addEventListener('click', cropAndEnlargeCornerView);
cornerFull.addEventListener('click', showFullCornerView);
cornerDone.addEventListener('click', async () => {
  if (!rectifyFile) return;
  closeCornerEditor();
  cornerDone.disabled = true;
  cornerToggle.disabled = true;
  paperModeLabel.textContent = '正在生成正方形 PNG…';
  try {
    const buffer = await rectifyFile.arrayBuffer();
    const data = await callWorker('rectify', { buffer, corners: cornerPoints }, [buffer]);
    rectifiedDataUri = data.image_data_uri;
    rectifiedPreview.src = rectifiedDataUri;
    rectifiedMeta.textContent = `${data.width} × ${data.height}px · PNG`;
    rectifiedResult.classList.remove('hidden');
    paperModeLabel.textContent = '正方形图片已生成，可继续调整或下载';
  } catch (error) {
    paperModeLabel.textContent = error.message || '透视校正失败';
  } finally {
    cornerDone.disabled = false;
    cornerToggle.disabled = false;
    cornerToggle.focus();
  }
});
cornerDisable.addEventListener('click', () => {
  closeCornerEditor();
  cornerToggle.focus();
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || cornerEditor.classList.contains('hidden')) return;
  closeCornerEditor();
});

async function selectRectifyFile(file) {
  if (!file) return;
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    paperModeLabel.textContent = '请选择 PNG 或 JPG 图片';
    return;
  }
  rectifyFile = file;
  rectifiedDataUri = '';
  rectifiedResult.classList.add('hidden');
  cornerToggle.disabled = true;
  paperModeLabel.textContent = '正在读取图片…';
  try {
    await prepareCornerEditor(file);
    cornerToggle.disabled = false;
    cornerToggle.textContent = '调整四角锚点';
    paperModeLabel.textContent = `${file.name} · 等待调整四角`;
    cornerToggle.click();
  } catch (_) {
    paperModeLabel.textContent = '无法读取图片';
  }
}

rectifyInput.addEventListener('change', () => selectRectifyFile(rectifyInput.files[0]));

rectifiedDownload.addEventListener('click', () => {
  if (!rectifiedDataUri) return;
  const link = document.createElement('a');
  const sourceName = rectifyFile?.name?.replace(/\.[^.]+$/, '') || 'rectified';
  link.href = rectifiedDataUri;
  link.download = `${sourceName}-square.png`;
  link.click();
});

function selectFile(file) {
  if (!file) return;
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    showError('请选择 PNG 或 JPG 图片。');
    return;
  }
  if (file.size > 12 * 1024 * 1024) {
    showError('图片超过 12 MB，请压缩后重试。');
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  fileName.textContent = file.name;
}

input.addEventListener('change', () => {
  const file = input.files[0];
  fileName.textContent = file?.name || '尚未选择文件';
});

for (const eventName of ['dragenter', 'dragover']) {
  dropZone.addEventListener(eventName, event => {
    event.preventDefault();
    dropZone.classList.add('dragging');
  });
}
for (const eventName of ['dragleave', 'drop']) {
  dropZone.addEventListener(eventName, event => {
    event.preventDefault();
    dropZone.classList.remove('dragging');
  });
}
dropZone.addEventListener('drop', event => selectFile(event.dataTransfer.files[0]));

window.addEventListener('paste', event => {
  const imageItem = Array.from(event.clipboardData?.items || []).find(item =>
    item.kind === 'file' && ['image/png', 'image/jpeg'].includes(item.type)
  );
  if (!imageItem) return;
  event.preventDefault();
  const clipboardFile = imageItem.getAsFile();
  if (!clipboardFile) return;
  const extension = clipboardFile.type === 'image/jpeg' ? 'jpg' : 'png';
  const pastedFile = new File(
    [clipboardFile],
    `clipboard-${new Date().toISOString().replace(/[:.]/g, '-')}.${extension}`,
    { type: clipboardFile.type, lastModified: Date.now() },
  );
  if (document.activeElement === rectifyInput) {
    selectRectifyFile(pastedFile);
    return;
  }
  selectFile(pastedFile);
});

uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!input.files.length || !engineReady) return;

  emptyState.classList.add('hidden');
  resultContent.classList.add('hidden');
  loading.classList.remove('hidden');
  updateProgress(0, '正在准备白底 CP 线稿…');
  warnings.innerHTML = '';
  submitButton.disabled = true;

  try {
    const file = input.files[0];
    const buffer = await file.arrayBuffer();
    const settings = {
      angle_tolerance_mode: angleMode.value,
      angle_tolerance_deg: Number(angleInput.value),
      output_support: Number(document.querySelector('#support').value),
      algebraic_snap_px: Number(document.querySelector('#algebraic').value),
      mv_mode: document.querySelector('#mv-mode').value,
      construction_variants: document.querySelector('#construction-variants').checked,
      paper_corners: null,
    };
    const data = await callWorker('reconstruct', { buffer, settings }, [buffer]);
    currentResult = data;
    renderResult(data);
  } catch (error) {
    showError(error.message || '识别失败');
  } finally {
    loading.classList.add('hidden');
    submitButton.disabled = !engineReady;
  }
});

function showError(message) {
  resultContent.classList.add('hidden');
  loading.classList.add('hidden');
  emptyState.classList.remove('hidden');
  emptyState.querySelector('p').textContent = message;
  emptyState.querySelector('small').textContent = '请检查图片或调整参数后重试';
}

function renderResult(data) {
  currentVariant = data;
  renderVersion(data, data);
  const versions = [data, ...(data.variants || [])];
  versionTabs.classList.toggle('hidden', versions.length < 2);
  versionTabs.innerHTML = versions.map((version, index) =>
    `<button type="button" role="tab" data-version="${index}" class="${index === 0 ? 'active' : ''}" aria-selected="${index === 0}">${escapeHtml(version.label || (index ? `备选 ${index}` : '严格 22.5°'))}</button>`
  ).join('');
  versionTabs.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
    versionTabs.querySelectorAll('button').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
    });
    currentVariant = versions[Number(button.dataset.version)];
    renderVersion(currentVariant, data);
  }));
  resultContent.classList.remove('hidden');
}

function renderVersion(version, root) {
  preview.src = version.overlay_data_uri;
  preview.dataset.overlay = version.overlay_data_uri;
  preview.dataset.clean = version.reconstruction_data_uri;

  warnings.innerHTML = (version.warnings || root.warnings || []).map(message => `<p>${escapeHtml(message)}</p>`).join('');
  const data = version.stats ? version : root;
  const values = [
    ['分析图尺寸', `${data.stats.analysis_size_used ?? 0}px`],
    ['小图自动放大', data.stats.source_upscaled ? `${Number(data.stats.analysis_scale ?? 1).toFixed(2)}×` : '未放大'],
    ['可构造射线', data.stats.constructible_rays ?? data.stats.exact_rays],
    ['初始种子射线', data.stats.construction_seed_rays ?? 0],
    ['唯一代数核心点', data.stats.algebraic_seed_points ?? 0],
    ['纸边交点派生射线', data.stats.boundary_contact_derived_rays ?? 0],
    ['全部派生射线', data.stats.derived_rays ?? 0],
    ['内部线段', data.stats.internal_segments],
    ['cAMV 结构分', `${Math.round((data.stats.camv_structural_completeness_score ?? 0) * 100)}%`],
    ['cAMV 可疑节点', data.stats.camv_structure?.violation_vertex_count ?? 0],
    ['cAMV 补回射线', data.stats.camv_path_committed_arms ?? 0],
    ['cAMV 几何复核轮次', data.stats.camv_path_recheck_rounds ?? 0],
    ['峰线 / 红', data.stats.mv_red_segments ?? 0],
    ['谷线 / 蓝', data.stats.mv_blue_segments ?? 0],
    ['红蓝模糊线', data.stats.mv_ambiguous_segments ?? 0],
    ['cAMV 改色线', data.stats.mv_camv_changed_segments ?? 0],
    ['完整 cAMV 异常', data.stats.camv_full?.violation_vertex_count ?? 0],
    ['忽略自由角度证据', data.stats.angle_rejected_segments],
  ];
  stats.innerHTML = values.map(([label, value]) =>
    `<div><strong>${escapeHtml(value ?? 0)}</strong><span>${label}</span></div>`
  ).join('');

  const anchors = root.anchors || [];
  renderCorePoint(anchors);
  anchorCount.textContent = `${anchors.length} 条`;
  anchorTable.innerHTML = anchors.slice(0, 120).map(anchor => `
    <div>
      <span>${escapeHtml(anchor.source || anchor.side)}</span>
      <code>${escapeHtml(anchor.expression)}</code>
      <b>${Number(anchor.angle).toFixed(1)}°</b>
      <small>误差 ${Number(anchor.snap_error_px).toFixed(2)}px</small>
    </div>
  `).join('');
  const constructions = version.constructions || [];
  constructionDetails.classList.toggle('hidden', constructions.length === 0);
  constructionCount.textContent = `${constructions.length} 条`;
  constructionList.innerHTML = constructions.map(item => `
    <div><strong>${escapeHtml(item.label)}</strong><code>${escapeHtml(item.expression)}</code><small>证据 ${Math.round(Number(item.support) * 100)}%</small></div>
  `).join('');
}

function renderCorePoint(anchors) {
  const core = anchors.find(anchor =>
    String(anchor.source || '').includes('a+b√2')
    && Array.isArray(anchor.coordinate_decimal)
    && Array.isArray(anchor.coordinate_expression)
  );
  const context = corePointCanvas.getContext('2d');
  const width = corePointCanvas.width;
  const height = corePointCanvas.height;
  const margin = 15;
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#fff';
  context.fillRect(0, 0, width, height);
  context.strokeStyle = '#171714';
  context.lineWidth = 1;
  context.strokeRect(margin + .5, margin + .5, width - margin * 2 - 1, height - margin * 2 - 1);
  context.strokeStyle = '#d7d5cc';
  context.beginPath();
  context.moveTo(width / 2, margin); context.lineTo(width / 2, height - margin);
  context.moveTo(margin, height / 2); context.lineTo(width - margin, height / 2);
  context.stroke();
  if (!core) {
    corePointTitle.textContent = '未使用额外核心点';
    corePointCoordinate.textContent = '—';
    return;
  }
  const [normalizedX, normalizedY] = core.coordinate_decimal.map(Number);
  const x = margin + (normalizedX + 1) * .5 * (width - margin * 2);
  const y = margin + (normalizedY + 1) * .5 * (height - margin * 2);
  context.fillStyle = '#c7ff2f';
  context.strokeStyle = '#171714';
  context.lineWidth = 2;
  context.beginPath();
  context.arc(x, y, 6, 0, Math.PI * 2);
  context.fill();
  context.stroke();
  context.beginPath();
  context.moveTo(x - 11, y); context.lineTo(x + 11, y);
  context.moveTo(x, y - 11); context.lineTo(x, y + 11);
  context.stroke();
  const [expressionX, expressionY] = core.coordinate_expression;
  corePointTitle.textContent = core.source;
  corePointCoordinate.textContent = `x = ${expressionX} · y = ${expressionY}`;
}

document.querySelectorAll('.view-tabs button').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.view-tabs button').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
    });
    preview.src = button.dataset.view === 'overlay' ? preview.dataset.overlay : preview.dataset.clean;
  });
});

downloadButton.addEventListener('click', () => {
  if (!currentResult || !currentVariant) return;
  const sourceName = input.files[0]?.name?.replace(/\.[^.]+$/, '') || 'reconstructed';
  const blob = new Blob([currentVariant.cp], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  const suffix = currentVariant.id && currentVariant.id !== 'strict' ? `-${currentVariant.id}` : '';
  link.download = `${sourceName}${suffix}.cp`;
  link.click();
  URL.revokeObjectURL(url);
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}
