(() => {
  'use strict';

  const FORMAT = 'oriredraw-project';
  const FORMAT_VERSION = 1;
  const DB_NAME = 'oriredraw-projects';
  const DB_VERSION = 1;
  const STORE_NAME = 'projects';

  const bridge = window.oriredrawProjectBridge;
  if (!bridge) return;

  let activeSavedId = null;
  let activeCreatedAt = null;

  const style = document.createElement('style');
  style.textContent = `
    .oriredraw-project-open { width: 100%; margin: 0 0 14px; min-height: 38px; border: 1px solid var(--ink, #171714); background: #fff; color: var(--ink, #171714); cursor: pointer; font-size: 11px; font-weight: 700; }
    .oriredraw-project-open:hover { background: #f5f5ef; }
    .oriredraw-project-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; flex-wrap: wrap; }
    .oriredraw-project-actions button { min-height: 42px; padding: 0 12px; border: 1px solid var(--ink, #171714); background: #fff; color: var(--ink, #171714); cursor: pointer; font-size: 11px; font-weight: 700; }
    .oriredraw-project-actions button:hover { background: #f5f5ef; }
    .oriredraw-project-actions #download { background: var(--acid, #c7ff2f); padding-inline: 18px; }
    .oriredraw-project-actions #download:hover { background: #aff400; }
    .oriredraw-project-dialog { width: min(620px, calc(100% - 28px)); max-height: min(78vh, 720px); padding: 0; border: 1px solid var(--ink, #171714); background: var(--panel, #fbfaf6); color: var(--ink, #171714); box-shadow: 8px 8px 0 rgba(23, 23, 20, .22); }
    .oriredraw-project-dialog::backdrop { background: rgba(23, 23, 20, .38); backdrop-filter: blur(2px); }
    .oriredraw-project-dialog-inner { padding: 20px; }
    .oriredraw-project-dialog-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    .oriredraw-project-dialog h2 { margin: 0; font-size: 24px; letter-spacing: -.035em; }
    .oriredraw-project-dialog-close { border: 0; background: none; color: var(--muted, #75766f); cursor: pointer; font-size: 20px; line-height: 1; padding: 4px; }
    .oriredraw-project-import { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; padding: 12px; border: 1px solid var(--line, #d7d5cc); background: #fff; }
    .oriredraw-project-import span { color: var(--muted, #75766f); font-size: 11px; line-height: 1.5; }
    .oriredraw-project-import button { flex: 0 0 auto; border: 1px solid var(--ink, #171714); background: var(--acid, #c7ff2f); padding: 8px 10px; cursor: pointer; font-size: 10px; font-weight: 700; }
    .oriredraw-project-list { display: grid; gap: 7px; margin-top: 14px; max-height: 420px; overflow: auto; }
    .oriredraw-project-empty { padding: 22px 10px; border: 1px dashed var(--line, #d7d5cc); color: var(--muted, #75766f); text-align: center; font-size: 11px; }
    .oriredraw-project-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 11px 12px; border: 1px solid var(--line, #d7d5cc); background: #fff; }
    .oriredraw-project-row strong, .oriredraw-project-row small { display: block; }
    .oriredraw-project-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
    .oriredraw-project-row small { margin-top: 4px; color: var(--muted, #75766f); font: 700 9px/1.35 ui-monospace, Consolas, monospace; }
    .oriredraw-project-row-actions { display: flex; gap: 5px; }
    .oriredraw-project-row-actions button { border: 1px solid var(--ink, #171714); background: #fff; padding: 7px 9px; cursor: pointer; font-size: 10px; font-weight: 700; }
    .oriredraw-project-row-actions button:first-child { background: var(--acid, #c7ff2f); }
    .oriredraw-project-toast { position: fixed; left: 50%; bottom: 24px; z-index: 300; transform: translateX(-50%) translateY(12px); padding: 9px 12px; border: 1px solid var(--ink, #171714); background: var(--panel, #fbfaf6); color: var(--ink, #171714); box-shadow: 4px 4px 0 rgba(23, 23, 20, .18); font-size: 11px; font-weight: 700; opacity: 0; pointer-events: none; transition: opacity .16s ease, transform .16s ease; }
    .oriredraw-project-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    @media (max-width: 620px) {
      .result-head { align-items: flex-start; gap: 12px; }
      .oriredraw-project-actions { max-width: 250px; }
      .oriredraw-project-actions button { min-height: 36px; padding-inline: 9px; }
      .oriredraw-project-row { grid-template-columns: 1fr; }
      .oriredraw-project-row-actions { justify-content: flex-end; }
      .oriredraw-project-import { align-items: flex-start; flex-direction: column; }
    }
  `;
  document.head.append(style);

  const form = document.querySelector('#upload-form');
  const submitButton = document.querySelector('#submit-button');
  const input = document.querySelector('#image-input');
  const fileName = document.querySelector('#file-name');
  const resultHead = document.querySelector('.result-head');
  const downloadButton = document.querySelector('#download');
  if (!form || !submitButton || !input || !resultHead || !downloadButton) return;

  const openButton = document.createElement('button');
  openButton.type = 'button';
  openButton.className = 'oriredraw-project-open';
  submitButton.insertAdjacentElement('afterend', openButton);

  const actions = document.createElement('div');
  actions.className = 'oriredraw-project-actions';
  const saveButton = document.createElement('button');
  saveButton.type = 'button';
  const exportButton = document.createElement('button');
  exportButton.type = 'button';
  actions.append(saveButton, exportButton, downloadButton);
  resultHead.append(actions);

  const dialog = document.createElement('dialog');
  dialog.className = 'oriredraw-project-dialog';
  dialog.innerHTML = `
    <div class="oriredraw-project-dialog-inner">
      <div class="oriredraw-project-dialog-head">
        <h2></h2>
        <button type="button" class="oriredraw-project-dialog-close" aria-label="Close">×</button>
      </div>
      <div class="oriredraw-project-import">
        <span></span>
        <button type="button"></button>
        <input type="file" accept=".oriredraw,application/json" hidden>
      </div>
      <div class="oriredraw-project-list"></div>
    </div>
  `;
  document.body.append(dialog);

  const dialogTitle = dialog.querySelector('h2');
  const dialogClose = dialog.querySelector('.oriredraw-project-dialog-close');
  const importCopy = dialog.querySelector('.oriredraw-project-import span');
  const importButton = dialog.querySelector('.oriredraw-project-import button');
  const importInput = dialog.querySelector('.oriredraw-project-import input');
  const projectList = dialog.querySelector('.oriredraw-project-list');

  const toast = document.createElement('div');
  toast.className = 'oriredraw-project-toast';
  document.body.append(toast);
  let toastTimer = null;

  function isEnglish() {
    return document.documentElement.lang.toLowerCase().startsWith('en');
  }

  function copy() {
    return isEnglish()
      ? {
          open: 'Open project',
          save: 'Save project',
          export: 'Export project',
          dialog: 'Open project',
          importCopy: 'Open an exported .oriredraw project file without rerunning reconstruction.',
          importButton: 'Open project file',
          empty: 'No projects saved in this browser yet.',
          openSaved: 'Open',
          deleteSaved: 'Delete',
          saved: 'Project saved in this browser.',
          exported: 'Project file exported.',
          loaded: 'Project opened without reconstruction.',
          deleted: 'Saved project deleted.',
          noResult: 'Run a reconstruction or open a project first.',
          invalid: 'This is not a valid Oriredraw project file.',
          newer: 'This project was created by a newer Oriredraw project format.',
          storageError: 'Could not save in browser storage. Export the project file instead.',
          readError: 'Could not open this project file.',
        }
      : {
          open: '打开项目',
          save: '保存项目',
          export: '导出项目',
          dialog: '打开项目',
          importCopy: '打开导出的 .oriredraw 项目文件，直接恢复结果，不重新运行重建。',
          importButton: '从项目文件打开',
          empty: '这个浏览器里还没有保存的项目。',
          openSaved: '打开',
          deleteSaved: '删除',
          saved: '项目已保存到这个浏览器。',
          exported: '项目文件已导出。',
          loaded: '项目已直接恢复，没有重新重建。',
          deleted: '已删除本地保存项目。',
          noResult: '请先完成一次重建或打开一个项目。',
          invalid: '这不是有效的 Oriredraw 项目文件。',
          newer: '这个项目由更新版本的 Oriredraw 项目格式生成，当前版本无法打开。',
          storageError: '浏览器本地保存失败；可以改用“导出项目”保存为文件。',
          readError: '项目文件读取失败。',
        };
  }

  function applyCopy() {
    const text = copy();
    openButton.textContent = text.open;
    saveButton.textContent = text.save;
    exportButton.textContent = text.export;
    dialogTitle.textContent = text.dialog;
    importCopy.textContent = text.importCopy;
    importButton.textContent = text.importButton;
    if (dialog.open) renderSavedProjects();
  }

  new MutationObserver(applyCopy).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['lang'],
  });
  applyCopy();

  function notify(message) {
    toast.textContent = message;
    toast.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2200);
  }

  function baseName(value) {
    return String(value || 'oriredraw-project')
      .replace(/\.[^.]+$/, '')
      .replace(/[\\/:*?"<>|]+/g, '-')
      .trim() || 'oriredraw-project';
  }

  function activeVariantId(root) {
    const active = document.querySelector('#version-tabs button.active[data-version]');
    const index = Number(active?.dataset.version || 0);
    const versions = [root, ...(root?.variants || [])];
    return versions[index]?.id || root?.id || 'strict';
  }

  function fileToDataUri(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(reader.error || new Error('FileReader failed'));
      reader.readAsDataURL(file);
    });
  }

  function dataUriToFile(source, fallbackName) {
    if (!source?.data_uri || typeof source.data_uri !== 'string') {
      return new File([new Uint8Array([0])], `${baseName(fallbackName)}.png`, { type: 'image/png' });
    }
    const match = source.data_uri.match(/^data:([^;,]+)?(;base64)?,(.*)$/s);
    if (!match) throw new Error('Invalid source data URI');
    const mime = source.type || match[1] || 'application/octet-stream';
    const bytes = match[2]
      ? Uint8Array.from(atob(match[3]), char => char.charCodeAt(0))
      : new TextEncoder().encode(decodeURIComponent(match[3]));
    return new File(
      [bytes],
      source.name || `${baseName(fallbackName)}.png`,
      { type: mime, lastModified: Number(source.last_modified || Date.now()) },
    );
  }

  async function buildProject() {
    const root = bridge.result;
    if (!root?.cp || typeof root.reconstruction_data_uri !== 'string') {
      throw new Error(copy().noResult);
    }
    const sourceFile = input.files?.[0] || null;
    const source = sourceFile
      ? {
          name: sourceFile.name,
          type: sourceFile.type,
          last_modified: sourceFile.lastModified,
          data_uri: await fileToDataUri(sourceFile),
        }
      : null;
    const now = new Date().toISOString();
    return {
      format: FORMAT,
      format_version: FORMAT_VERSION,
      id: activeSavedId || crypto.randomUUID(),
      name: baseName(sourceFile?.name || root.label || 'oriredraw-project'),
      created_at: activeCreatedAt || now,
      saved_at: now,
      source,
      settings: root.stats?.settings || null,
      active_variant_id: activeVariantId(root),
      result: root,
    };
  }

  function validateProject(project) {
    if (
      !project
      || project.format !== FORMAT
      || !Number.isInteger(Number(project.format_version))
      || !project.result
      || typeof project.result.cp !== 'string'
      || typeof project.result.reconstruction_data_uri !== 'string'
    ) {
      throw new Error(copy().invalid);
    }
    if (Number(project.format_version) > FORMAT_VERSION) {
      throw new Error(copy().newer);
    }
    return project;
  }

  function openDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(STORE_NAME)) {
          database.createObjectStore(STORE_NAME, { keyPath: 'id' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function withStore(mode, callback) {
    const database = await openDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE_NAME, mode);
        const store = transaction.objectStore(STORE_NAME);
        let request;
        try {
          request = callback(store);
        } catch (error) {
          reject(error);
          return;
        }
        if (request) {
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        } else {
          transaction.oncomplete = () => resolve(undefined);
          transaction.onerror = () => reject(transaction.error);
        }
      });
    } finally {
      database.close();
    }
  }

  async function saveLocal() {
    try {
      const project = await buildProject();
      await withStore('readwrite', store => store.put(project));
      activeSavedId = project.id;
      activeCreatedAt = project.created_at;
      notify(copy().saved);
      if (dialog.open) renderSavedProjects();
    } catch (error) {
      notify(error?.message === copy().noResult ? error.message : copy().storageError);
    }
  }

  async function exportProject() {
    try {
      const project = await buildProject();
      const blob = new Blob([JSON.stringify(project)], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${baseName(project.name)}.oriredraw`;
      link.click();
      URL.revokeObjectURL(url);
      notify(copy().exported);
    } catch (error) {
      notify(error?.message || copy().noResult);
    }
  }

  function applySettings(settings) {
    if (!settings || typeof settings !== 'object') return;
    const values = {
      '#angle-mode': settings.angle_tolerance_mode,
      '#angle': settings.angle_tolerance_deg,
      '#support': settings.output_support,
      '#algebraic': settings.algebraic_snap_px,
      '#construction-offset': settings.construction_offset_tolerance_px,
      '#mv-mode': settings.mv_mode,
    };
    for (const [selector, value] of Object.entries(values)) {
      if (value === undefined || value === null) continue;
      const element = document.querySelector(selector);
      if (!element) continue;
      element.value = String(value);
      element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    }
    const variants = document.querySelector('#construction-variants');
    if (variants && settings.construction_variants !== undefined) {
      variants.checked = Boolean(settings.construction_variants);
      variants.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function putSourceFile(file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (fileName) fileName.textContent = file.name;
  }

  function chooseVariant(project) {
    const versions = [project.result, ...(project.result.variants || [])];
    const index = Math.max(0, versions.findIndex(version => version.id === project.active_variant_id));
    const button = document.querySelector(`#version-tabs button[data-version="${index}"]`);
    if (button) button.click();
  }

  async function restoreProject(rawProject, { saved = false } = {}) {
    const project = validateProject(rawProject);
    const sourceFile = dataUriToFile(project.source, project.name);
    putSourceFile(sourceFile);
    applySettings(project.settings);
    activeSavedId = saved ? project.id : null;
    activeCreatedAt = saved ? project.created_at : null;

    bridge.prepareRestore(project.result);
    form.requestSubmit();
    setTimeout(() => chooseVariant(project), 0);
    dialog.close();
    notify(copy().loaded);
  }

  async function renderSavedProjects() {
    const text = copy();
    try {
      const projects = await withStore('readonly', store => store.getAll());
      const ordered = [...(projects || [])].sort((a, b) => String(b.saved_at || '').localeCompare(String(a.saved_at || '')));
      if (!ordered.length) {
        projectList.innerHTML = `<div class="oriredraw-project-empty">${text.empty}</div>`;
        return;
      }
      projectList.innerHTML = '';
      for (const project of ordered) {
        const row = document.createElement('div');
        row.className = 'oriredraw-project-row';
        const info = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = project.name || 'Oriredraw project';
        const meta = document.createElement('small');
        const date = project.saved_at ? new Date(project.saved_at) : null;
        meta.textContent = date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : '';
        info.append(title, meta);
        const rowActions = document.createElement('div');
        rowActions.className = 'oriredraw-project-row-actions';
        const openSaved = document.createElement('button');
        openSaved.type = 'button';
        openSaved.textContent = text.openSaved;
        openSaved.addEventListener('click', () => restoreProject(project, { saved: true }).catch(error => notify(error?.message || text.readError)));
        const removeSaved = document.createElement('button');
        removeSaved.type = 'button';
        removeSaved.textContent = text.deleteSaved;
        removeSaved.addEventListener('click', async () => {
          try {
            await withStore('readwrite', store => store.delete(project.id));
            if (activeSavedId === project.id) {
              activeSavedId = null;
              activeCreatedAt = null;
            }
            notify(text.deleted);
            renderSavedProjects();
          } catch (_) {
            notify(text.storageError);
          }
        });
        rowActions.append(openSaved, removeSaved);
        row.append(info, rowActions);
        projectList.append(row);
      }
    } catch (_) {
      projectList.innerHTML = `<div class="oriredraw-project-empty">${text.storageError}</div>`;
    }
  }

  openButton.addEventListener('click', () => {
    renderSavedProjects();
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  });
  dialogClose.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });
  saveButton.addEventListener('click', saveLocal);
  exportButton.addEventListener('click', exportProject);
  importButton.addEventListener('click', () => importInput.click());
  importInput.addEventListener('change', async () => {
    const file = importInput.files?.[0];
    importInput.value = '';
    if (!file) return;
    try {
      const project = validateProject(JSON.parse(await file.text()));
      await restoreProject(project, { saved: false });
    } catch (error) {
      notify(error?.message || copy().readError);
    }
  });

  input.addEventListener('change', () => {
    if (!bridge.restoring) {
      activeSavedId = null;
      activeCreatedAt = null;
    }
  });
})();