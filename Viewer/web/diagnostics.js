(() => {
  const post = (payload) => {
    try { window.chrome?.webview?.postMessage(payload); } catch {}
  };
  window.addEventListener('error', (event) => {
    post({ type: 'error', message: `JavaScript 오류: ${event.message || '알 수 없음'} (${event.filename || 'module'}:${event.lineno || 0})` });
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason?.message || String(event.reason || '알 수 없는 Promise 오류');
    post({ type: 'error', message: `모듈 오류: ${reason}` });
  });
  post({ type: 'html-ready' });
})();