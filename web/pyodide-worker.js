const PYODIDE_BASE = 'https://cdn.jsdelivr.net/pyodide/v314.0.5/full/';
const SOURCE_FILES = [
  'foldability.py',
  'reconstructor.py',
  'web_bridge.py',
  'construction_search.py',
  'boundary_relations.py',
  'exact_qsqrt2.py',
  'qsqrt2_coordinates.py',
  'exact_graph_propagation.py',
  'construction_proof_topology.py',
  'finite_endpoint_closure.py',
  'guided_cp_output.py',
  'constrained_angle_candidates.py',
  'transactional_angle_repair.py',
  'proof_ray_candidates.py',
  'guided_construction.py',
  'raw_boundary_evidence.py',
  'raw_crease_evidence.py',
  'raw_crease_topology.py',
  'raw_primary_bridge.py',
  'shadow_search.py',
  'shadow_evidence.py',
  'shadow_geometry.py',
  'shadow_geometry_v2.py',
  'shadow_variant.py',
  'provenance_v3.py',
  'provenance_v4.py',
  'provenance_v5.py',
  'provenance_v6.py',
  'quality_v5.py',
  'selected_geometry_v4.py',
  'shadow_variant_v3.py',
  'isolated_ratio.py',
  'shadow_variant_v4.py',
  'shadow_variant_v5.py',
  'shadow_variant_v6.py',
  'shadow_bridge.py',
];
const WEB_ENGINE_VERSION = '20260903-topology-anchored-rays-v1';

let pyodide;
let readyPromise;

function announce(stage, message, percent = null, id = null, indeterminate = false) {
  self.postMessage({ type: 'status', stage, message, percent, id, indeterminate });
}

async function initialize() {
  announce('runtime', '正在加载 Python 运行环境…');
  const { loadPyodide } = await import(`${PYODIDE_BASE}pyodide.mjs`);
  pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });

  announce('packages', '正在加载 NumPy 与 OpenCV…');
  await pyodide.loadPackage(['numpy', 'opencv-python']);

  announce('sources', '正在装入 Oriredraw 重建算法…');
  for (const fileName of SOURCE_FILES) {
    const response = await fetch(`./python/${fileName}?v=${WEB_ENGINE_VERSION}`, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`无法加载 ${fileName}（HTTP ${response.status}）`);
    }
    pyodide.FS.writeFile(fileName, await response.text(), { encoding: 'utf8' });
  }
  pyodide.runPython('from shadow_bridge import reconstruct_for_web_shadow_json, rectify_for_web_json; from guided_construction import build_guided_boundary_report_json; from raw_primary_bridge import analyze_raw_primary_json');
  announce('ready', '浏览器识别引擎已就绪');
}

async function ensureReady() {
  if (!readyPromise) readyPromise = initialize();
  return readyPromise;
}

async function reconstructInBrowser(buffer, settings, id) {
  await ensureReady();
  const inputPath = '/tmp/oriredraw-input';
  pyodide.FS.writeFile(inputPath, new Uint8Array(buffer));
  pyodide.globals.set('_oriredraw_settings_json', JSON.stringify(settings));
  pyodide.globals.set('_oriredraw_progress', (percent, message) => {
    announce('reconstruct', String(message), Number(percent), id);
  });
  try {
    return pyodide.runPython(`
from pathlib import Path
reconstruct_for_web_shadow_json(Path("${inputPath}").read_bytes(), _oriredraw_settings_json, _oriredraw_progress)
    `);
  } finally {
    pyodide.globals.delete('_oriredraw_settings_json');
    pyodide.globals.delete('_oriredraw_progress');
    try { pyodide.FS.unlink(inputPath); } catch (_) { /* best-effort cleanup */ }
  }
}

async function rectifyInBrowser(buffer, corners) {
  await ensureReady();
  const inputPath = '/tmp/oriredraw-rectify-input';
  pyodide.FS.writeFile(inputPath, new Uint8Array(buffer));
  pyodide.globals.set('_oriredraw_corners_json', JSON.stringify(corners));
  try {
    return pyodide.runPython(`
from pathlib import Path
rectify_for_web_json(Path("${inputPath}").read_bytes(), _oriredraw_corners_json)
    `);
  } finally {
    pyodide.globals.delete('_oriredraw_corners_json');
    try { pyodide.FS.unlink(inputPath); } catch (_) { /* best-effort cleanup */ }
  }
}

async function analyzeRawInBrowser(buffer, settings, id) {
  await ensureReady();
  const inputPath = '/tmp/oriredraw-raw-primary-input';
  pyodide.FS.writeFile(inputPath, new Uint8Array(buffer));
  pyodide.globals.set('_oriredraw_raw_settings_json', JSON.stringify(settings));
  pyodide.globals.set('_oriredraw_raw_progress', (percent, message) => {
    announce('analyze-raw', String(message), Number(percent), id);
  });
  try {
    return pyodide.runPython(`
from pathlib import Path
analyze_raw_primary_json(Path("${inputPath}").read_bytes(), _oriredraw_raw_settings_json, _oriredraw_raw_progress)
    `);
  } finally {
    pyodide.globals.delete('_oriredraw_raw_settings_json');
    pyodide.globals.delete('_oriredraw_raw_progress');
    try { pyodide.FS.unlink(inputPath); } catch (_) { /* best-effort cleanup */ }
  }
}

async function guidedBoundaryInBrowser(result, selection, id) {
  await ensureReady();
  announce('guided-boundary', '正在准备本次起点推导…', 8, id);
  pyodide.globals.set('_oriredraw_guided_result_json', JSON.stringify(result));
  pyodide.globals.set('_oriredraw_guided_selection_json', JSON.stringify(selection));
  announce('guided-boundary', '正在根据起点计算折痕，剩余时间无法预估…', 35, id, true);
  try {
    const json = pyodide.runPython(`
build_guided_boundary_report_json(
    _oriredraw_guided_result_json,
    _oriredraw_guided_selection_json,
)
    `);
    announce('guided-boundary', '正在整理推导结果…', 96, id);
    return json;
  } finally {
    pyodide.globals.delete('_oriredraw_guided_result_json');
    pyodide.globals.delete('_oriredraw_guided_selection_json');
  }
}

self.onmessage = async event => {
  const { type, id } = event.data;
  try {
    if (type === 'init') {
      await ensureReady();
      self.postMessage({ type: 'ready', id });
      return;
    }
    if (type === 'reconstruct') {
      const json = await reconstructInBrowser(event.data.buffer, event.data.settings, id);
      self.postMessage({ type: 'result', id, payload: JSON.parse(json) });
      return;
    }
    if (type === 'analyze-raw') {
      const json = await analyzeRawInBrowser(event.data.buffer, event.data.settings, id);
      self.postMessage({ type: 'result', id, payload: JSON.parse(json) });
      return;
    }
    if (type === 'rectify') {
      const json = await rectifyInBrowser(event.data.buffer, event.data.corners, id);
      self.postMessage({ type: 'result', id, payload: JSON.parse(json) });
      return;
    }
    if (type === 'guided-boundary') {
      const json = await guidedBoundaryInBrowser(event.data.result, event.data.selection, id);
      self.postMessage({ type: 'result', id, payload: JSON.parse(json) });
      return;
    }
  } catch (error) {
    self.postMessage({
      type: 'error',
      id,
      message: error?.message || String(error),
    });
  }
};
