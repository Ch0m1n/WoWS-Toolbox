// Portable viewer sessions contain metadata only, never meshes or textures.
const core = window.WoWSViewerCore;
if (core) {
  const english = new URLSearchParams(location.search).get('lang') === 'en';
  const t = (ko, en) => english ? en : ko;
  let identity = null;
  let modelReady = false;
  let conditions = null;
  let compareConditions = null;
  const shell = document.createElement('section');
  shell.style.cssText = 'position:absolute;bottom:12px;left:12px;max-width:430px;background:#10213a;padding:10px;border-radius:8px;z-index:8';
  const note = document.createElement('textarea');
  note.maxLength = 2000;
  note.rows = 2;
  note.placeholder = t('이 모델에 대한 메모', 'Notes for this model');
  note.style.cssText = 'display:block;width:330px;margin-bottom:8px';
  const summary = document.createElement('details');
  const heading = document.createElement('summary');
  heading.textContent = t('모델·비교 조건', 'Model / comparison conditions');
  const info = document.createElement('pre');
  info.style.cssText = 'white-space:pre-wrap;max-height:180px;overflow:auto';
  summary.append(heading, info);
  shell.append(note, summary);
  core.viewportShell.append(shell);

  function partKeys() {
    const seen = new Map();
    return core.getParts().map(part => {
      const label = `${part.name}|${part.userData.viewerLabel || ''}`;
      const occurrence = seen.get(label) || 0;
      seen.set(label, occurrence + 1);
      return [`${label}|${occurrence}`, part];
    });
  }
  function modelStats(root = core.getShipRoot(), parts = core.getParts()) {
    let triangles = 0;
    const materials = new Set();
    for (const part of parts) {
      if (part.geometry) triangles += (part.geometry.index?.count || part.geometry.attributes.position?.count || 0) / 3;
      for (const material of Array.isArray(part.material) ? part.material : [part.material]) if (material) materials.add(material.name || material.uuid);
    }
    const bounds = root ? new core.THREE.Box3().setFromObject(root) : null;
    return {parts: parts.length, materials: materials.size, triangles: Math.round(triangles),
      bounds: bounds ? {min: bounds.min.toArray(), max: bounds.max.toArray()} : null};
  }
  function comparisonStats() {
    const root = window.WoWSViewerAdvanced?.getCompareRoot?.();
    if (!root) return null;
    const parts = []; root.traverse(part => {if (part.isMesh) parts.push(part);});
    return modelStats(root, parts);
  }
  function renderConditions() {
    info.textContent = JSON.stringify({model: conditions, comparison: compareConditions, statistics: modelStats(), comparisonStatistics: comparisonStats(),
      boundsFrame: "viewer world coordinates; current transforms included",
      scope: t('옵션·구조 비교이며 외형 일치를 보증하지 않아요.', 'Conditions and structure only; visual equivalence is not guaranteed.')}, null, 2);
  }
  summary.addEventListener('toggle', renderConditions);
  function button(text, action) {
    const node = document.createElement('button'); node.textContent = text;
    node.style.margin = '8px 8px 0 0'; node.addEventListener('click', action); shell.append(node);
  }
  button(t('세션 저장', 'Save session'), () => {
    if (!identity || !modelReady) return core.setStatus(t('모델을 먼저 열어 주세요.', 'Open a model first.'), true);
    core.hostMessage({type: 'saveViewerSession', session: {
      schema: 'wows-toolbox-viewer-session/v1', model: identity,
      camera: core.camera.position.toArray(), target: core.orbit.target.toArray(), up: core.camera.up.toArray(),
      hidden: partKeys().filter(([,part]) => !part.visible).map(([key]) => key),
      note: note.value, layout: {inspectorWidth: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--inspector-width')) || 300},
    }});
  });
  button(t('세션 불러오기', 'Load session'), () => core.hostMessage({type: 'loadViewerSession'}));
  button(t('조건 새로 표시', 'Refresh conditions'), renderConditions);

  const vector = value => Array.isArray(value) && value.length === 3 && value.every(n => Number.isFinite(n) && Math.abs(n) < 1e8);
  function restore(session) {
    if (!modelReady || session?.schema !== 'wows-toolbox-viewer-session/v1' || session.model !== identity) {
      return core.setStatus(t('모델 식별자가 달라 세션을 적용하지 않았어요.', 'Model identity differs; session was not applied.'), true);
    }
    if (!vector(session.camera) || !vector(session.target) || !vector(session.up) || !session.up.some(n => n !== 0) || session.camera.every((n,i) => n === session.target[i]) || !Array.isArray(session.hidden) || session.hidden.length > 20000 || typeof session.note !== 'string' || session.note.length > 2000) {
      return core.setStatus(t('세션 형식이 올바르지 않아요.', 'Invalid session data.'), true);
    }
    const keys = partKeys(); const known = new Set(keys.map(([key]) => key));
    const orphaned = session.hidden.filter(key => typeof key !== 'string' || !known.has(key));
    if (orphaned.length) return core.setStatus(t('세션의 일부 파트를 찾을 수 없어 적용하지 않았어요.', 'Some session parts are unavailable; nothing was applied.'), true);
    const hidden = new Set(session.hidden);
    for (const [key, part] of keys) part.visible = !hidden.has(key);
    core.camera.position.fromArray(session.camera); core.camera.up.fromArray(session.up);
    core.orbit.target.fromArray(session.target); core.orbit.update();
    note.value = session.note;
    const width = Number(session.layout?.inspectorWidth);
    if (Number.isFinite(width)) document.documentElement.style.setProperty('--inspector-width', `${Math.min(560, Math.max(248, width))}px`);
    core.renderPartList(); core.setStatus(t('뷰어 세션을 불러왔어요.', 'Viewer session restored.'));
  }
  window.chrome?.webview?.addEventListener('message', event => {
    const message = event.data;
    if (!message || typeof message !== 'object') return;
    if (message.type === 'loadModel') {
      modelReady = false; identity = message.modelIdentity || null; conditions = message.provenance || null; compareConditions = null; note.value = '';
    } else if (message.type === 'loadCompareModel') {
      compareConditions = message.provenance || null;
    } else if (message.type === 'restoreViewerSession') restore(message.session);
    renderConditions();
  });
  window.addEventListener('wows-viewer-loaded', event => {modelReady = event.detail?.identity === identity; renderConditions();});
  window.addEventListener('wows-viewer-comparison', event => {if (!event.detail?.loaded) compareConditions = null; renderConditions();});
  window.addEventListener('wows-viewer-reset', () => { modelReady = false; compareConditions = null; });
}
