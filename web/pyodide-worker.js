const PYODIDE_BASE = 'https://cdn.jsdelivr.net/pyodide/v314.0.5/full/';
const SOURCE_FILES = ['foldability.py', 'reconstructor.py', 'web_bridge.py'];
const WEB_ENGINE_VERSION = '20260818-line-evidence-v3';

let pyodide;
let readyPromise;

function announce(stage, message) {
  self.postMessage({ type: 'status', stage, message });
}

async function initialize() {
  announce('runtime', '正在加载 Python 运行环境…');
  const { loadPyodide } = await import(`${PYODIDE_BASE}pyodide.mjs`);
  pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });

  announce('packages', '正在加载 NumPy 与 OpenCV…');
  await pyodide.loadPackage(['numpy', 'opencv-python']);

  announce('sources', '正在装入 Oriedraw 重建算法…');
  for (const fileName of SOURCE_FILES) {
    const response = await fetch(`./python/${fileName}?v=${WEB_ENGINE_VERSION}`, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`无法加载 ${fileName}（HTTP ${response.status}）`);
    }
    pyodide.FS.writeFile(fileName, await response.text(), { encoding: 'utf8' });
  }
  pyodide.runPython('from web_bridge import reconstruct_for_web_json');
  announce('ready', '浏览器识别引擎已就绪');
}

async function ensureReady() {
  if (!readyPromise) readyPromise = initialize();
  return readyPromise;
}

async function reconstructInBrowser(buffer, settings) {
  await ensureReady();
  const inputPath = '/tmp/oriedraw-input';
  pyodide.FS.writeFile(inputPath, new Uint8Array(buffer));
  pyodide.globals.set('_oriedraw_settings_json', JSON.stringify(settings));
  try {
    return pyodide.runPython(`
from pathlib import Path
reconstruct_for_web_json(Path("${inputPath}").read_bytes(), _oriedraw_settings_json)
    `);
  } finally {
    pyodide.globals.delete('_oriedraw_settings_json');
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
      const json = await reconstructInBrowser(event.data.buffer, event.data.settings);
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
