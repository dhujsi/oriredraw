const uploadForm = document.querySelector('#upload-form');
const input = document.querySelector('#image-input');
const form = document.querySelector('#upload-form');
const fileName = document.querySelector('#file-name');
const emptyState = document.querySelector('#empty-state');
const loading = document.querySelector('#loading');
const resultContent = document.querySelector('#result-content');
const preview = document.querySelector('#preview-image');
const warnings = document.querySelector('#warnings');
const stats = document.querySelector('#stats');
const anchorTable = document.querySelector('#anchor-table');
const anchorCount = document.querySelector('#anchor-count');
const download = document.querySelector('#download');

let currentResult = null;

function bindRange(id, outputId, format) {
  const element = document.querySelector(id);
  const output = document.querySelector(outputId);
  const update = () => { output.textContent = format(Number(element.value)); };
  element.addEventListener('input', update);
  update();
}

bindRange('#angle', '#angle-value', value => `${value.toFixed(1)}°`);
bindRange('#support', '#support-value', value => `${Math.round(value * 100)}%`);
bindRange('#algebraic', '#algebraic-value', value => `${value.toFixed(1)}px`);

input.addEventListener('change', () => {
  fileName.textContent = input.files[0]?.name || '尚未选择文件';
});

uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!input.files.length) return;

  emptyState.classList.add('hidden');
  resultContent.classList.add('hidden');
  loading.classList.remove('hidden');
  warnings.innerHTML = '';

  try {
    const response = await fetch('/api/reconstruct', { method: 'POST', body: new FormData(uploadForm) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '识别失败');
    currentResult = data;
    renderResult(data);
  } catch (error) {
    emptyState.classList.remove('hidden');
    emptyState.querySelector('p').textContent = error.message;
    emptyState.querySelector('small').textContent = '请检查图片或调整后重试';
  } finally {
    loading.classList.add('hidden');
  }
});

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
    ['峰线 / 红', data.stats.mv_red_segments ?? 0],
    ['谷线 / 蓝', data.stats.mv_blue_segments ?? 0],
    ['红蓝模糊线', data.stats.mv_ambiguous_segments ?? 0],
    ['cAMV 改色线', data.stats.mv_camv_changed_segments ?? 0],
    ['完整 cAMV 异常', data.stats.camv_full?.violation_vertex_count ?? 0],
    ['忽略自由角度证据', data.stats.angle_rejected_segments],
  ];
  stats.innerHTML = values.map(([label, value]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join('');

  anchorCount.textContent = `${data.anchors.length} 条`;
  anchorTable.innerHTML = data.anchors.slice(0, 120).map(anchor => `
    <div>
      <span>${escapeHtml(anchor.source || anchor.side)}</span>
      <code>${escapeHtml(anchor.expression)}</code>
      <b>${anchor.angle.toFixed(1)}°</b>
      <small>误差 ${anchor.snap_error_px.toFixed(2)}px</small>
    </div>
  `).join('');
  resultContent.classList.remove('hidden');
}

document.querySelectorAll('.view-tabs button').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.view-tabs button').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    preview.src = button.dataset.view === 'overlay' ? preview.dataset.overlay : preview.dataset.clean;
  });
});

download.addEventListener('click', () => {
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
