(() => {
  'use strict';

  const bridge = window.oriredrawProjectBridge;
  if (!bridge) return;

  const originalTrace = new WeakMap();

  function remember(root) {
    if (!root || originalTrace.has(root)) return;
    originalTrace.set(
      root,
      Array.isArray(root.playback_trace) ? root.playback_trace : [],
    );
  }

  // playback.js registered its normal bubbling click handler before project.js
  // is imported.  Capture here so the root trace is swapped before that
  // handler rebuilds its generation groups.
  document.addEventListener('click', event => {
    const button = event.target.closest?.('#version-tabs button[data-version]');
    if (!button) return;
    const root = bridge.result;
    if (!root) return;
    remember(root);

    const versions = [root, ...(root.variants || [])];
    const index = Math.max(0, Number(button.dataset.version || 0));
    const version = versions[index] || root;
    if (index === 0) {
      root.playback_trace = originalTrace.get(root) || [];
      return;
    }
    root.playback_trace = Array.isArray(version.playback_trace)
      ? version.playback_trace
      : (originalTrace.get(root) || []);
  }, true);
})();
