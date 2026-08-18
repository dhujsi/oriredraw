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
const cornerToggle = document.querySelector('#corner-toggle');
const cornerEditor = document.querySelector('#corner-editor');
const cornerCanvas = document.querySelector('#corner-canvas');
const cornerReset = document.querySelector('#corner-reset');
const cornerDone = document.querySelector('#corner-done');
const cornerDisable = document.querySelector('#corner-disable');
const angleMode = document.querySelector('#angle-mode');
const angleInput = document.querySelector('#angle');
const versionTabs = document.querySelector('#version-tabs');
const constructionDetails = document.querySelector('#construction-details');
const constructionCount = document.querySelector('#construction-count');
const constructionList = document.querySelector('#construction-list');

const worker = new Worker('./pyodide-worker.js', { type: 'module' });
const pending = new Map();
let requestId = 0;
let currentResult = null;
let engineReady = false;
let perspectiveEnabled = false;
let sourceBitmap = null;
let cornerPoints = [];
let draggedCorner = -1;
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

function cleanWorkerError(message) {
  const lines = String(message || '').split('\n').filter(Boolean);
  const last = lines.at(-1) || '识别失败';
  return last.replace(/^\w+(Error|Exception):\s*/, '');
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
  drawCornerEditor();
}

function drawCornerEditor() {
  if (!sourceBitmap) return;
  const context = cornerCanvas.getContext('2d');
  context.clearRect(0, 0, cornerCanvas.width, cornerCanvas.height);
  context.drawImage(sourceBitmap, 0, 0, cornerCanvas.width, cornerCanvas.height);
  const points = cornerPoints.map(([x, y]) => [x * cornerCanvas.width, y * cornerCanvas.height]);
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
}

async function prepareCornerEditor(file) {
  sourceBitmap?.close?.();
  sourceBitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(sourceBitmap.width, sourceBitmap.height));
  cornerCanvas.width = Math.max(1, Math.round(sourceBitmap.width * scale));
  cornerCanvas.height = Math.max(1, Math.round(sourceBitmap.height * scale));
  resetCornerPoints();
  paperTool.classList.remove('hidden');
}

function pointerPosition(event) {
  const box = cornerCanvas.getBoundingClientRect();
  return [
    (event.clientX - box.left) / box.width,
    (event.clientY - box.top) / box.height,
  ];
}

cornerCanvas.addEventListener('pointerdown', event => {
  const [x, y] = pointerPosition(event);
  const box = cornerCanvas.getBoundingClientRect();
  const nearest = cornerPoints.reduce((best, point, index) => {
    const distance = Math.hypot((point[0] - x) * box.width, (point[1] - y) * box.height);
    return distance < best.distance ? { index, distance } : best;
  }, { index: -1, distance: Infinity });
  if (nearest.distance > 30) return;
  draggedCorner = nearest.index;
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
  perspectiveEnabled = true;
  cornerEditor.classList.remove('hidden');
  document.body.classList.add('corner-editor-open');
  cornerToggle.classList.toggle('active', perspectiveEnabled);
  cornerToggle.textContent = '重新调整四角';
  paperModeLabel.textContent = '按四个准星做透视还原';
  drawCornerEditor();
});
cornerReset.addEventListener('click', resetCornerPoints);
cornerDone.addEventListener('click', () => {
  closeCornerEditor();
  cornerToggle.focus();
});
cornerDisable.addEventListener('click', () => {
  perspectiveEnabled = false;
  closeCornerEditor();
  cornerToggle.classList.remove('active');
  cornerToggle.textContent = '拍照图：调整四角';
  paperModeLabel.textContent = '自动寻找并裁切';
  cornerToggle.focus();
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || cornerEditor.classList.contains('hidden')) return;
  cornerDone.click();
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
  prepareSelectedImage(file);
}

input.addEventListener('change', () => {
  const file = input.files[0];
  fileName.textContent = file?.name || '尚未选择文件';
  if (file) prepareSelectedImage(file);
});

function prepareSelectedImage(file) {
  perspectiveEnabled = false;
  cornerEditor.classList.add('hidden');
  document.body.classList.remove('corner-editor-open');
  cornerToggle.classList.remove('active');
  cornerToggle.textContent = '拍照图：调整四角';
  paperModeLabel.textContent = '自动寻找并裁切';
  prepareCornerEditor(file).catch(() => showError('无法读取图片预览。'));
}

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
  selectFile(pastedFile);
});

uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!input.files.length || !engineReady) return;

  emptyState.classList.add('hidden');
  resultContent.classList.add('hidden');
  loading.classList.remove('hidden');
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
      paper_corners: perspectiveEnabled ? cornerPoints : null,
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
