(() => {
  'use strict';

  const APP_VERSION = '0.2.0-dev.9';
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
})();
