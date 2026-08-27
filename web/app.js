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
const boundaryRelations = document.querySelector('#boundary-relations');
const boundaryRelationCount = document.querySelector('#boundary-relation-count');
const boundaryRelationList = document.querySelector('#boundary-relation-list');
const boundaryRelationStatus = document.querySelector('#boundary-relation-status');
const boundaryRelationIntro = document.querySelector('#boundary-relation-intro');
const boundaryRelationHistory = document.querySelector('#boundary-relation-history');
const boundaryRelationHistoryList = document.querySelector('#boundary-relation-history-list');
const boundaryRelationUndo = document.querySelector('#boundary-relation-undo');
const topologyPointLayer = document.querySelector('#topology-point-layer');
const topologyPointTooltip = document.querySelector('#topology-point-tooltip');
const topologyPointConfirmation = document.querySelector('#topology-point-confirmation');
const topologyPointConfirmationTitle = document.querySelector('#topology-point-confirmation-title');
const topologyPointConfirmationCoordinate = document.querySelector('#topology-point-confirmation-coordinate');
const topologyPointConfirmationNote = document.querySelector('#topology-point-confirmation-note');
const topologyPointConfirm = document.querySelector('#topology-point-confirm');
const topologyPointCancel = document.querySelector('#topology-point-cancel');
const mvSegmentLayer = document.querySelector('#mv-segment-layer');
const mvEditor = document.querySelector('#mv-editor');
const mvEditorCount = document.querySelector('#mv-editor-count');
const mvEditorProgress = document.querySelector('#mv-editor-progress');
const mvEditorStatus = document.querySelector('#mv-editor-status');
const mvEditorCurrent = document.querySelector('#mv-editor-current');
const mvNextUnassigned = document.querySelector('#mv-next-unassigned');
const mvBrushButtons = Array.from(document.querySelectorAll('.mv-editor-toolbar [data-line-type]'));
const mvUndo = document.querySelector('#mv-undo');
const mvClear = document.querySelector('#mv-clear');
const mvApply = document.querySelector('#mv-apply');
const loadingStage = document.querySelector('#loading-stage');
const loadingNote = document.querySelector('#loading-note');
const loadingProgress = document.querySelector('#loading-progress');
const loadingProgressValue = document.querySelector('#loading-progress-value');
const corePointCanvas = document.querySelector('#core-point-canvas');
const corePointCard = document.querySelector('#core-point-card');
const corePointTitle = document.querySelector('#core-point-title');
const corePointCoordinate = document.querySelector('#core-point-coordinate');
const anchorDetails = document.querySelector('#anchor-details');
const legacyReconstructPanel = document.querySelector('#legacy-reconstruct-panel');
const legacyReconstructButton = document.querySelector('#legacy-reconstruct');
const resultEyebrow = document.querySelector('#result-eyebrow');
const resultTitle = document.querySelector('#result-title');
const previewFigure = preview.closest('.preview');

const WEB_ENGINE_VERSION = '20260826-guided-segment-mv-v1';
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
let pendingTopologyPointId = '';
let guidedMvBrush = 2;
let guidedMvDirty = false;
let guidedMvUndoStack = [];
let guidedMvPainting = false;
let guidedMvStrokeBefore = null;
let guidedMvSelectedSegmentId = '';

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
    if (data.stage === 'reconstruct' || data.stage === 'analyze-raw') {
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
  if (legacyReconstructButton) legacyReconstructButton.disabled = !engineReady;
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
bindRange('#construction-offset', '#construction-offset-value', value => `${value.toFixed(1)}px`);

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

function readSettings() {
  return {
    angle_tolerance_mode: angleMode.value,
    angle_tolerance_deg: Number(angleInput.value),
    output_support: Number(document.querySelector('#support').value),
    algebraic_snap_px: Number(document.querySelector('#algebraic').value),
    construction_offset_tolerance_px: Number(document.querySelector('#construction-offset').value),
    mv_mode: document.querySelector('#mv-mode').value,
    construction_variants: document.querySelector('#construction-variants').checked,
    paper_corners: null,
  };
}

function setResultOutputReady(ready) {
  downloadButton.disabled = !ready;
  downloadButton.classList.toggle('hidden', !ready);
  document.dispatchEvent(new CustomEvent('oriredraw:result-state', {
    detail: { outputReady: Boolean(ready) },
  }));
}

function beginImageFlow({ legacy = false } = {}) {
  emptyState.classList.add('hidden');
  resultContent.classList.add('hidden');
  loading.classList.remove('hidden');
  updateProgress(0, legacy ? '正在启动旧版严格重建…' : '正在准备原图折痕分析…');
  loadingNote.textContent = legacy
    ? '兼容流程会执行完整严格重建，复杂图可能需要十几分钟，请保持页面开启'
    : '通常约一秒完成；此阶段不会运行旧版严格重建';
  warnings.innerHTML = '';
  setResultOutputReady(false);
  submitButton.disabled = true;
  if (legacyReconstructButton) legacyReconstructButton.disabled = true;
}

function endImageFlow() {
  loading.classList.add('hidden');
  submitButton.disabled = !engineReady;
  if (legacyReconstructButton) legacyReconstructButton.disabled = !engineReady;
}

async function runImageFlow(type) {
  if (!input.files.length || !engineReady) return;
  const legacy = type === 'reconstruct';
  beginImageFlow({ legacy });

  try {
    const file = input.files[0];
    const buffer = await file.arrayBuffer();
    const data = await callWorker(type, { buffer, settings: readSettings() }, [buffer]);
    guidedMvDirty = false;
    guidedMvUndoStack = [];
    guidedMvPainting = false;
    guidedMvStrokeBefore = null;
    guidedMvSelectedSegmentId = '';
    currentResult = data;
    renderResult(data);
  } catch (error) {
    showError(error.message || '识别失败');
  } finally {
    endImageFlow();
  }
}

uploadForm.addEventListener('submit', event => {
  event.preventDefault();
  runImageFlow('analyze-raw');
});

legacyReconstructButton?.addEventListener('click', () => runImageFlow('reconstruct'));

function showError(message) {
  resultContent.classList.add('hidden');
  loading.classList.add('hidden');
  emptyState.classList.remove('hidden');
  emptyState.querySelector('p').textContent = message;
  emptyState.querySelector('small').textContent = '请检查图片或调整参数后重试';
  setResultOutputReady(false);
}

function isRawPrimaryResult(data) {
  return data?.mode === 'guided_raw_primary_v1';
}

function configureResultView({ rawPrimary }) {
  previewFigure?.classList.remove('playback-active');
  const overlayButton = document.querySelector('.view-tabs button[data-view="overlay"]');
  const cleanButton = document.querySelector('.view-tabs button[data-view="clean"]');
  const playbackButton = document.querySelector('.view-tabs button[data-view="playback"]');
  if (overlayButton) overlayButton.textContent = rawPrimary ? '原图折痕证据' : '叠加检查';
  if (cleanButton) cleanButton.textContent = rawPrimary ? '有限拓扑' : '纯重绘';
  playbackButton?.classList.toggle('hidden', rawPrimary);
  document.querySelectorAll('.view-tabs button').forEach(button => {
    const active = button.dataset.view === 'overlay';
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
}

function renderRawPrimaryStats(data) {
  const raw = data.shadow_search?.raw_crease_evidence || {};
  const topology = data.shadow_search?.raw_crease_topology || {};
  const values = [
    ['分析图尺寸', `${data.stats?.analysis_size_used ?? 0}px`],
    ['原图有限折痕', data.stats?.raw_crease_count ?? raw.line_count ?? 0],
    ['有限证据线段', data.stats?.raw_finite_segment_count ?? raw.finite_segment_count ?? 0],
    ['拓扑点', data.stats?.raw_topology_point_count ?? topology.point_count ?? 0],
    ['拓扑线段', data.stats?.raw_topology_segment_count ?? topology.segment_count ?? 0],
    ['纸边接触点', data.stats?.raw_boundary_contact_count ?? topology.boundary_contacts?.length ?? 0],
    ['候选起点关系', data.stats?.boundary_relation_candidate_count ?? 0],
    ['分析耗时', `${(Number(data.stats?.raw_analysis_duration_ms || 0) / 1000).toFixed(2)}s`],
  ];
  stats.innerHTML = values.map(([label, value]) =>
    `<div><strong>${escapeHtml(value ?? 0)}</strong><span>${label}</span></div>`
  ).join('');
}

function renderRawPrimaryResult(data) {
  currentVariant = null;
  resultContent.classList.add('raw-primary');
  resultEyebrow.textContent = 'GUIDED / RAW IMAGE';
  resultTitle.textContent = '选择取线起点';
  configureResultView({ rawPrimary: true });
  preview.src = data.overlay_data_uri;
  preview.dataset.overlay = data.overlay_data_uri;
  preview.dataset.clean = data.reconstruction_data_uri;
  warnings.innerHTML = (data.warnings || []).map(message => `<p>${escapeHtml(message)}</p>`).join('');
  renderRawPrimaryStats(data);
  versionTabs.classList.add('hidden');
  versionTabs.replaceChildren();
  corePointCard?.classList.add('hidden');
  anchorDetails?.classList.add('hidden');
  constructionDetails.classList.add('hidden');
  legacyReconstructPanel?.classList.remove('hidden');
  const guided = data.shadow_search?.guided_boundary || null;
  syncGuidedMvAssignments(data, guided);
  renderBoundaryRelations(data);
  if (boundaryRelations && !boundaryRelations.classList.contains('hidden')) boundaryRelations.open = true;
  syncGuidedOutputState(data, guided);
  resultContent.classList.remove('hidden');
}

function renderResult(data) {
  if (isRawPrimaryResult(data)) {
    renderRawPrimaryResult(data);
    return;
  }
  currentVariant = data;
  resultContent.classList.remove('raw-primary');
  resultEyebrow.textContent = 'REDRAW';
  resultTitle.textContent = '严格重绘结果';
  configureResultView({ rawPrimary: false });
  corePointCard?.classList.remove('hidden');
  anchorDetails?.classList.remove('hidden');
  legacyReconstructPanel?.classList.add('hidden');
  renderVersion(data, data);
  renderBoundaryRelations(data);
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
  setResultOutputReady(typeof data.cp === 'string' && data.cp.length > 0);
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
    ['纸框比例校正', data.stats.aspect_ratio_corrected
      ? `${Number(data.stats.source_paper_aspect_ratio ?? 1).toFixed(3)}× → 1:1`
      : '无需校正'],
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
    ['局部偏移保留线', data.stats.observed_proxy_edges_preserved ?? 0],
    ['最大验证偏移 px', data.stats.observed_proxy_max_shift_px ?? 0],
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

function guidedSelectionSteps(report) {
  if (Array.isArray(report?.selection_steps)) {
    return report.selection_steps
      .filter(step => step && ['boundary_relation', 'topology_point'].includes(step.kind) && step.id)
      .map(step => ({ kind: String(step.kind), id: String(step.id) }));
  }
  if (Array.isArray(report?.selected_relation_ids)) {
    return report.selected_relation_ids.map(String).filter(Boolean)
      .map(id => ({ kind: 'boundary_relation', id }));
  }
  const selectedId = report?.selected_relation?.id;
  return selectedId ? [{ kind: 'boundary_relation', id: String(selectedId) }] : [];
}

function guidedSelectionIds(report) {
  return guidedSelectionSteps(report)
    .filter(step => step.kind === 'boundary_relation')
    .map(step => step.id);
}

const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';

function normalizeGuidedMvAssignments(rawAssignments) {
  const normalized = {};
  if (!rawAssignments || typeof rawAssignments !== 'object') return normalized;
  for (const [rawId, rawAssignment] of Object.entries(rawAssignments)) {
    const id = String(rawId || '').trim();
    if (!id) continue;
    const wrapped = rawAssignment && typeof rawAssignment === 'object';
    const lineType = Number(wrapped ? rawAssignment.line_type : rawAssignment);
    if (![2, 3].includes(lineType)) continue;
    const source = wrapped && typeof rawAssignment.source === 'string' && rawAssignment.source
      ? rawAssignment.source
      : 'user_confirmed';
    normalized[id] = { line_type: lineType, source };
  }
  return normalized;
}

function guidedMvAssignments(root) {
  return normalizeGuidedMvAssignments(root?.shadow_search?.guided_segment_line_types);
}

function writeGuidedMvAssignments(root, assignments) {
  if (!root) return {};
  root.shadow_search = root.shadow_search || {};
  const normalized = normalizeGuidedMvAssignments(assignments);
  root.shadow_search.guided_segment_line_types = normalized;
  return normalized;
}

function syncGuidedMvAssignments(root, report) {
  if (!root) return {};
  if (!report) return writeGuidedMvAssignments(root, guidedMvAssignments(root));
  const contract = report.cp_output_contract || {};
  let accepted = Object.prototype.hasOwnProperty.call(report, 'segment_line_type_assignments')
    ? report.segment_line_type_assignments
    : contract.segment_line_type_assignments;
  if (!accepted || typeof accepted !== 'object') {
    accepted = {};
    for (const segment of contract.candidate_segments || []) {
      if (
        [2, 3].includes(Number(segment?.line_type))
        && ['explicit_segment_assignment', 'source_image_color_evidence', 'user_confirmed']
          .includes(String(segment?.line_type_source || ''))
      ) {
        accepted[String(segment.id)] = {
          line_type: Number(segment.line_type),
          source: String(segment.line_type_source),
        };
      }
    }
  }
  return writeGuidedMvAssignments(root, accepted);
}

function guidedMvCandidateSegments(report) {
  const candidates = report?.cp_output_contract?.candidate_segments;
  if (!Array.isArray(candidates)) return [];
  return candidates.filter(segment => {
    const coordinates = [
      ...(Array.isArray(segment?.start_cp) ? segment.start_cp : []),
      ...(Array.isArray(segment?.end_cp) ? segment.end_cp : []),
    ].map(Number);
    return segment?.id && coordinates.length === 4 && coordinates.every(Number.isFinite);
  });
}

const GUIDED_MV_AUTOMATIC_SOURCE = 'source_image_color_evidence';
const GUIDED_MV_TRUSTED_SOURCES = new Set([
  'explicit_segment_assignment',
  GUIDED_MV_AUTOMATIC_SOURCE,
  'user_confirmed',
]);

function guidedMvEffectiveAssignment(root, segment) {
  const segmentId = String(segment?.id || '');
  const assignments = guidedMvAssignments(root);
  const explicit = assignments[segmentId];
  if (explicit) return explicit;
  const lineType = Number(segment?.line_type);
  const source = String(segment?.line_type_source || '');
  if ([2, 3].includes(lineType) && GUIDED_MV_TRUSTED_SOURCES.has(source)) {
    return { line_type: lineType, source };
  }
  return null;
}

function guidedMvSegmentIsAutomatic(root, segment) {
  const assignment = guidedMvEffectiveAssignment(root, segment);
  return assignment?.source === GUIDED_MV_AUTOMATIC_SOURCE
    && [2, 3].includes(Number(assignment.line_type));
}

function guidedMvEditableSegments(root, report) {
  return guidedMvCandidateSegments(report)
    .filter(segment => !guidedMvSegmentIsAutomatic(root, segment));
}

function guidedMvResolvedAssignments(root, report) {
  const resolved = {};
  for (const segment of guidedMvCandidateSegments(report)) {
    const assignment = guidedMvEffectiveAssignment(root, segment);
    if (assignment) resolved[String(segment.id)] = assignment;
  }
  return resolved;
}

function syncGuidedOutputState(root, report) {
  if (!root) return;
  const ready = Boolean(
    report?.output_ready
    && typeof report.cp === 'string'
    && report.cp.length,
  );
  root.cp = ready ? report.cp : null;
  root.output_ready = ready;
  root.output_unchanged = !ready;
  currentVariant = ready ? root : null;
  setResultOutputReady(ready);
}

function invalidateGuidedOutput(root) {
  if (!root) return;
  root.cp = null;
  root.output_ready = false;
  root.output_unchanged = true;
  const report = root.shadow_search?.guided_boundary;
  if (report) {
    report.cp = null;
    report.output_ready = false;
    report.output_unchanged = true;
  }
  currentVariant = null;
  setResultOutputReady(false);
}

function setGuidedMvBrush(lineType) {
  guidedMvBrush = [2, 3].includes(Number(lineType)) ? Number(lineType) : 0;
  for (const button of mvBrushButtons) {
    const active = Number(button.dataset.lineType) === guidedMvBrush;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
}

function guidedMvTypeLabel(lineType) {
  if (Number(lineType) === 2) return '山线 M';
  if (Number(lineType) === 3) return '谷线 V';
  return '未标注';
}

function setGuidedMvSelectedSegment(segmentId, lineType) {
  guidedMvSelectedSegmentId = String(segmentId || '');
  mvSegmentLayer?.querySelectorAll('.mv-segment').forEach(group => {
    group.classList.toggle('selected', group.dataset.segmentId === guidedMvSelectedSegmentId);
  });
  if (mvEditorCurrent) {
    mvEditorCurrent.textContent = guidedMvSelectedSegmentId
      ? `${guidedMvSelectedSegmentId} · ${guidedMvTypeLabel(lineType)}`
      : '尚未选择线段';
  }
}

function guidedMvSegmentGroup(segmentId) {
  return Array.from(mvSegmentLayer?.querySelectorAll('.mv-segment') || [])
    .find(group => group.dataset.segmentId === String(segmentId)) || null;
}

function focusGuidedMvSegment(root, segmentId) {
  const group = guidedMvSegmentGroup(segmentId);
  if (!group) return false;
  const report = root?.shadow_search?.guided_boundary;
  const segment = guidedMvCandidateSegments(report)
    .find(item => String(item.id) === String(segmentId));
  const lineType = guidedMvEffectiveAssignment(root, segment)?.line_type || 0;
  setGuidedMvSelectedSegment(segmentId, lineType);
  group.focus();
  return true;
}

function focusNextGuidedMvUnassigned(root, report) {
  const segments = guidedMvEditableSegments(root, report);
  if (!segments.length) return false;
  const currentIndex = segments.findIndex(
    segment => String(segment.id) === guidedMvSelectedSegmentId,
  );
  for (let offset = 1; offset <= segments.length; offset += 1) {
    const index = (currentIndex + offset + segments.length) % segments.length;
    const segmentId = String(segments[index].id);
    if (!guidedMvEffectiveAssignment(root, segments[index])) {
      return focusGuidedMvSegment(root, segmentId);
    }
  }
  return false;
}

function setGuidedMvSegmentVisual(segmentId, lineType) {
  const group = guidedMvSegmentGroup(segmentId);
  if (!group) return;
  const normalizedType = [2, 3].includes(Number(lineType)) ? Number(lineType) : 0;
  group.dataset.lineType = String(normalizedType);
  group.setAttribute(
    'aria-label',
    `线段 ${segmentId}，${guidedMvTypeLabel(normalizedType)}；按 M、V 或 U 可直接修改`,
  );
  const title = group.querySelector('title');
  if (title) title.textContent = `${segmentId} · ${guidedMvTypeLabel(normalizedType)}`;
  if (guidedMvSelectedSegmentId === String(segmentId)) {
    setGuidedMvSelectedSegment(segmentId, normalizedType);
  }
}

function updateGuidedMvSummary(root, report) {
  const segments = guidedMvCandidateSegments(report);
  const assignments = guidedMvAssignments(root);
  const editableSegments = guidedMvEditableSegments(root, report);
  const editableIds = new Set(editableSegments.map(segment => String(segment.id)));
  let assigned = 0;
  let automatic = 0;
  let mountain = 0;
  let valley = 0;
  for (const segment of segments) {
    const assignment = guidedMvEffectiveAssignment(root, segment);
    if (!assignment) continue;
    assigned += 1;
    if (assignment.source === GUIDED_MV_AUTOMATIC_SOURCE) automatic += 1;
    if (Number(assignment.line_type) === 2) mountain += 1;
    if (Number(assignment.line_type) === 3) valley += 1;
  }
  const missing = Math.max(0, segments.length - assigned);
  const manualAssigned = Object.entries(assignments)
    .filter(([id, value]) => editableIds.has(id) && [2, 3].includes(Number(value?.line_type)))
    .length;
  const automaticNote = automatic > 0
    ? `原图红蓝证据已自动判定 ${automatic} 条；`
    : '';
  const busy = boundaryRelationList?.dataset.busy === 'true';

  if (mvEditorCount) mvEditorCount.textContent = `${assigned} / ${segments.length}`;
  if (mvEditorProgress) {
    mvEditorProgress.max = Math.max(1, segments.length);
    mvEditorProgress.value = assigned;
  }
  if (mvUndo) mvUndo.disabled = busy || guidedMvUndoStack.length === 0;
  if (mvClear) mvClear.disabled = busy || manualAssigned === 0;
  if (mvApply) mvApply.disabled = busy || segments.length === 0;
  if (mvNextUnassigned) mvNextUnassigned.disabled = busy || missing === 0;
  if (!mvEditorStatus) return;

  if (guidedMvDirty && missing > 0) {
    mvEditorStatus.textContent = `${automaticNote}已标注 ${assigned} 条（山 ${mountain} / 谷 ${valley}），还差 ${missing} 条含混线段；系统不会自动猜测 M/V。`;
  } else if (guidedMvDirty) {
    mvEditorStatus.textContent = `${automaticNote}全部 ${segments.length} 条已确认（人工 ${manualAssigned} 条，山 ${mountain} / 谷 ${valley}）。点击“应用人工确认并检查导出”统一重算一次。`;
  } else if (report?.output_ready) {
    mvEditorStatus.textContent = `${automaticNote}全部 ${segments.length} 条已确认（人工 ${manualAssigned} 条，山 ${mountain} / 谷 ${valley}），硬性导出检查已通过，可以下载 .cp。`;
  } else if (missing > 0) {
    mvEditorStatus.textContent = `${automaticNote}有限折痕端点已经闭合；请人工确认剩余 ${missing} 条含混线段。系统不会自动猜测 M/V。`;
  } else {
    const blockerCodes = (report?.cp_output_contract?.blockers || [])
      .map(item => item?.code)
      .filter(Boolean);
    mvEditorStatus.textContent = blockerCodes.length
      ? `M/V 已满配，但仍有导出检查未通过：${blockerCodes.join('、')}`
      : 'M/V 已满配；请应用标注完成导出检查。';
  }
}

function rememberGuidedMvBefore(segmentId, previous) {
  const snapshot = previous ? { ...previous } : null;
  if (guidedMvPainting && guidedMvStrokeBefore) {
    if (!guidedMvStrokeBefore.has(segmentId)) {
      guidedMvStrokeBefore.set(segmentId, snapshot);
    }
    return;
  }
  guidedMvUndoStack.push([{ segmentId, previous: snapshot }]);
}

function paintGuidedMvSegment(root, report, segmentId, lineType) {
  if (!root || !report || boundaryRelationList?.dataset.busy === 'true') return;
  const id = String(segmentId || '');
  const segment = guidedMvCandidateSegments(report)
    .find(item => String(item.id) === id);
  if (!segment || guidedMvSegmentIsAutomatic(root, segment)) return;
  const assignments = guidedMvAssignments(root);
  const previous = assignments[id] || null;
  const normalizedType = [2, 3].includes(Number(lineType)) ? Number(lineType) : 0;
  if (
    (normalizedType === 0 && !previous)
    || (
      normalizedType > 0
      && previous?.line_type === normalizedType
      && previous?.source === 'user_confirmed'
    )
  ) return;

  rememberGuidedMvBefore(id, previous);
  if (normalizedType > 0) {
    assignments[id] = { line_type: normalizedType, source: 'user_confirmed' };
  } else {
    delete assignments[id];
  }
  writeGuidedMvAssignments(root, assignments);
  guidedMvDirty = true;
  invalidateGuidedOutput(root);
  setGuidedMvSegmentVisual(id, normalizedType);
  setGuidedMvSelectedSegment(id, normalizedType);
  updateGuidedMvSummary(root, report);
}

function beginGuidedMvStroke() {
  if (guidedMvPainting) return;
  guidedMvPainting = true;
  guidedMvStrokeBefore = new Map();
}

function finishGuidedMvStroke() {
  if (!guidedMvPainting) return;
  guidedMvPainting = false;
  if (guidedMvStrokeBefore?.size) {
    guidedMvUndoStack.push(
      Array.from(guidedMvStrokeBefore, ([segmentId, previous]) => ({ segmentId, previous })),
    );
  }
  guidedMvStrokeBefore = null;
  updateGuidedMvSummary(currentResult, currentResult?.shadow_search?.guided_boundary);
}

function paintGuidedMvAtPointer(event) {
  if (!guidedMvPainting || !currentResult) return;
  const hit = document.elementFromPoint(event.clientX, event.clientY);
  const group = hit instanceof Element ? hit.closest('.mv-segment') : null;
  if (!group?.dataset.segmentId) return;
  paintGuidedMvSegment(
    currentResult,
    currentResult.shadow_search?.guided_boundary,
    group.dataset.segmentId,
    guidedMvBrush,
  );
}

function renderGuidedMvOverlay(root, report) {
  if (!mvSegmentLayer) return;
  mvSegmentLayer.replaceChildren();
  const segments = guidedMvCandidateSegments(report);
  mvSegmentLayer.classList.toggle('hidden', segments.length === 0);
  if (!segments.length) return;

  for (const segment of segments) {
    const [x1, y1] = segment.start_cp.map(Number);
    const [x2, y2] = segment.end_cp.map(Number);
    const segmentId = String(segment.id);
    const assignment = guidedMvEffectiveAssignment(root, segment);
    const lineType = Number(assignment?.line_type || 0);
    const source = String(assignment?.source || segment.line_type_source || '');
    const automatic = source === GUIDED_MV_AUTOMATIC_SOURCE
      && [2, 3].includes(lineType);
    const group = document.createElementNS(SVG_NAMESPACE, 'g');
    group.classList.add('mv-segment');
    group.classList.toggle('automatic', automatic);
    group.dataset.segmentId = segmentId;
    group.dataset.lineType = String(lineType);
    group.dataset.lineSource = source;
    group.setAttribute('tabindex', automatic ? '-1' : '0');
    group.setAttribute('role', automatic ? 'img' : 'button');
    group.setAttribute('aria-disabled', automatic ? 'true' : 'false');
    group.setAttribute(
      'aria-label',
      automatic
        ? `线段 ${segmentId}，${guidedMvTypeLabel(lineType)}；原图红蓝证据自动判定，不可手动修改`
        : `线段 ${segmentId}，${guidedMvTypeLabel(lineType)}；按 M、V 或 U 可直接修改`,
    );
    group.classList.toggle('selected', segmentId === guidedMvSelectedSegmentId);

    const title = document.createElementNS(SVG_NAMESPACE, 'title');
    title.textContent = `${segmentId} · ${guidedMvTypeLabel(lineType)}`;
    const hit = document.createElementNS(SVG_NAMESPACE, 'line');
    hit.classList.add('mv-segment-hit');
    const visible = document.createElementNS(SVG_NAMESPACE, 'line');
    visible.classList.add('mv-segment-visible');
    for (const line of [hit, visible]) {
      line.setAttribute('x1', String(x1));
      line.setAttribute('y1', String(y1));
      line.setAttribute('x2', String(x2));
      line.setAttribute('y2', String(y2));
    }

    group.addEventListener('pointerdown', event => {
      if (automatic) return;
      event.preventDefault();
      group.focus();
      beginGuidedMvStroke();
      paintGuidedMvSegment(root, report, segmentId, guidedMvBrush);
    });
    group.addEventListener('pointerenter', () => {
      if (automatic) return;
      setGuidedMvSelectedSegment(
        segmentId,
        guidedMvEffectiveAssignment(root, segment)?.line_type || 0,
      );
      if (guidedMvPainting) {
        paintGuidedMvSegment(root, report, segmentId, guidedMvBrush);
      }
    });
    group.addEventListener('focus', () => {
      if (automatic) return;
      setGuidedMvSelectedSegment(
        segmentId,
        guidedMvEffectiveAssignment(root, segment)?.line_type || 0,
      );
    });
    group.addEventListener('keydown', event => {
      if (automatic) return;
      const key = event.key.toLowerCase();
      const directType = key === 'm' ? 2 : key === 'v' ? 3 : key === 'u' ? 0 : null;
      if (directType !== null) {
        event.preventDefault();
        event.stopPropagation();
        setGuidedMvBrush(directType);
        paintGuidedMvSegment(root, report, segmentId, directType);
        if (directType > 0) focusNextGuidedMvUnassigned(root, report);
      } else if (key === 'enter' || key === ' ') {
        event.preventDefault();
        paintGuidedMvSegment(root, report, segmentId, guidedMvBrush);
      } else if (['arrowright', 'arrowdown', 'arrowleft', 'arrowup'].includes(key)) {
        event.preventDefault();
        const groups = Array.from(mvSegmentLayer.querySelectorAll('.mv-segment'))
          .filter(item => item.getAttribute('aria-disabled') !== 'true');
        const currentIndex = groups.indexOf(group);
        const delta = ['arrowright', 'arrowdown'].includes(key) ? 1 : -1;
        const next = groups[(currentIndex + delta + groups.length) % groups.length];
        focusGuidedMvSegment(root, next?.dataset.segmentId || segmentId);
      }
    });
    group.append(title, hit, visible);
    mvSegmentLayer.append(group);
  }
}

function renderGuidedMvEditor(root, report) {
  if (!mvEditor || !mvSegmentLayer) return;
  const segments = report?.phase === 'complete_existing_creases'
    ? guidedMvCandidateSegments(report)
    : [];
  if (!segments.length) {
    mvEditor.classList.add('hidden');
    mvEditor.setAttribute('aria-hidden', 'true');
    mvSegmentLayer.classList.add('hidden');
    mvSegmentLayer.replaceChildren();
    return;
  }
  renderGuidedMvOverlay(root, report);
  const editableSegments = guidedMvEditableSegments(root, report);
  const visible = editableSegments.length > 0;
  mvEditor.classList.toggle('hidden', !visible);
  mvEditor.setAttribute('aria-hidden', String(!visible));
  if (!visible) return;
  updateGuidedMvSummary(root, report);
}

function renderBoundaryRelationHistory(report) {
  if (!boundaryRelationHistory || !boundaryRelationHistoryList || !boundaryRelationUndo) return;
  const selectedSteps = guidedSelectionSteps(report);
  boundaryRelationHistory.classList.toggle('hidden', selectedSteps.length === 0);
  boundaryRelationHistoryList.replaceChildren();
  if (!selectedSteps.length) return;
  const history = Array.isArray(report?.selection_history)
    ? report.selection_history
    : (report?.selected_relations || []).map((relation, index) => ({
        selection_round: index + 1,
        label: relation.label || relation.id,
        step_kind: 'boundary_relation',
      }));
  for (const entry of history) {
    const chip = document.createElement('span');
    const isPoint = entry.step_kind === 'topology_point';
    const expression = Array.isArray(entry.coordinate_expression)
      ? ` ≈ (${entry.coordinate_expression.join(', ')})`
      : '';
    chip.textContent = `第 ${entry.selection_round ?? '—'} 步 · ${isPoint ? '内部点' : (entry.label || entry.id || '边界关系')}${expression}`;
    boundaryRelationHistoryList.append(chip);
  }
  const sideLength = report?.global_side_length?.expression || '';
  const heading = boundaryRelationHistory.querySelector('strong');
  if (heading) heading.textContent = sideLength ? `同一条取线链 · L=${sideLength}` : '同一条取线链';
  boundaryRelationUndo.textContent = selectedSteps.length > 1
    ? `撤销第 ${selectedSteps.length} 步`
    : '撤销起点';
}

function clearTopologyPointConfirmation() {
  pendingTopologyPointId = '';
  topologyPointConfirmation?.classList.add('hidden');
  topologyPointLayer?.querySelectorAll('.topology-point-marker.pending')
    .forEach(marker => marker.classList.remove('pending'));
}

function guidedPointMaximum(report, root) {
  const values = [
    report?.raw_topology?.maximum_coordinate_px,
    root?.shadow_search?.raw_topology?.maximum_coordinate_px,
    root?.shadow_search?.raw_crease_topology?.maximum_coordinate_px,
    root?.shadow_search?.raw_crease_evidence?.maximum_coordinate_px,
    root?.shadow_search?.raw_boundary_evidence?.maximum_coordinate_px,
  ];
  const maximum = values
    .map(value => Number(value))
    .find(value => Number.isFinite(value) && value > 0);
  return maximum || 1;
}

function guidedPointCoordinateExpressions(point) {
  const coordinate = Array.isArray(point?.project_coordinate)
    ? point.project_coordinate
    : Array.isArray(point?.coordinate_expression)
    ? point.coordinate_expression
    : [];
  return coordinate.map(value => {
    if (value && typeof value === 'object') return String(value.expression || '—');
    return value == null ? '—' : String(value);
  });
}

function buildBoundaryRelationPointCandidates(relations) {
  const points = new Map();
  for (const relation of Array.isArray(relations) ? relations : []) {
    const relationId = String(relation?.id || relation?.label || 'boundary-relation');
    const relationLabel = String(relation?.label || '边界关系');
    const relationPriority = relation?.next_priority ?? relation?.priority;
    const sideLength = String(
      relation?.recommended_coordinate_gauge?.side_length?.expression || '',
    );
    for (const point of Array.isArray(relation?.points) ? relation.points : []) {
      const observed = Array.isArray(point?.observed_point_px)
        ? point.observed_point_px
        : point?.point_px;
      if (!Array.isArray(observed) || observed.length < 2) continue;
      const x = Number(observed[0]);
      const y = Number(observed[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const key = `${x.toFixed(6)}:${y.toFixed(6)}`;
      let candidate = points.get(key);
      if (!candidate) {
        candidate = {
          id: `boundary-point:${key}`,
          kind: 'boundary_relation_point',
          label: String(point?.label || '边界候选点'),
          observed_point_px: [x, y],
          boundary_assignments: [],
        };
        points.set(key, candidate);
      }
      const coordinateExpression = guidedPointCoordinateExpressions(point);
      const assignmentKey = [
        relationId,
        sideLength,
        coordinateExpression.join(','),
      ].join('|');
      if (candidate.boundary_assignments.some(item => item.key === assignmentKey)) continue;
      candidate.boundary_assignments.push({
        key: assignmentKey,
        relationId,
        relationLabel,
        relationPriority,
        coordinate_expression: coordinateExpression,
        sideLength,
        imageResidualPx: Number(point?.image_residual_px || 0),
      });
    }
  }
  return [...points.values()];
}

function showTopologyPointTooltip(candidate, report, root = null) {
  if (!topologyPointTooltip) return;
  const point = Array.isArray(candidate.observed_point_px) ? candidate.observed_point_px : [0, 0];
  const maximum = guidedPointMaximum(report, root);
  const left = Math.max(2, Math.min(98, Number(point[0]) / maximum * 100));
  const top = Math.max(2, Math.min(98, Number(point[1]) / maximum * 100));
  if (candidate.kind === 'boundary_relation_point') {
    const assignments = Array.isArray(candidate.boundary_assignments)
      ? candidate.boundary_assignments
      : [];
    const assignmentText = assignments
      .slice(0, 4)
      .map(item => {
        const coordinate = (item.coordinate_expression || []).join(', ') || '—';
        const gauge = item.sideLength ? ` · L=${item.sideLength}` : '';
        const priority = item.relationPriority ? `优先 ${item.relationPriority} ` : '';
        return `${priority}${item.relationLabel}: (${coordinate})${gauge}`;
      })
      .join('；');
    const more = assignments.length > 4 ? `；另有 ${assignments.length - 4} 个关系` : '';
    topologyPointTooltip.textContent = `像素 (${Number(point[0]).toFixed(1)}, ${Number(point[1]).toFixed(1)}) · 边界候选点 · ${assignmentText || '暂无 Q(√2) 坐标'}${more} · 下方选择对应取线关系`;
  } else {
    const expression = Array.isArray(candidate.coordinate_expression)
      ? candidate.coordinate_expression.join(', ')
      : '—';
    const sideLength = report?.global_side_length?.expression || '—';
    const selectableNote = candidate.selectable
      ? `预计新增解释 ${Number(candidate.projected_new_crease_count || 0)} 条`
      : '该近似只供查看，当前不能作为精确种子';
    topologyPointTooltip.textContent = `像素 (${Number(point[0]).toFixed(1)}, ${Number(point[1]).toFixed(1)}) · ≈ (${expression}) · L=${sideLength} · 残差 ${Number(candidate.fit_residual_px || 0).toFixed(2)}px · 连接 ${Number(candidate.incident_unresolved_crease_count || 0)} 条未解释折痕 · ${selectableNote}`;
  }
  topologyPointTooltip.style.left = `${left}%`;
  topologyPointTooltip.style.top = `${top}%`;
  topologyPointTooltip.classList.toggle('leftward', left > 62);
  topologyPointTooltip.classList.toggle('below', top < 25);
  topologyPointTooltip.classList.remove('hidden');
}

function hideTopologyPointTooltip() {
  topologyPointTooltip?.classList.add('hidden');
}

function stageTopologyPointConfirmation(candidate, report) {
  if (!candidate?.selectable || !topologyPointConfirmation) return;
  pendingTopologyPointId = String(candidate.id || '');
  topologyPointLayer?.querySelectorAll('.topology-point-marker')
    .forEach(marker => marker.classList.toggle('pending', marker.dataset.pointId === pendingTopologyPointId));
  const nextRound = guidedSelectionSteps(report).length + 1;
  const expression = Array.isArray(candidate.coordinate_expression)
    ? candidate.coordinate_expression.join(', ')
    : '—';
  topologyPointConfirmationTitle.textContent = `确认第 ${nextRound} 步内部点`;
  topologyPointConfirmationCoordinate.textContent = `≈ (${expression}) · L=${report?.global_side_length?.expression || '—'}`;
  topologyPointConfirmationNote.textContent = `这是原图中已经存在、并连接 ${Number(candidate.incident_unresolved_crease_count || 0)} 条未解释折痕的拓扑点。拟合残差 ${Number(candidate.fit_residual_px || 0).toFixed(2)}px；确认后只会沿既有入射折痕继续传播。`;
  topologyPointConfirmation.classList.remove('hidden');
  topologyPointConfirm?.focus();
}

function bindGuidedPointTooltip(marker, candidate, report, root) {
  marker.addEventListener('pointerenter', () => showTopologyPointTooltip(candidate, report, root));
  marker.addEventListener('pointerleave', hideTopologyPointTooltip);
  marker.addEventListener('focus', () => showTopologyPointTooltip(candidate, report, root));
  marker.addEventListener('blur', hideTopologyPointTooltip);
}

function renderTopologyPointOverlay(report, boundaryRelations = [], root = null) {
  if (!topologyPointLayer || !topologyPointTooltip) return;
  topologyPointLayer.replaceChildren();
  hideTopologyPointTooltip();
  clearTopologyPointConfirmation();
  const topologyCandidates = Array.isArray(report?.next_topology_point_candidates)
    ? report.next_topology_point_candidates
    : [];
  const candidates = topologyCandidates.length
    ? topologyCandidates
    : buildBoundaryRelationPointCandidates(boundaryRelations);
  topologyPointLayer.classList.toggle('hidden', candidates.length === 0);
  if (!candidates.length) return;
  const maximum = guidedPointMaximum(report, root);
  for (const candidate of candidates) {
    const point = Array.isArray(candidate.observed_point_px) ? candidate.observed_point_px : null;
    if (!point || maximum <= 0) continue;
    const isBoundaryPoint = candidate.kind === 'boundary_relation_point';
    const marker = document.createElement(isBoundaryPoint ? 'span' : 'button');
    if (!isBoundaryPoint) marker.type = 'button';
    marker.className = `topology-point-marker${isBoundaryPoint ? ' boundary-relation-point' : ''}`;
    marker.dataset.pointId = String(candidate.id || '');
    marker.style.left = `${Math.max(0, Math.min(100, Number(point[0]) / maximum * 100))}%`;
    marker.style.top = `${Math.max(0, Math.min(100, Number(point[1]) / maximum * 100))}%`;
    if (isBoundaryPoint) {
      const firstAssignment = candidate.boundary_assignments?.[0];
      marker.tabIndex = 0;
      marker.setAttribute('role', 'img');
      marker.setAttribute(
        'aria-label',
        `${candidate.label || '边界候选点'}，近似坐标 ${(firstAssignment?.coordinate_expression || []).join(', ') || '未知'}`,
      );
    } else {
      marker.setAttribute('aria-disabled', candidate.selectable ? 'false' : 'true');
      marker.setAttribute('aria-label', `${candidate.label || '内部拓扑点'}，近似坐标 ${(candidate.coordinate_expression || []).join(', ') || '未知'}`);
    }
    bindGuidedPointTooltip(marker, candidate, report, root);
    if (!isBoundaryPoint) {
      marker.addEventListener('click', () => stageTopologyPointConfirmation(candidate, report));
    }
    topologyPointLayer.append(marker);
  }
}

function boundaryRelationMessage(report, root) {
  const rawPrimary = isRawPrimaryResult(root);
  if (!report) {
    if (rawPrimary) {
      return '请先选择一个你认为可能是取线起点的边界关系；系统只沿原图中已有入射的有限折痕传播。';
    }
    return '选择后只会重排已有构造轨迹，不会重新识别整张图，也不会改动严格 .cp。';
  }
  if (!report.enabled) {
    if (report.reason === 'incompatible_relation_coordinate_gauge') {
      return '该关系使用了不同的全局边长，不能并入当前取线链。';
    }
    if (report.reason === 'conflicting_selected_relation_geometry') {
      return '该关系与当前链上的精确点冲突，未采用。';
    }
    return '该关系已经不在当前候选目录中，未把它当成自由种子。请重新选择。';
  }
  const rounds = guidedSelectionSteps(report).length;
  const guided = Number(report.guided_selected_ray_count || 0);
  const unresolved = Number(report.unexplained_observations || 0);
  const nextCount = Number(report.next_relation_candidate_count || 0);
  const nextPointCount = Number(report.selectable_topology_point_candidate_count || 0);
  if (report.phase === 'complete_existing_creases') {
    const segments = guidedMvCandidateSegments(report);
    const segmentCount = Number(
      report.cp_output_contract?.candidate_internal_segment_count || segments.length,
    );
    const automaticCount = segments.filter(segment => guidedMvSegmentIsAutomatic(root, segment)).length;
    const missingCount = segments.filter(
      segment => !guidedMvEffectiveAssignment(root, segment),
    ).length;
    if (report.output_ready) {
      return automaticCount > 0
        ? `已有折痕已经完整解释，${automaticCount} 条有限线段已根据原图红蓝证据自动判定 M/V，可直接导出 .cp。`
        : `已有折痕已经完整解释，${segmentCount} 条有限线段的人工 M/V 确认也已通过导出检查。`;
    }
    if (automaticCount > 0 && missingCount > 0) {
      return `已有折痕已经完整解释，${automaticCount} 条有限线段已根据原图红蓝证据自动判定；还剩 ${missingCount} 条含混线段需要人工确认。`;
    }
    if (missingCount === 0) {
      return `已有折痕已经完整解释，${segmentCount} 条有限线段的 M/V 已确认；但导出检查尚未通过。`;
    }
    return `已有折痕已经完整解释；下方还有 ${missingCount} 条含混线段需要人工确认 M/V，系统不会自动猜测。`;
  }
  if (report.status === 'complete_propagation') {
    if (rawPrimary) {
      return `累计 ${rounds} 步选择后，已在同一张原图拓扑上解释 ${guided} 条折痕；当前仍是取线分析，不会提前导出 .cp。`;
    }
    return `累计 ${rounds} 步选择后，受约束路径覆盖 ${guided} 条取线；严格 .cp 未改。`;
  }
  if (report.status === 'partial_propagation') {
    if (nextCount > 0) {
      return `累计 ${rounds} 步已解释 ${guided} 条原图折痕，仍有 ${unresolved} 条未解释；下方只列出能继续减少未解释折痕的同尺度边界候选。`;
    }
    if (nextPointCount > 0) {
      return `累计 ${rounds} 步已解释 ${guided} 条原图折痕，仍有 ${unresolved} 条未解释；边界续选已经耗尽，请在上方原图悬停查看内部点，点击后再明确确认。`;
    }
    return `累计 ${rounds} 步已解释 ${guided} 条原图折痕，仍有 ${unresolved} 条未解释；现有拓扑中没有通过残差与传播收益检查的内部点。`;
  }
  if (report.status === 'no_matching_trace_rays' || report.status === 'no_matching_observed_creases') {
    return '所选关系没有匹配到有边界接触证据的原图折痕，未生成任何新取线。';
  }
  return rawPrimary
    ? '所选关系已被接受，但当前原图有限拓扑没有实际采用它；没有生成新折痕。'
    : '所选关系已被接受，但当前受约束路径没有实际采用它；未改动严格 .cp。';
}

function renderBoundaryRelations(root) {
  const shadow = root?.shadow_search || {};
  const allCandidates = Array.isArray(shadow.boundary_relation_candidates)
    ? shadow.boundary_relation_candidates
    : [];
  if (!boundaryRelations || !boundaryRelationList || !boundaryRelationStatus) return;
  const guided = shadow.guided_boundary || null;
  const selectedSteps = guidedSelectionSteps(guided);
  const selectedIds = guidedSelectionIds(guided);
  const continuing = Boolean(guided?.enabled && selectedSteps.length);
  const candidates = continuing && Array.isArray(guided.next_relation_candidates)
    ? guided.next_relation_candidates
    : allCandidates;
  const topologyPointCandidates = continuing && Array.isArray(guided.next_topology_point_candidates)
    ? guided.next_topology_point_candidates
    : [];
  const selectableTopologyPointCount = topologyPointCandidates.filter(candidate => candidate.selectable).length;
  const showingTopologyPoints = continuing && candidates.length === 0 && topologyPointCandidates.length > 0;
  boundaryRelations.classList.toggle('hidden', allCandidates.length === 0 && selectedSteps.length === 0);
  renderBoundaryRelationHistory(guided);
  renderTopologyPointOverlay(guided, candidates, root);
  renderGuidedMvEditor(root, guided);
  if (!allCandidates.length && !selectedSteps.length) {
    boundaryRelationList.replaceChildren();
    boundaryRelationStatus.textContent = '';
    return;
  }

  boundaryRelationCount.textContent = showingTopologyPoints
    ? `${selectableTopologyPointCount} 个内部点可确认`
    : continuing
    ? `${candidates.length} 组可继续`
    : `${candidates.length} 组`;
  boundaryRelationStatus.textContent = boundaryRelationMessage(guided, root);
  if (boundaryRelationIntro) {
    if (continuing) {
      const sideLength = guided.global_side_length?.expression || '当前';
      boundaryRelationIntro.textContent = candidates.length
        ? `这些续选候选全部沿用 L=${sideLength}，并按对未解释折痕的预计新增覆盖排序。试算只用于列出，不会自动采用。`
        : showingTopologyPoints
        ? `边界续选已经耗尽。上方原图只标出连接未解释折痕的既有拓扑点；悬停显示约等于的 Q(√2) 坐标与残差，点击后还要确认才会成为第 ${selectedSteps.length + 1} 步。`
        : '当前边界候选已经不能继续减少未解释折痕，既有内部点也没有通过拟合与传播收益检查；保留现有链和未解释证据。';
    } else {
      boundaryRelationIntro.textContent = isRawPrimaryResult(root)
        ? '候选直接来自原图有限折痕到纸边的接触，按等分完整度、拟合残差和 √2 表达式复杂度排序。请由人选择；系统不会自动决定。'
        : '这些是严格结果中已出现的纸边接触关系，按完整度、残差和 √2 表达式复杂度排序。请由人选择；系统不会自动把它当成构造事实。';
    }
  }
  boundaryRelationList.replaceChildren();
  if (!candidates.length) {
    const empty = document.createElement('p');
    empty.className = 'boundary-relation-empty';
    empty.textContent = showingTopologyPoints
      ? `请在上方原图查看 ${topologyPointCandidates.length} 个近似坐标点；其中 ${selectableTopologyPointCount} 个通过试算，可以点击并确认。光标本身不会被拟合成新点。`
      : '没有剩余边界关系或合格内部点能够解释新的折痕。已选链和未解释证据都会保留。';
    boundaryRelationList.append(empty);
    return;
  }

  for (const relation of candidates) {
    const item = document.createElement('article');
    item.className = 'boundary-relation-item';
    const heading = document.createElement('div');
    heading.className = 'boundary-relation-heading';
    const title = document.createElement('strong');
    const priority = continuing ? relation.next_priority : relation.priority;
    title.textContent = `${continuing ? '续选优先' : '优先'} ${priority ?? '—'} · ${relation.label || '边界关系'}`;
    const meta = document.createElement('small');
    const relationResidual = relation.fitted_geometry_max_residual_px
      ?? relation.max_residual_px
      ?? 0;
    const projection = Number(relation.projected_new_crease_count || 0);
    meta.textContent = continuing
      ? `预计新增解释 ${projection} 条 · 选择后预计剩余 ${Number(relation.projected_unexplained_observations || 0)} 条`
      : `${relation.observed_point_count ?? relation.points?.length ?? 0} 个已出现点 · 最大关系残差 ${Number(relationResidual).toFixed(2)}px`;
    heading.append(title, meta);

    const expressions = document.createElement('code');
    const gauge = relation.recommended_coordinate_gauge || null;
    const sideLength = gauge?.side_length?.expression || '';
    expressions.textContent = (relation.points || []).map(point => {
      const projectCoordinate = Array.isArray(point.project_coordinate)
        ? point.project_coordinate.map(value => value?.expression || '—')
        : null;
      const coordinate = projectCoordinate
        ? projectCoordinate.join(', ')
        : (Array.isArray(point.coordinate_expression) ? point.coordinate_expression.join(', ') : '—');
      return `${point.label || point.id}: (${coordinate})`;
    }).join(' · ');
    if (sideLength) expressions.textContent = `L=${sideLength} · ${expressions.textContent}`;

    const note = document.createElement('small');
    note.className = 'boundary-relation-note';
    const rawEvidence = relation.evidence_source === 'raw_image_directional_scan'
      || relation.evidence_source === 'raw_image_finite_topology';
    const coordinateNote = sideLength
      ? '左上角为 (0,0)，全图共用同一个根号二边长 L；坐标不是逐点单独缩放。'
      : '当前没有稳定的全局边长候选，暂时显示中心归一化兼容坐标。';
    note.textContent = continuing
      ? `${coordinateNote} 预计覆盖来自同一张有限拓扑的未解释折痕，仍由人决定是否加入当前链。`
      : rawEvidence
      ? `${coordinateNote} 候选来自原图边界观测，像素位置与精确坐标分开保留。`
      : `${coordinateNote} 这是严格轨迹兼容候选。`;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'boundary-relation-select';
    button.setAttribute('aria-pressed', 'false');
    button.textContent = continuing
      ? `作为第 ${selectedSteps.length + 1} 步边界关系`
      : '以此作为取线起点';
    button.addEventListener('click', () => evaluateBoundaryRelation(relation.id));

    item.append(heading, expressions, note, button);
    boundaryRelationList.append(item);
  }
}

function setBoundaryRelationBusy(busy) {
  if (!boundaryRelationList) return;
  boundaryRelationList.dataset.busy = busy ? 'true' : 'false';
  boundaryRelationList.querySelectorAll('button').forEach(button => { button.disabled = busy; });
  if (boundaryRelationUndo) boundaryRelationUndo.disabled = busy;
  if (topologyPointConfirm) topologyPointConfirm.disabled = busy;
  if (topologyPointCancel) topologyPointCancel.disabled = busy;
  if (topologyPointLayer) topologyPointLayer.style.pointerEvents = busy ? 'none' : '';
  if (mvSegmentLayer) mvSegmentLayer.style.pointerEvents = busy ? 'none' : '';
  updateGuidedMvSummary(currentResult, currentResult?.shadow_search?.guided_boundary);
}

function guidedReportError(report) {
  if (report?.reason === 'incompatible_relation_coordinate_gauge') {
    return '所选关系使用了不同的全局边长';
  }
  if (report?.reason === 'conflicting_selected_relation_geometry') {
    return '所选关系与当前链上的精确点冲突';
  }
  if (report?.reason === 'boundary_relation_has_priority') {
    return '仍有能够减少未解释折痕的边界关系，内部点尚未开放';
  }
  if (report?.reason === 'invalid_guided_topology_point') {
    return '该内部点不是当前未解释拓扑中的可确认候选';
  }
  return '所选关系当前不可用';
}

async function requestGuidedBoundary(selectionSteps, segmentLineTypes = null) {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const root = currentResult;
  const assignments = segmentLineTypes === null
    ? guidedMvResolvedAssignments(root, root.shadow_search?.guided_boundary)
    : normalizeGuidedMvAssignments(segmentLineTypes);
  setBoundaryRelationBusy(true);
  boundaryRelationStatus.textContent = `正在用 ${selectionSteps.length} 步选择重算同一张有限拓扑…`;
  try {
    const report = await callWorker('guided-boundary', {
      result: {
        stats: root.stats || {},
        playback_trace: root.playback_trace || [],
        raw_crease_evidence: root.shadow_search?.raw_crease_evidence || null,
        boundary_relation_candidates: root.shadow_search?.boundary_relation_candidates || [],
      },
      selection: {
        selection_steps: selectionSteps,
        segment_line_types: assignments,
      },
    });
    if (!report?.enabled) throw new Error(guidedReportError(report));
    if (currentResult === root) {
      root.shadow_search = root.shadow_search || {};
      root.shadow_search.guided_boundary = report;
      syncGuidedMvAssignments(root, report);
      guidedMvDirty = false;
      guidedMvUndoStack = [];
      root.phase = report.phase || report.status || root.phase;
      syncGuidedOutputState(root, report);
      renderBoundaryRelations(root);
    }
  } catch (error) {
    if (currentResult === root) {
      boundaryRelationStatus.textContent = `受约束传播失败：${cleanWorkerError(error.message)}`;
    }
  } finally {
    if (currentResult === root) setBoundaryRelationBusy(false);
  }
}

async function evaluateBoundaryRelation(relationId) {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const report = currentResult.shadow_search?.guided_boundary;
  const selectedSteps = guidedSelectionSteps(report);
  const selectedIds = guidedSelectionIds(report);
  const nextId = String(relationId || '');
  if (!nextId || selectedIds.includes(nextId)) return;
  await requestGuidedBoundary([
    ...selectedSteps,
    { kind: 'boundary_relation', id: nextId },
  ]);
}

async function confirmGuidedTopologyPoint() {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const report = currentResult.shadow_search?.guided_boundary;
  const selectedSteps = guidedSelectionSteps(report);
  const pointId = String(pendingTopologyPointId || '');
  const candidate = (report?.next_topology_point_candidates || [])
    .find(item => String(item?.id || '') === pointId && item?.selectable);
  if (!candidate || !pointId) return;
  await requestGuidedBoundary([
    ...selectedSteps,
    { kind: 'topology_point', id: pointId },
  ]);
}

async function undoGuidedBoundary() {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const root = currentResult;
  const selectedSteps = guidedSelectionSteps(root.shadow_search?.guided_boundary);
  if (!selectedSteps.length) return;
  const previousSteps = selectedSteps.slice(0, -1);
  if (!previousSteps.length) {
    delete root.shadow_search.guided_boundary;
    root.phase = 'awaiting_boundary_relation';
    invalidateGuidedOutput(root);
    renderBoundaryRelations(root);
    return;
  }
  await requestGuidedBoundary(previousSteps);
}

function undoGuidedMvEdit() {
  finishGuidedMvStroke();
  const root = currentResult;
  const report = root?.shadow_search?.guided_boundary;
  const stroke = guidedMvUndoStack.pop();
  if (!root || !report || !stroke?.length) return;
  const assignments = guidedMvAssignments(root);
  for (const { segmentId, previous } of stroke) {
    if (previous) assignments[segmentId] = previous;
    else delete assignments[segmentId];
  }
  writeGuidedMvAssignments(root, assignments);
  guidedMvDirty = true;
  invalidateGuidedOutput(root);
  renderGuidedMvEditor(root, report);
  updateGuidedMvSummary(root, report);
}

function clearGuidedMvAssignments() {
  finishGuidedMvStroke();
  const root = currentResult;
  const report = root?.shadow_search?.guided_boundary;
  if (!root || !report) return;
  const assignments = guidedMvAssignments(root);
  const editableIds = new Set(guidedMvEditableSegments(root, report).map(segment => String(segment.id)));
  const stroke = Object.entries(assignments)
    .filter(([segmentId, value]) => (
      editableIds.has(segmentId)
      && value?.source !== GUIDED_MV_AUTOMATIC_SOURCE
    ))
    .map(([segmentId, previous]) => ({ segmentId, previous: { ...previous } }));
  if (!stroke.length) return;
  guidedMvUndoStack.push(stroke);
  for (const { segmentId } of stroke) delete assignments[segmentId];
  writeGuidedMvAssignments(root, assignments);
  guidedMvDirty = true;
  invalidateGuidedOutput(root);
  renderGuidedMvEditor(root, report);
  updateGuidedMvSummary(root, report);
}

async function applyGuidedMvAssignments() {
  finishGuidedMvStroke();
  const root = currentResult;
  const report = root?.shadow_search?.guided_boundary;
  if (!root || !report || boundaryRelationList?.dataset.busy === 'true') return;
  const segments = guidedMvCandidateSegments(report);
  const assignments = guidedMvResolvedAssignments(root, report);
  const missingIds = guidedMvEditableSegments(root, report)
    .filter(segment => !assignments[String(segment.id)])
    .map(segment => String(segment.id));
  if (missingIds.length) {
    if (mvEditorStatus) {
      mvEditorStatus.textContent = `还差 ${missingIds.length} 条未标注；先补完灰色虚线，当前结果不会导出。`;
    }
    const firstMissing = Array.from(mvSegmentLayer?.querySelectorAll('.mv-segment') || [])
      .find(group => group.dataset.segmentId === missingIds[0]);
    firstMissing?.focus();
    return;
  }
  if (mvEditorStatus) {
    mvEditorStatus.textContent = `正在用 ${Object.keys(assignments).length} 条已确认 M/V（含原图红蓝证据）统一重算并执行导出检查……`;
  }
  await requestGuidedBoundary(guidedSelectionSteps(report), assignments);
}

boundaryRelationUndo?.addEventListener('click', () => { void undoGuidedBoundary(); });
topologyPointConfirm?.addEventListener('click', () => { void confirmGuidedTopologyPoint(); });
topologyPointCancel?.addEventListener('click', clearTopologyPointConfirmation);
mvBrushButtons.forEach(button => {
  button.addEventListener('click', () => setGuidedMvBrush(Number(button.dataset.lineType)));
});
mvUndo?.addEventListener('click', undoGuidedMvEdit);
mvClear?.addEventListener('click', clearGuidedMvAssignments);
mvApply?.addEventListener('click', () => { void applyGuidedMvAssignments(); });
mvNextUnassigned?.addEventListener('click', () => {
  focusNextGuidedMvUnassigned(currentResult, currentResult?.shadow_search?.guided_boundary);
});
window.addEventListener('pointermove', paintGuidedMvAtPointer);
window.addEventListener('mousemove', paintGuidedMvAtPointer);
window.addEventListener('pointerup', finishGuidedMvStroke);
window.addEventListener('pointercancel', finishGuidedMvStroke);
document.addEventListener('keydown', event => {
  if (mvEditor?.classList.contains('hidden') || event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (target instanceof Element && target.closest('input, textarea, select, button, [contenteditable="true"], .mv-segment')) return;
  const key = event.key.toLowerCase();
  const lineType = key === 'm' ? 2 : key === 'v' ? 3 : key === 'u' ? 0 : null;
  if (lineType === null) return;
  event.preventDefault();
  setGuidedMvBrush(lineType);
});
setGuidedMvBrush(guidedMvBrush);

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
  if (
    !currentResult
    || !currentVariant
    || typeof currentVariant.cp !== 'string'
    || !currentVariant.cp.length
  ) return;
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
