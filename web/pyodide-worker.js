const PYODIDE_BASE = 'https://cdn.jsdelivr.net/pyodide/v314.0.5/full/';
const SOURCE_FILES = [
  'foldability.py',
  'reconstructor.py',
  'web_bridge.py',
  'construction_search.py',
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
const WEB_ENGINE_VERSION = '20260819-core-free-dev10';

let pyodide;
let readyPromise;

function announce(stage, message, percent = null, id = null) {
  self.postMessage({ type: 'status', stage, message, percent, id });
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
  pyodide.runPython('from shadow_bridge import reconstruct_for_web_shadow_json, rectify_for_web_json');
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
    if (type === 'rectify') {
      const json = await rectifyInBrowser(event.data.buffer, event.data.corners, id);
      self.postMessage({ type: 'result', id, payload: JSON.parse(json) });
    }
  } catch (error) {
    self.postMessage({
      type: 'error',
      id,
      message: error?.message || String(error),
    });
  }
};