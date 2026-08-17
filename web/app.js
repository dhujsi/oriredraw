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

const worker = new Worker('./pyodide-worker.js', { type: 'module' });
const pending = new Map();
let requestId = 0;
let currentResult = null;
let engineReady = false;

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

bindRange('#angle', '#angle-value', value => `${value.toFixed(1)}°`);
bindRange('#support', '#support-value', value => `${Math.round(value * 100)}%`);
bindRange('#algebraic', '#algebraic-value', value => `${value.toFixed(1)}px`);

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
  fileName.textContent = input.files[0]?.name || '尚未选择文件';
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
      angle_tolerance_deg: Number(document.querySelector('#angle').value),
      output_support: Number(document.querySelector('#support').value),
      algebraic_snap_px: Number(document.querySelector('#algebraic').value),
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
  preview.src = data.overlay_data_uri;
  preview.dataset.overlay = data.overlay_data_uri;
  preview.dataset.clean = data.reconstruction_data_uri;

  warnings.innerHTML = data.warnings.map(message => `<p>${escapeHtml(message)}</p>`).join('');
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

  anchorCount.textContent = `${data.anchors.length} 条`;
  anchorTable.innerHTML = data.anchors.slice(0, 120).map(anchor => `
    <div>
      <span>${escapeHtml(anchor.source || anchor.side)}</span>
      <code>${escapeHtml(anchor.expression)}</code>
      <b>${Number(anchor.angle).toFixed(1)}°</b>
      <small>误差 ${Number(anchor.snap_error_px).toFixed(2)}px</small>
    </div>
  `).join('');
  resultContent.classList.remove('hidden');
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
  if (!currentResult) return;
  const sourceName = input.files[0]?.name?.replace(/\.[^.]+$/, '') || 'reconstructed';
  const blob = new Blob([currentResult.cp], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${sourceName}.cp`;
  link.click();
  URL.revokeObjectURL(url);
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}
