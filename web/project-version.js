(() => {
  'use strict';

  const APP_VERSION = '0.2.0-dev.11';
  window.oriredrawAppVersion = APP_VERSION;

  const nativeStringify = JSON.stringify.bind(JSON);
  JSON.stringify = function stringifyWithOriredrawVersion(value, replacer, space) {
    if (
      value
      && typeof value === 'object'
      && value.format === 'oriredraw-project'
      && !value.app_version
    ) {
      value = { ...value, app_version: APP_VERSION };
    }
    return nativeStringify(value, replacer, space);
  };

  function pad(value, width = 2) {
    return String(value).padStart(width, '0');
  }

  function safeBaseName(value, fallback = 'oriredraw') {
    const cleaned = String(value || fallback)
      .replace(/\.[^.]+$/, '')
      .replace(/[\\/:*?"<>|]+/g, '-')
      .replace(/\s+/g, ' ')
      .trim();
    return cleaned || fallback;
  }

  function safePart(value, fallback) {
    const cleaned = String(value || fallback)
      .replace(/[\\/:*?"<>|.\s]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .toLowerCase();
    return cleaned || fallback;
  }

  function timestamp(date = new Date()) {
    return [
      date.getFullYear(),
      pad(date.getMonth() + 1),
      pad(date.getDate()),
    ].join('') + '-' + [
      pad(date.getHours()),
      pad(date.getMinutes()),
      pad(date.getSeconds()),
    ].join('');
  }

  function formattedDownloadFilename(
    sourceName,
    kind = 'strict',
    extension = 'cp',
    date = new Date(),
  ) {
    const source = safeBaseName(sourceName, 'reconstructed');
    const resultKind = safePart(kind, 'strict');
    const ext = safePart(extension, 'cp');
    return `${source}-${resultKind}-v${APP_VERSION}-${timestamp(date)}.${ext}`;
  }

  function splitCpName(originalBase, inputSourceName) {
    const sourceFromInput = safeBaseName(inputSourceName || '', '');
    if (sourceFromInput) {
      const prefix = `${sourceFromInput}-`;
      return {
        source: sourceFromInput,
        kind: originalBase.startsWith(prefix)
          ? (originalBase.slice(prefix.length) || 'strict')
          : (originalBase === sourceFromInput ? 'strict' : originalBase),
      };
    }
    const variantMatch = originalBase.match(/^(.*?)-(construction-[a-z0-9-]+)$/i);
    if (variantMatch) {
      return { source: variantMatch[1], kind: variantMatch[2] };
    }
    const strictMatch = originalBase.match(/^(.*?)-strict$/i);
    if (strictMatch) {
      return { source: strictMatch[1], kind: 'strict' };
    }
    return { source: originalBase, kind: 'strict' };
  }

  window.oriredrawFormatDownloadFilename = formattedDownloadFilename;

  const nativeAnchorClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function oriredrawVersionedDownload(...args) {
    if (typeof this.download === 'string' && this.download) {
      const lower = this.download.toLowerCase();
      const input = document.querySelector('#image-input');
      const inputSource = input?.files?.[0]?.name || '';
      if (lower.endsWith('.cp')) {
        const originalBase = safeBaseName(this.download, 'reconstructed');
        const parsed = splitCpName(originalBase, inputSource);
        this.download = formattedDownloadFilename(parsed.source, parsed.kind, 'cp');
      } else if (lower.endsWith('.oriredraw')) {
        const originalBase = safeBaseName(this.download, 'oriredraw');
        const source = inputSource ? safeBaseName(inputSource, originalBase) : originalBase;
        this.download = formattedDownloadFilename(source, 'project', 'oriredraw');
      }
    }
    return nativeAnchorClick.apply(this, args);
  };
})();
