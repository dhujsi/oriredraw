(() => {
  'use strict';

  const APP_VERSION = '0.2.0-dev.10';
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

  function formattedProjectFilename(date = new Date()) {
    const stamp = [
      date.getFullYear(),
      pad(date.getMonth() + 1),
      pad(date.getDate()),
    ].join('') + '-' + [
      pad(date.getHours()),
      pad(date.getMinutes()),
      pad(date.getSeconds()),
    ].join('');
    return `oriredraw-${stamp}-v${APP_VERSION}.oriredraw`;
  }

  window.oriredrawProjectFilename = formattedProjectFilename;

  // project-core creates a temporary <a> and clicks it immediately.  Keep that
  // implementation simple while standardising every .oriredraw export name in
  // one place together with the app version metadata.
  const nativeAnchorClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function oriredrawVersionedDownload(...args) {
    if (
      typeof this.download === 'string'
      && this.download.toLowerCase().endsWith('.oriredraw')
    ) {
      this.download = formattedProjectFilename();
    }
    return nativeAnchorClick.apply(this, args);
  };
})();
