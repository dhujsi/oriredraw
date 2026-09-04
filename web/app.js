const uploadForm = document.querySelector('#upload-form');
const input = document.querySelector('#image-input');
const fileName = document.querySelector('#file-name');
const dropZone = document.querySelector('#drop-zone');
const emptyState = document.querySelector('#empty-state');
const loading = document.querySelector('#loading');
const resultContent = document.querySelector('#result-content');
const sourcePreview = document.querySelector('#source-image');
const preview = document.querySelector('#preview-image');
const redrawLayerToggle = document.querySelector('#layer-redraw');
const sourceLayerToggle = document.querySelector('#layer-source');
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
const boundaryRelationSummary = document.querySelector('#boundary-relations-summary');
const boundaryRelationCount = document.querySelector('#boundary-relation-count');
const boundaryRelationList = document.querySelector('#boundary-relation-list');
const boundaryRelationStatus = document.querySelector('#boundary-relation-status');
const boundaryRelationIntro = document.querySelector('#boundary-relation-intro');
const boundaryRelationHistory = document.querySelector('#boundary-relation-history');
const boundaryRelationHistoryList = document.querySelector('#boundary-relation-history-list');
const boundaryRelationUndo = document.querySelector('#boundary-relation-undo');
const topologyPointLayer = document.querySelector('#topology-point-layer');
const topologyPointTooltip = document.querySelector('#topology-point-tooltip');
const topologyPointPopover = document.querySelector('#topology-point-popover');
const lineLegend = document.querySelector('#line-legend');
const topologyPointConfirmation = document.querySelector('#topology-point-confirmation');
const topologyPointConfirmationTitle = document.querySelector('#topology-point-confirmation-title');
const topologyPointConfirmationCoordinate = document.querySelector('#topology-point-confirmation-coordinate');
const topologyPointConfirmationNote = document.querySelector('#topology-point-confirmation-note');
const topologyPointConfirm = document.querySelector('#topology-point-confirm');
const topologyPointCancel = document.querySelector('#topology-point-cancel');
const mvSegmentLayer = document.querySelector('#mv-segment-layer');
const loadingStage = document.querySelector('#loading-stage');
const loadingNote = document.querySelector('#loading-note');
const loadingProgress = document.querySelector('#loading-progress');
const loadingProgressValue = document.querySelector('#loading-progress-value');
const guidedProgress = document.querySelector('#guided-progress');
const guidedProgressTrack = document.querySelector('#guided-progress-track');
const guidedProgressStage = document.querySelector('#guided-progress-stage');
const guidedProgressValue = document.querySelector('#guided-progress-value');
const corePointCanvas = document.querySelector('#core-point-canvas');
const corePointCard = document.querySelector('#core-point-card');
const corePointTitle = document.querySelector('#core-point-title');
const corePointCoordinate = document.querySelector('#core-point-coordinate');
const anchorDetails = document.querySelector('#anchor-details');
const resultEyebrow = document.querySelector('#result-eyebrow');
const resultTitle = document.querySelector('#result-title');
const previewFigure = preview.closest('.preview');

const WEB_ENGINE_VERSION = '20260903-topology-anchored-rays-v1';
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
let openBoundaryPointId = '';

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
    if (data.stage === 'analyze-raw') {
      updateProgress(Number(data.percent ?? 0), data.message);
      return;
    }
    if (data.stage === 'guided-boundary') {
      updateGuidedProgress(
        Number(data.percent ?? 0),
        data.message,
        Boolean(data.indeterminate),
      );
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
  loadingStage.textContent = message || '正在处理…';
}

function updateGuidedProgress(percent, message, indeterminate = false) {
  if (!guidedProgress || !guidedProgressTrack) return;
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  guidedProgressTrack.setAttribute('aria-valuenow', String(value));
  guidedProgressTrack.setAttribute(
    'aria-valuetext',
    indeterminate ? '正在计算，剩余时间无法预估' : `${value}%`,
  );
  guidedProgressTrack.querySelector('i').style.width = `${value}%`;
  guidedProgressValue.textContent = indeterminate ? '计算中' : `${value}%`;
  guidedProgressStage.textContent = message || '正在计算折痕…';
  guidedProgress.classList.toggle('indeterminate', indeterminate);
}

function beginGuidedProgress() {
  if (!guidedProgress) return;
  updateGuidedProgress(8, '正在准备本次起点推导…');
  guidedProgress.classList.remove('hidden');
}

function endGuidedProgress() {
  guidedProgress?.classList.add('hidden');
  guidedProgress?.classList.remove('indeterminate');
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

function setResultAvailability(cpAvailable, projectAvailable = Boolean(currentResult?.reconstruction_data_uri)) {
  downloadButton.disabled = !cpAvailable;
  downloadButton.classList.toggle('hidden', !cpAvailable);
  document.dispatchEvent(new CustomEvent('oriredraw:result-state', {
    detail: {
      cpAvailable: Boolean(cpAvailable),
      projectAvailable: Boolean(projectAvailable),
    },
  }));
}

function syncPreviewLayers() {
  if (!sourcePreview || !preview) return;
  const showSource = sourceLayerToggle?.checked !== false;
  const showRedraw = redrawLayerToggle?.checked !== false;
  sourcePreview.classList.toggle('hidden', !showSource);
  preview.classList.toggle('hidden', !showRedraw);
  sourcePreview.setAttribute('aria-hidden', String(!showSource));
  preview.setAttribute('aria-hidden', String(!showRedraw));
}

function setPreviewAssets(sourceUri, redrawUri) {
  if (sourcePreview) sourcePreview.src = sourceUri || '';
  if (preview) preview.src = redrawUri || '';
  syncPreviewLayers();
}

function beginImageFlow() {
  emptyState.classList.add('hidden');
  resultContent.classList.add('hidden');
  loading.classList.remove('hidden');
  updateProgress(0, '正在分析原图中的线…');
  loadingNote.textContent = '先显示原图和绿色点；只需选一次开始方式。';
  warnings.innerHTML = '';
  setResultAvailability(false, false);
  submitButton.disabled = true;
}

function endImageFlow() {
  loading.classList.add('hidden');
  submitButton.disabled = !engineReady;
}

async function runImageFlow() {
  if (!input.files.length || !engineReady) return;
  beginImageFlow();

  try {
    const file = input.files[0];
    const buffer = await file.arrayBuffer();
    const data = await callWorker('analyze-raw', { buffer, settings: readSettings() }, [buffer]);
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
  runImageFlow();
});

function showError(message) {
  resultContent.classList.add('hidden');
  loading.classList.add('hidden');
  emptyState.classList.remove('hidden');
  emptyState.querySelector('p').textContent = message;
  emptyState.querySelector('small').textContent = '请检查图片或调整参数后重试';
  setResultAvailability(false, false);
}

function isRawPrimaryResult(data) {
  return data?.mode === 'guided_raw_primary_v1';
}

function configureResultView({ rawPrimary }) {
  previewFigure?.classList.remove('playback-active');
  lineLegend?.classList.toggle('hidden', !rawPrimary);
}

function renderRawPrimaryStats(data) {
  const raw = data.shadow_search?.raw_crease_evidence || {};
  const topology = data.shadow_search?.raw_crease_topology || {};
  const values = [
    ['分析图尺寸', `${data.stats?.analysis_size_used ?? 0}px`],
    ['识别到的线', data.stats?.raw_crease_count ?? raw.line_count ?? 0],
    ['分成的小段', data.stats?.raw_finite_segment_count ?? raw.finite_segment_count ?? 0],
    ['交点', data.stats?.raw_topology_point_count ?? topology.point_count ?? 0],
    ['接好的线', data.stats?.raw_topology_segment_count ?? topology.segment_count ?? 0],
    ['接触纸边的点', data.stats?.raw_boundary_contact_count ?? topology.boundary_contacts?.length ?? 0],
    ['可用起点方式', data.stats?.boundary_relation_candidate_count ?? 0],
    ['分析耗时', `${(Number(data.stats?.raw_analysis_duration_ms || 0) / 1000).toFixed(2)}s`],
  ];
  stats.innerHTML = values.map(([label, value]) =>
    `<div><strong>${escapeHtml(value ?? 0)}</strong><span>${label}</span></div>`
  ).join('');
}

function rawPrimaryWarnings(data) {
  const warnings = [
    '现在显示的是原图。点一个绿色点，再在点旁边选择开始方式。',
    '选择开始方式后即可下载当前 .cp 草稿；通过全部检查后会标记为已验证。',
  ];
  const candidates = data?.shadow_search?.boundary_relation_candidates;
  if (!Array.isArray(candidates) || candidates.length === 0) {
    warnings.push('没有找到可用的起点。请检查纸张边缘和线条是否完整、清楚。');
  }
  return warnings;
}

function updateRawPrimaryGuidedCopy(root, report) {
  if (!isRawPrimaryResult(root)) return;
  if (!report?.enabled) {
    resultEyebrow.textContent = '原图分析';
    resultTitle.textContent = '请先选一个起点';
    warnings.innerHTML = rawPrimaryWarnings(root)
      .map(message => `<p>${escapeHtml(message)}</p>`)
      .join('');
    return;
  }

  const unresolved = Number(report.unexplained_observations || 0);
  const canonicalAdded = Number(
    report.canonical_ray_application?.accepted_candidate_count || 0,
  );
  const blockerLabels = [...new Set((report.cp_output_contract?.blockers || [])
    .map(item => guidedBlockerLabel(item?.code))
    .filter(Boolean))];
  resultEyebrow.textContent = '重绘';
  resultTitle.textContent = '重绘已结束';
  const messages = [
    `程序已从唯一的起点自动检验合法方向，并加入 ${canonicalAdded} 条有连续原图证据的 22.5° 系折痕。`,
    ...(unresolved > 0 ? [`仍有 ${unresolved} 条观测线无法由当前证明链确定。`] : []),
    ...(report.checks_passed
      ? ['全部检查已通过，可以下载当前 .cp。']
      : [
          '当前结果没有通过检查；仍可下载未验证的 .cp 草稿。',
          ...(blockerLabels.length ? [`需要处理：${blockerLabels.join('；')}。`] : []),
        ]),
  ];
  warnings.innerHTML = messages.map(message => `<p>${escapeHtml(message)}</p>`).join('');
}

function renderRawPrimaryResult(data) {
  currentVariant = null;
  resultContent.classList.add('raw-primary');
  resultEyebrow.textContent = '原图分析';
  resultTitle.textContent = '请先选一个起点';
  configureResultView({ rawPrimary: true });
  setPreviewAssets(
    data.source_data_uri || data.overlay_data_uri,
    data.redraw_data_uri || data.reconstruction_data_uri,
  );
  warnings.innerHTML = rawPrimaryWarnings(data).map(message => `<p>${escapeHtml(message)}</p>`).join('');
  renderRawPrimaryStats(data);
  versionTabs.classList.add('hidden');
  versionTabs.replaceChildren();
  corePointCard?.classList.add('hidden');
  anchorDetails?.classList.add('hidden');
  constructionDetails.classList.add('hidden');
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
  resultTitle.textContent = '重绘结果';
  configureResultView({ rawPrimary: false });
  corePointCard?.classList.remove('hidden');
  anchorDetails?.classList.remove('hidden');
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
  setResultAvailability(typeof data.cp === 'string' && data.cp.length > 0);
  resultContent.classList.remove('hidden');
}

function renderVersion(version, root) {
  setPreviewAssets(
    root.source_data_uri || version.source_data_uri || version.overlay_data_uri,
    version.redraw_data_uri || version.reconstruction_data_uri,
  );

  warnings.innerHTML = (version.warnings || root.warnings || []).map(message => `<p>${escapeHtml(message)}</p>`).join('');
  const data = version.stats ? version : root;
  const values = [
    ['分析尺寸', `${data.stats.analysis_size_used ?? 0}px`],
    ['纸张是否校正', data.stats.aspect_ratio_corrected
      ? `${Number(data.stats.source_paper_aspect_ratio ?? 1).toFixed(3)}× → 1:1`
      : '无需校正'],
    ['小图放大', data.stats.source_upscaled ? `${Number(data.stats.analysis_scale ?? 1).toFixed(2)}×` : '未放大'],
    ['可用直线', data.stats.constructible_rays ?? data.stats.exact_rays],
    ['开始时的线', data.stats.construction_seed_rays ?? 0],
    ['精确定位点', data.stats.algebraic_seed_points ?? 0],
    ['由纸边找到的线', data.stats.boundary_contact_derived_rays ?? 0],
    ['补出的线', data.stats.derived_rays ?? 0],
    ['图内线段', data.stats.internal_segments],
    ['结构检查分', `${Math.round((data.stats.camv_structural_completeness_score ?? 0) * 100)}%`],
    ['结构检查可疑点', data.stats.camv_structure?.violation_vertex_count ?? 0],
    ['结构检查补线', data.stats.camv_path_committed_arms ?? 0],
    ['结构检查次数', data.stats.camv_path_recheck_rounds ?? 0],
    ['山折（红线）', data.stats.mv_red_segments ?? 0],
    ['谷折（蓝线）', data.stats.mv_blue_segments ?? 0],
    ['颜色不确定的线', data.stats.mv_ambiguous_segments ?? 0],
    ['自动改色的线', data.stats.mv_camv_changed_segments ?? 0],
    ['结构异常', data.stats.camv_full?.violation_vertex_count ?? 0],
    ['保留的偏移线', data.stats.observed_proxy_edges_preserved ?? 0],
    ['最大偏移 px', data.stats.observed_proxy_max_shift_px ?? 0],
    ['忽略的非标准线', data.stats.angle_rejected_segments],
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
        && [
          'explicit_segment_assignment',
          'source_image_color_evidence',
          'source_image_default_mountain',
          'camv_maekawa_single_line_solution',
          'user_confirmed',
        ]
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

function guidedMvRawDisplaySegments(report) {
  const topology = report?.raw_topology;
  const rawSegments = Array.isArray(topology?.segments) ? topology.segments : [];
  const rawPoints = new Map(
    (Array.isArray(topology?.points) ? topology.points : [])
      .map(point => [String(point?.id || ''), point?.point])
      .filter(([id, point]) => (
        id
        && Array.isArray(point)
        && point.length >= 2
        && point.slice(0, 2).every(value => Number.isFinite(Number(value)))
      )),
  );
  const maximum = Number(
    topology?.maximum_coordinate_px
    || Number(topology?.analysis_size || 0) - 1,
  );
  if (!rawSegments.length || !Number.isFinite(maximum) || maximum <= 0) return [];
  const toCp = point => point.slice(0, 2).map(value => (
    -200 + (400 * Number(value)) / maximum
  ));
  return rawSegments.map(segment => {
    const start = rawPoints.get(String(segment?.start_point_id || ''));
    const end = rawPoints.get(String(segment?.end_point_id || ''));
    if (!start || !end) return null;
    const evidence = segment?.line_type_evidence;
    const directType = Number(segment?.line_type);
    const evidenceType = Number(evidence?.line_type);
    const lineType = [2, 3].includes(directType)
      ? directType
      : [2, 3].includes(evidenceType)
      ? evidenceType
      : 2;
    const observedSource = lineType === directType
      ? String(segment?.line_type_source || '')
      : String(evidence?.source || '');
    const lineTypeSource = observedSource || (
      lineType === 2 ? 'source_image_default_mountain' : null
    );
    return {
      ...segment,
      line_type: lineType,
      line_type_source: lineTypeSource,
      start_cp: toCp(start),
      end_cp: toCp(end),
      display_source: 'observed_raw_topology',
    };
  }).filter(Boolean);
}

function guidedMvDisplaySegments(report) {
  const candidates = guidedMvCandidateSegments(report);
  if (report?.cp_available || report?.cp_output_contract?.cp_available) {
    return candidates;
  }
  const candidateIds = new Set(candidates.map(segment => String(segment.id)));
  const rawSegments = guidedMvRawDisplaySegments(report)
    .filter(segment => !candidateIds.has(String(segment.id)));
  return [...candidates, ...rawSegments];
}

const GUIDED_MV_TRUSTED_SOURCES = new Set([
  'explicit_segment_assignment',
  'source_image_color_evidence',
  'source_image_default_mountain',
  'camv_maekawa_single_line_solution',
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

function syncGuidedOutputState(root, report) {
  if (!root) return;
  if (report) {
    const checksPassed = report.output_ready === true && report.checks_passed === true;
    const cpAvailable = report.cp_available === true
      && typeof report.cp === 'string'
      && report.cp.length > 0;
    root.cp = cpAvailable
      ? report.cp
      : null;
    root.output_ready = checksPassed;
    root.checks_passed = checksPassed;
  } else {
    root.cp = null;
    root.output_ready = false;
    root.checks_passed = false;
  }
  const cpAvailable = Boolean(typeof root.cp === 'string' && root.cp.length);
  root.cp_available = cpAvailable;
  root.output_unchanged = !cpAvailable;
  currentVariant = cpAvailable ? root : null;
  setResultAvailability(cpAvailable);
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
  setResultAvailability(false);
}

function guidedMvTypeLabel(lineType) {
  if (Number(lineType) === 2) return '山折（红线）';
  return '谷折（蓝线）';
}

function guidedBlockerLabel(code) {
  const labels = {
    unresolved_existing_creases: '原图还有线没有接上',
    missing_exact_creases: '原图里还有线没有生成',
    unresolved_finite_segment_endpoints: '还有线段两端没有确定',
    unresolved_boundary_contacts: '还有纸边连接没有确定',
    finite_segment_direction_mismatch: '有些线的方向和原图对不上',
    endpoint_residual_exceeds_tolerance: '有个线头的位置和原图偏差过大',
    internal_dangling_segment_endpoints: '有线在图内突然断开',
    camv_foldability_violations: '局部平折检查没有通过',
  };
  return labels[code] || '还有一项检查没有通过';
}

function renderGuidedMvOverlay(root, report) {
  if (!mvSegmentLayer) return;
  mvSegmentLayer.replaceChildren();
  const segments = report?.enabled
    ? guidedMvDisplaySegments(report)
    : [];
  mvSegmentLayer.classList.toggle('hidden', segments.length === 0);
  if (!segments.length) return;

  for (const segment of segments) {
    const [x1, y1] = segment.start_cp.map(Number);
    const [x2, y2] = segment.end_cp.map(Number);
    const segmentId = String(segment.id);
    const assignment = guidedMvEffectiveAssignment(root, segment);
    const candidateType = Number(assignment?.line_type || segment.line_type || 2);
    const lineType = [2, 3].includes(candidateType) ? candidateType : 2;
    const source = String(
      assignment?.source || segment.line_type_source || 'source_image_default_mountain',
    );
    const group = document.createElementNS(SVG_NAMESPACE, 'g');
    group.classList.add('mv-segment');
    group.classList.add('automatic');
    group.dataset.segmentId = segmentId;
    group.dataset.lineType = String(lineType);
    group.dataset.lineSource = source;
    group.setAttribute('tabindex', '-1');
    group.setAttribute('role', 'img');
    group.setAttribute('aria-label', `这条线：${guidedMvTypeLabel(lineType)}`);

    const title = document.createElementNS(SVG_NAMESPACE, 'title');
    title.textContent = guidedMvTypeLabel(lineType);
    const visible = document.createElementNS(SVG_NAMESPACE, 'line');
    visible.classList.add('mv-segment-visible');
    visible.setAttribute('x1', String(x1));
    visible.setAttribute('y1', String(y1));
    visible.setAttribute('x2', String(x2));
    visible.setAttribute('y2', String(y2));
    group.append(title, visible);
    mvSegmentLayer.append(group);
  }
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
    if (expression) chip.title = `根号二坐标：${expression.trim().replace(/^≈\s*/, '')}`;
    const label = isPoint
      ? (entry.label || '图上的黄色点')
      : (entry.label || '起点方式');
    chip.textContent = isPoint ? `补充点：${label}` : `起点方式：${label}`;
    boundaryRelationHistoryList.append(chip);
  }
  const heading = boundaryRelationHistory.querySelector('strong');
  if (heading) heading.textContent = '已经选好的起点';
  boundaryRelationUndo.textContent = selectedSteps.length > 1
    ? '撤销最后一次'
    : '撤销起点';
}

function clearTopologyPointConfirmation() {
  pendingTopologyPointId = '';
  topologyPointConfirmation?.classList.add('hidden');
  topologyPointConfirmation?.classList.remove('leftward', 'below');
  resetTopologyPointPopupPosition(topologyPointConfirmation);
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

const CROSS_SIDE_LABELS = { left: '左', right: '右', top: '上', bottom: '下' };

function crossSegmentSummary(point) {
  const cross = point?.cross_segment_lengths;
  const visible = Array.isArray(cross?.visible_sides) ? cross.visible_sides : [];
  const distances = cross?.distances && typeof cross.distances === 'object'
    ? cross.distances
    : {};
  return visible
    .map(side => {
      const expression = distances[side]?.expression;
      return expression ? `${CROSS_SIDE_LABELS[side] || side}：${expression}` : '';
    })
    .filter(Boolean)
    .join(' · ');
}

function pointGeometrySummary(point) {
  const summary = crossSegmentSummary(point);
  return summary ? `到纸边：${summary}` : '';
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
      const boundaryRangePx = Array.isArray(relation?.observed_endpoint_coordinates)
        ? relation.observed_endpoint_coordinates
          .map(value => Number(value))
          .filter(value => Number.isFinite(value))
        : [];
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
        boundary_range_px: boundaryRangePx,
        cross_segment_lengths: point?.cross_segment_lengths || null,
        imageResidualPx: Number(point?.image_residual_px || 0),
      });
    }
  }
  return [...points.values()];
}

function fitTopologyPointPopup(popup) {
  if (!popup || popup.classList.contains('hidden')) return;
  const padding = 8;
  let rect = popup.getBoundingClientRect();
  if (rect.top < padding && !popup.classList.contains('below')) {
    popup.classList.add('below');
  } else if (rect.bottom > window.innerHeight - padding && popup.classList.contains('below')) {
    popup.classList.remove('below');
  }
  rect = popup.getBoundingClientRect();
  if (rect.right > window.innerWidth - padding && !popup.classList.contains('leftward')) {
    popup.classList.add('leftward');
  } else if (rect.left < padding && popup.classList.contains('leftward')) {
    popup.classList.remove('leftward');
  }
}

function resetTopologyPointPopupPosition(popup) {
  popup?.classList.remove('viewport-positioned');
  popup?.style.removeProperty('left');
  popup?.style.removeProperty('top');
}

function anchorTopologyPointPopup(popup, candidate) {
  if (!popup || !candidate || !topologyPointLayer) return;
  const pointId = String(candidate.id || '');
  const marker = Array.from(topologyPointLayer.querySelectorAll('.topology-point-marker'))
    .find(item => item.dataset.pointId === pointId);
  const rect = marker?.getBoundingClientRect();
  if (!rect || rect.width <= 0 || rect.height <= 0) return;
  popup.classList.add('viewport-positioned');
  popup.style.left = `${rect.left + rect.width / 2}px`;
  popup.style.top = `${rect.top + rect.height / 2}px`;
}

function showTopologyPointTooltip(candidate, report, root = null) {
  if (!topologyPointTooltip) return;
  resetTopologyPointPopupPosition(topologyPointTooltip);
  const point = Array.isArray(candidate.observed_point_px) ? candidate.observed_point_px : [0, 0];
  const maximum = guidedPointMaximum(report, root);
  const left = Math.max(2, Math.min(98, Number(point[0]) / maximum * 100));
  const top = Math.max(2, Math.min(98, Number(point[1]) / maximum * 100));
  if (candidate.kind === 'boundary_relation_point') {
    const assignments = Array.isArray(candidate.boundary_assignments)
      ? candidate.boundary_assignments
      : [];
    const more = assignments.length > 4 ? `，另有 ${assignments.length - 4} 种方案` : '';
    const geometry = pointGeometrySummary(assignments[0]);
    topologyPointTooltip.textContent = `绿色起点，再选一种开始方式${more}${geometry ? `；${geometry}` : ''}`;
  } else {
    const expression = Array.isArray(candidate.coordinate_expression)
      ? candidate.coordinate_expression.join(', ')
      : '—';
    const selectableNote = candidate.selectable
      ? `黄色补充点：可能补上 ${Number(candidate.projected_new_crease_count || 0)} 条线；点一下查看，确认后才会使用`
      : '这个黄色点暂时不能使用';
    const geometry = pointGeometrySummary(candidate);
    const coordinateNote = geometry
      ? `；${geometry}`
      : expression !== '—' ? `；根号二坐标 (${expression})` : '';
    topologyPointTooltip.textContent = `${selectableNote}${coordinateNote}`;
  }
  topologyPointTooltip.style.left = `${left}%`;
  topologyPointTooltip.style.top = `${top}%`;
  anchorTopologyPointPopup(topologyPointTooltip, candidate);
  topologyPointTooltip.classList.toggle('leftward', left > 62);
  topologyPointTooltip.classList.toggle('below', top < 25);
  topologyPointTooltip.classList.remove('hidden');
  fitTopologyPointPopup(topologyPointTooltip);
}

function hideTopologyPointTooltip() {
  topologyPointTooltip?.classList.add('hidden');
  resetTopologyPointPopupPosition(topologyPointTooltip);
}

function clearBoundaryPointPopover() {
  openBoundaryPointId = '';
  if (!topologyPointPopover) return;
  topologyPointPopover.replaceChildren();
  topologyPointPopover.classList.add('hidden');
  resetTopologyPointPopupPosition(topologyPointPopover);
}

function boundaryAssignmentDetail(assignment) {
  const summary = pointGeometrySummary(assignment);
  return summary ? `（${summary}）` : '';
}

function showBoundaryPointPopover(candidate, report, root) {
  if (!topologyPointPopover) return;
  resetTopologyPointPopupPosition(topologyPointPopover);
  const point = Array.isArray(candidate?.observed_point_px) ? candidate.observed_point_px : [0, 0];
  const maximum = guidedPointMaximum(report, root);
  if (!Number.isFinite(Number(point[0])) || !Number.isFinite(Number(point[1])) || maximum <= 0) return;
  const assignments = Array.isArray(candidate.boundary_assignments)
    ? candidate.boundary_assignments
    : [];
  openBoundaryPointId = String(candidate.id || '');
  topologyPointPopover.replaceChildren();

  const title = document.createElement('strong');
  title.textContent = '这个点怎么开始？';
  const note = document.createElement('small');
  note.textContent = assignments.length > 1
    ? '下面每个按钮是一种开始方式，选一个就行。'
    : '点下面的按钮就开始，也可以暂时不选。';
  const choices = document.createElement('div');
  choices.className = 'topology-point-popover-choices';
  const relationLabelCounts = new Map();
  for (const assignment of assignments) {
    const label = String(assignment?.relationLabel || '');
    relationLabelCounts.set(label, (relationLabelCounts.get(label) || 0) + 1);
  }
  for (const assignment of assignments) {
    const choice = document.createElement('button');
    choice.type = 'button';
    const relationLabel = assignment.relationLabel
      ? `按“${assignment.relationLabel}”开始`
      : '用这个方式开始';
    const detail = relationLabelCounts.get(String(assignment?.relationLabel || '')) > 1
      ? boundaryAssignmentDetail(assignment)
      : '';
    choice.textContent = `${relationLabel}${detail}`;
    choice.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      clearBoundaryPointPopover();
      evaluateBoundaryRelation(assignment.relationId);
    });
    choices.append(choice);
  }
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'topology-point-popover-close';
  close.textContent = '暂时不选';
  close.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    clearBoundaryPointPopover();
  });
  topologyPointPopover.append(title, note, choices, close);

  const left = Math.max(2, Math.min(98, Number(point[0]) / maximum * 100));
  const top = Math.max(2, Math.min(98, Number(point[1]) / maximum * 100));
  topologyPointPopover.style.left = `${left}%`;
  topologyPointPopover.style.top = `${top}%`;
  anchorTopologyPointPopup(topologyPointPopover, candidate);
  topologyPointPopover.classList.toggle('leftward', left > 62);
  topologyPointPopover.classList.toggle('below', top < 25);
  topologyPointPopover.classList.remove('hidden');
  fitTopologyPointPopup(topologyPointPopover);
}

function stageTopologyPointConfirmation(candidate, report, root) {
  if (!candidate?.selectable || !topologyPointConfirmation) return;
  pendingTopologyPointId = String(candidate.id || '');
  topologyPointLayer?.querySelectorAll('.topology-point-marker')
    .forEach(marker => marker.classList.toggle('pending', marker.dataset.pointId === pendingTopologyPointId));
  const unresolvedCount = Number(candidate.incident_unresolved_crease_count || 0);
  const isStartPoint = candidate.kind === 'topology_point_start';
  topologyPointConfirmationTitle.textContent = isStartPoint
    ? '要用这个点作为起点吗？'
    : '要不要用这个黄色点补线？';
  topologyPointConfirmationCoordinate.textContent = pointGeometrySummary(candidate)
    || '这是图上的点，不需要输入坐标。';
  topologyPointConfirmationNote.textContent = isStartPoint
    ? `这是原图中连接斜线的交点；试算可继续解释 ${Number(candidate.projected_new_crease_count || 0)} 条已有线。`
    : unresolvedCount > 0
    ? `程序估计它可以补上 ${unresolvedCount} 条还没接上的线。确定它是关键点时再继续；不确定就先跳过。`
    : '程序还不确定它能补哪条线。不确定就先跳过。';
  const point = Array.isArray(candidate.observed_point_px) ? candidate.observed_point_px : [0, 0];
  const maximum = guidedPointMaximum(report, root);
  const left = Math.max(2, Math.min(98, Number(point[0]) / maximum * 100));
  const top = Math.max(2, Math.min(98, Number(point[1]) / maximum * 100));
  topologyPointConfirmation.style.left = `${left}%`;
  topologyPointConfirmation.style.top = `${top}%`;
  anchorTopologyPointPopup(topologyPointConfirmation, candidate);
  topologyPointConfirmation.classList.toggle('leftward', left > 62);
  topologyPointConfirmation.classList.toggle('below', top < 25);
  topologyPointConfirmation.classList.remove('hidden');
  fitTopologyPointPopup(topologyPointConfirmation);
  topologyPointConfirm?.focus({ preventScroll: true });
}

function bindGuidedPointTooltip(marker, candidate, report, root) {
  marker.addEventListener('pointerenter', () => showTopologyPointTooltip(candidate, report, root));
  marker.addEventListener('pointerleave', hideTopologyPointTooltip);
  marker.addEventListener('focus', () => showTopologyPointTooltip(candidate, report, root));
  marker.addEventListener('blur', hideTopologyPointTooltip);
}

function renderTopologyPointOverlay(
  report,
  boundaryRelations = [],
  root = null,
  initialTopologyPoints = [],
) {
  if (!topologyPointLayer || !topologyPointTooltip) return;
  topologyPointLayer.replaceChildren();
  hideTopologyPointTooltip();
  clearBoundaryPointPopover();
  clearTopologyPointConfirmation();
  const topologyCandidates = Array.isArray(report?.next_topology_point_candidates)
    ? report.next_topology_point_candidates
    : [];
  const candidates = topologyCandidates.length
    ? topologyCandidates
    : [
      ...buildBoundaryRelationPointCandidates(boundaryRelations),
      ...(Array.isArray(initialTopologyPoints) ? initialTopologyPoints : []),
    ];
  topologyPointLayer.classList.toggle('hidden', candidates.length === 0);
  if (!candidates.length) return;
  const maximum = guidedPointMaximum(report, root);
  for (const candidate of candidates) {
    const point = Array.isArray(candidate.observed_point_px) ? candidate.observed_point_px : null;
    if (!point || maximum <= 0) continue;
    const isBoundaryPoint = candidate.kind === 'boundary_relation_point';
    const isStartPoint = candidate.kind === 'topology_point_start';
    const marker = document.createElement('button');
    marker.type = 'button';
    marker.className = `topology-point-marker${isBoundaryPoint ? ' boundary-relation-point' : ''}${isStartPoint ? ' topology-point-start' : ''}`;
    marker.dataset.pointId = String(candidate.id || '');
    marker.style.left = `${Math.max(0, Math.min(100, Number(point[0]) / maximum * 100))}%`;
    marker.style.top = `${Math.max(0, Math.min(100, Number(point[1]) / maximum * 100))}%`;
    if (isBoundaryPoint) {
      marker.setAttribute('aria-haspopup', 'dialog');
      marker.setAttribute(
        'aria-label',
        '绿色起点，点击选择开始方式',
      );
    } else {
      marker.setAttribute('aria-disabled', candidate.selectable ? 'false' : 'true');
      marker.setAttribute('aria-label', `${candidate.label || '黄色补充点'}，点击查看是否要继续`);
    }
    if (isBoundaryPoint) {
      marker.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        showBoundaryPointPopover(candidate, report, root);
      });
    } else {
      bindGuidedPointTooltip(marker, candidate, report, root);
      marker.addEventListener('click', () => stageTopologyPointConfirmation(candidate, report, root));
    }
    topologyPointLayer.append(marker);
  }
}

function boundaryRelationMessage(report, root) {
  const rawPrimary = isRawPrimaryResult(root);
  if (!report) {
    if (rawPrimary) {
      return '第一步：点一个绿色点。点旁边会出现开始方式，选一个就行。';
    }
    return '可以先选一个绿色点；后面的补充都不是必须。';
  }
  if (!report.enabled) {
    if (report.reason === 'incompatible_relation_coordinate_gauge') {
      return '这个方式不能和之前的选择一起用，之前的选择已经保留。';
    }
    if (report.reason === 'conflicting_selected_relation_geometry') {
      return '这个方式和之前的选择冲突，没有采用。';
    }
    return '这个方式已经失效，请重新点一个绿色点。';
  }
  const guided = Number(report.guided_selected_ray_count || 0);
  const unresolved = Number(report.unexplained_observations || 0);
  const canonicalAdded = Number(report.canonical_ray_application?.accepted_candidate_count || 0);
  if (report.phase === 'complete_existing_creases') {
    const segments = guidedMvDisplaySegments(report);
    const segmentCount = Number(
      report.cp_output_contract?.draft_internal_segment_count || segments.length,
    );
    const blockerLabels = [...new Set((report.cp_output_contract?.blockers || [])
      .map(item => guidedBlockerLabel(item?.code))
      .filter(Boolean))];
    if (report.checks_passed) {
      return `${segmentCount} 条折痕已通过全部检查，可以下载当前 .cp。`;
    }
    return blockerLabels.length
      ? `可下载未验证的 .cp 草稿；仍需处理：${blockerLabels.join('；')}。`
      : '当前结果没有通过全部检查；可下载未验证的 .cp 草稿。';
  }
  if (report.status === 'complete_propagation') {
    if (rawPrimary) {
      return `已经补上 ${guided} 条线；可下载未验证的 .cp 草稿。`;
    }
    return `已经补上 ${guided} 条线。原来的 .cp 没有改动。`;
  }
  if (report.status === 'partial_propagation') {
    return `自动推导已结束：加入 ${canonicalAdded} 条有证据的合法方向折痕；还有 ${unresolved} 条没确定，不再要求选择第二个起点。`;
  }
  if (report.status === 'no_matching_trace_rays' || report.status === 'no_matching_observed_creases') {
    return '这个方式没有补上原图里的线。请换一个点或方式。';
  }
  return rawPrimary
    ? '这个方式已接受，但没有补上新线。'
    : '这个方式已接受，但没有补上新线；原来的 .cp 没有改动。';
}

function renderBoundaryRelations(root) {
  const shadow = root?.shadow_search || {};
  const allCandidates = Array.isArray(shadow.boundary_relation_candidates)
    ? shadow.boundary_relation_candidates
    : [];
  const initialTopologyPoints = Array.isArray(shadow.topology_point_start_candidates)
    ? shadow.topology_point_start_candidates
    : [];
  if (!boundaryRelations || !boundaryRelationList || !boundaryRelationStatus) return;
  const guided = shadow.guided_boundary || null;
  updateRawPrimaryGuidedCopy(root, guided);
  const selectedSteps = guidedSelectionSteps(guided);
  const continuing = Boolean(guided?.enabled && selectedSteps.length);
  const candidates = continuing ? [] : allCandidates;
  const topologyPointCandidates = continuing && Array.isArray(guided.next_topology_point_candidates)
    ? guided.next_topology_point_candidates
    : [];
  const selectableTopologyPointCount = topologyPointCandidates.filter(candidate => candidate.selectable).length;
  const showingTopologyPoints = continuing && candidates.length === 0 && topologyPointCandidates.length > 0;
  const completed = continuing;
  const rawPrimary = isRawPrimaryResult(root);
  const initialRawSelection = rawPrimary && selectedSteps.length === 0;
  boundaryRelations.classList.toggle(
    'hidden',
    initialRawSelection || (allCandidates.length === 0 && selectedSteps.length === 0),
  );
  renderBoundaryRelationHistory(guided);
  renderTopologyPointOverlay(
    guided,
    candidates,
    root,
    selectedSteps.length ? [] : initialTopologyPoints,
  );
  renderGuidedMvOverlay(root, guided);
  if (!allCandidates.length && !selectedSteps.length) {
    boundaryRelationList.replaceChildren();
    boundaryRelationStatus.textContent = '';
    return;
  }

  if (boundaryRelationSummary) {
    boundaryRelationSummary.textContent = completed
      ? '已选起点'
      : selectedSteps.length
      ? '还可以继续（可选）'
      : '先选一个起点';
  }
  boundaryRelationCount.textContent = completed
    ? ''
    : showingTopologyPoints
    ? `${selectableTopologyPointCount} 个可选点`
    : continuing
    ? `${candidates.length} 个可选方式`
    : `${candidates.length} 个起点方式`;
  boundaryRelationStatus.textContent = boundaryRelationMessage(guided, root);
  if (boundaryRelationIntro) {
    boundaryRelationIntro.classList.toggle('hidden', completed);
    if (completed) {
      boundaryRelationIntro.textContent = '';
    } else {
      boundaryRelationIntro.textContent = isRawPrimaryResult(root)
        ? '点一个绿色点，在点旁边选择开始方式。'
        : '下面是可能的开始方式。优先级只是建议，也可以直接点图上的绿色点。';
    }
  }
  boundaryRelationList.replaceChildren();
  if (isRawPrimaryResult(root) && !continuing) {
    return;
  }
  if (!candidates.length) {
    if (completed) return;
    const empty = document.createElement('p');
    empty.className = 'boundary-relation-empty';
    empty.textContent = showingTopologyPoints
      ? '不确定时可以停在这里；已经选好的内容会保留。'
      : '没有找到可靠的补充方式。当前结果会保留。';
    boundaryRelationList.append(empty);
    return;
  }

  for (const relation of candidates) {
    const item = document.createElement('article');
    item.className = 'boundary-relation-item';
    const heading = document.createElement('div');
    heading.className = 'boundary-relation-heading';
    const title = document.createElement('strong');
    title.textContent = `${continuing ? '可选补充方式' : '可能的开始方式'}：${relation.label || '开始方式'}`;
    const meta = document.createElement('small');
    const projection = Number(relation.projected_new_crease_count || 0);
    meta.textContent = continuing
      ? `大约还能补上 ${projection} 条线`
      : `这个方式用到 ${relation.observed_point_count ?? relation.points?.length ?? 0} 个点`;
    heading.append(title, meta);

    const expressions = document.createElement('code');
    expressions.textContent = (relation.points || []).map(point => {
      return `${point.label || point.id}: ${pointGeometrySummary(point) || '精确长度关系已记录'}`;
    }).join(' · ');
    const coordinateDetails = document.createElement('details');
    coordinateDetails.className = 'boundary-relation-coordinates';
    const coordinateSummary = document.createElement('summary');
    coordinateSummary.textContent = '查看精确长度关系';
    coordinateDetails.append(coordinateSummary, expressions);

    const note = document.createElement('small');
    note.className = 'boundary-relation-note';
    const rawEvidence = relation.evidence_source === 'raw_image_directional_scan'
      || relation.evidence_source === 'raw_image_finite_topology';
    note.textContent = continuing
      ? '这是可选的补充方式；不确定就不要选。'
      : rawEvidence
      ? '这是程序从原图中找到的一个可能起点。'
      : '这是已有结果中的一个可能起点。';

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'boundary-relation-select';
    button.setAttribute('aria-pressed', 'false');
    button.textContent = continuing ? '试这个方式' : '从这里开始';
    button.addEventListener('click', () => evaluateBoundaryRelation(relation.id));

    item.append(heading, coordinateDetails, note, button);
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
}

function guidedReportError(report) {
  if (report?.reason === 'incompatible_relation_coordinate_gauge') {
    return '这个方式不能和之前的选择一起用。';
  }
  if (report?.reason === 'conflicting_selected_relation_geometry') {
    return '这个方式和之前的选择冲突。';
  }
  if (report?.reason === 'boundary_relation_has_priority') {
    return '请先点图上的绿色点；黄色点要等程序提示后才能选。';
  }
  if (report?.reason === 'invalid_guided_topology_point') {
    return '这个黄色点暂时不能使用，请换另一个点。';
  }
  return '这个方式目前不能使用，请换一个点或方式。';
}

async function requestGuidedBoundary(selectionSteps, segmentLineTypes = null) {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const root = currentResult;
  const previousScrollY = window.scrollY;
  const assignments = segmentLineTypes === null
    ? guidedMvAssignments(root)
    : normalizeGuidedMvAssignments(segmentLineTypes);
  setBoundaryRelationBusy(true);
  beginGuidedProgress();
  boundaryRelationStatus.textContent = '正在根据你的选择更新结果，请稍候…';
  try {
    const report = await callWorker('guided-boundary', {
      result: {
        stats: root.stats || {},
        playback_trace: root.playback_trace || [],
        raw_crease_evidence: root.shadow_search?.raw_crease_evidence || null,
        boundary_relation_candidates: root.shadow_search?.boundary_relation_candidates || [],
        topology_point_start_candidates: root.shadow_search?.topology_point_start_candidates || [],
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
      root.phase = report.phase || report.status || root.phase;
      syncGuidedOutputState(root, report);
      renderBoundaryRelations(root);
    }
  } catch (error) {
    if (currentResult === root) {
      boundaryRelationStatus.textContent = `更新失败：${cleanWorkerError(error.message)}`;
    }
  } finally {
    endGuidedProgress();
    if (currentResult === root) {
      setBoundaryRelationBusy(false);
      window.scrollTo(0, previousScrollY);
    }
  }
}

async function evaluateBoundaryRelation(relationId) {
  if (!currentResult || boundaryRelationList.dataset.busy === 'true') return;
  const report = currentResult.shadow_search?.guided_boundary;
  const selectedSteps = guidedSelectionSteps(report);
  if (selectedSteps.length) return;
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
  const initialCandidates = selectedSteps.length === 0
    ? (currentResult.shadow_search?.topology_point_start_candidates || [])
    : [];
  const candidate = [...initialCandidates, ...(report?.next_topology_point_candidates || [])]
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

boundaryRelationUndo?.addEventListener('click', () => { void undoGuidedBoundary(); });
topologyPointConfirm?.addEventListener('click', () => { void confirmGuidedTopologyPoint(); });
topologyPointCancel?.addEventListener('click', clearTopologyPointConfirmation);

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

redrawLayerToggle?.addEventListener('change', syncPreviewLayers);
sourceLayerToggle?.addEventListener('change', syncPreviewLayers);

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
  const verificationSuffix = currentVariant.checks_passed === true ? '' : '-unverified';
  link.download = `${sourceName}${suffix}${verificationSuffix}.cp`;
  link.click();
  URL.revokeObjectURL(url);
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}
