import { OBJLoader } from './vendor/OBJLoader.js';
import { MTLLoader } from './vendor/MTLLoader.js';
const core = window.WoWSViewerCore;

if (core) {
  const {
    THREE, renderer, scene, camera, canvas, viewportShell, orbit, transform,
    raycaster, pointer,
  } = core;
  const partList = document.querySelector('#partList');
  const categoryBar = document.querySelector('#categoryBar');
  const selectionName = document.querySelector('#selectionName');
  const modelOpacity = document.querySelector('#modelOpacity');
  const armorTooltip = document.querySelector('#armorTooltip');
  const armorHoverInfo = document.querySelector('#armorHoverInfo');
  const clipButton = document.querySelector('#clipButton');
  const clipPosition = document.querySelector('#clipPosition');
  const clipReset = document.querySelector('#clipReset');
  const measureButton = document.querySelector('#measureButton');
  const measureReadout = document.querySelector('#measureReadout');
  const waterlineButton = document.querySelector('#waterlineButton');
  const waterlinePanel = document.querySelector('#waterlinePanel');
  const waterlinePosition = document.querySelector('#waterlinePosition');
  const waterlineValue = document.querySelector('#waterlineValue');
  const compareLayoutButton = document.querySelector('#compareLayoutButton');
  const clearCompareButton = document.querySelector('#clearCompareButton');
  const resetPartButton = document.querySelector('#resetPartButton');
  const inspectorResizeHandle = document.querySelector('#inspectorResizeHandle');
  const modelPanel = document.querySelector('#modelPanel');
  const paneResizers = [...document.querySelectorAll('.pane-resizer')];
  const lightingPanel = document.querySelector('#lightingPanel');
  if (lightingPanel && lightingPanel.parentElement !== viewportShell) {
    viewportShell.append(lightingPanel);
  }


  renderer.localClippingEnabled = true;
  const clippingPlane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);
  let clippingEnabled = false;
  let clipAxis = 'x';
  let activeCategory = null;
  const hiddenCategories = new Set();
  const multiSelection = new Set();
  let measureMode = false;
  let measurePoints = [];
  let measurePointerDown = null;
  let compareRoot = null;
  const INSPECTOR_LAYOUT_KEY = 'wows-toolbox-viewer-inspector-layout-v1';
  const inspectorLayout = readInspectorLayout();

  function readInspectorLayout() {
    try {
      const value = JSON.parse(localStorage.getItem(INSPECTOR_LAYOUT_KEY) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch (_) {
      return {};
    }
  }

  function saveInspectorLayout() {
    try { localStorage.setItem(INSPECTOR_LAYOUT_KEY, JSON.stringify(inspectorLayout)); } catch (_) {}
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, Number(value) || minimum));
  }

  function applyInspectorWidth(value, persist = true) {
    const maximum = Math.max(300, Math.min(560, window.innerWidth * 0.55));
    const width = clamp(value, 248, maximum);
    document.documentElement.style.setProperty('--inspector-width', `${Math.round(width)}px`);
    inspectorResizeHandle.setAttribute('aria-valuenow', String(Math.round(width)));
    inspectorResizeHandle.setAttribute('aria-valuemin', '248');
    inspectorResizeHandle.setAttribute('aria-valuemax', String(Math.round(maximum)));
    if (persist) {
      inspectorLayout.width = Math.round(width);
      saveInspectorLayout();
    }
  }

  function paneLimits(pane) {
    const minimum = Number(pane.dataset.minSize || pane.previousElementSibling?.dataset.minSize || 80);
    const otherFixed = [...modelPanel.querySelectorAll('.pane-resizer')]
      .map((handle) => document.querySelector(`#${handle.dataset.pane}`))
      .filter((item) => item && item !== pane)
      .reduce((sum, item) => sum + item.getBoundingClientRect().height, 0);
    const handles = paneResizers.reduce((sum, item) => sum + item.getBoundingClientRect().height, 0);
    const maximum = Math.max(minimum, modelPanel.clientHeight - otherFixed - handles - 110);
    return { minimum, maximum };
  }

  function applyPaneSize(pane, value, persist = true) {
    const { minimum, maximum } = paneLimits(pane);
    const size = clamp(value, minimum, maximum);
    pane.style.flexBasis = `${Math.round(size)}px`;
    const handle = paneResizers.find((item) => item.dataset.pane === pane.id);
    handle?.setAttribute('aria-valuenow', String(Math.round(size)));
    handle?.setAttribute('aria-valuemin', String(Math.round(minimum)));
    handle?.setAttribute('aria-valuemax', String(Math.round(maximum)));
    if (persist) {
      inspectorLayout.panes = inspectorLayout.panes || {};
      inspectorLayout.panes[pane.id] = Math.round(size);
      saveInspectorLayout();
    }
  }

  function restoreInspectorLayout() {
    applyInspectorWidth(inspectorLayout.width || 286, false);
    for (const handle of paneResizers) {
      const pane = document.querySelector(`#${handle.dataset.pane}`);
      if (!pane) continue;
      pane.dataset.minSize = handle.dataset.minSize || '80';
      const saved = inspectorLayout.panes?.[pane.id];
      applyPaneSize(pane, saved || Number(handle.dataset.defaultSize || 120), false);
    }
  }

  inspectorResizeHandle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    inspectorResizeHandle.setPointerCapture(event.pointerId);
    document.body.classList.add('resizing-inspector');
  });
  inspectorResizeHandle.addEventListener('pointermove', (event) => {
    if (!inspectorResizeHandle.hasPointerCapture(event.pointerId)) return;
    applyInspectorWidth(window.innerWidth - event.clientX);
  });
  inspectorResizeHandle.addEventListener('pointerup', (event) => {
    if (inspectorResizeHandle.hasPointerCapture(event.pointerId)) inspectorResizeHandle.releasePointerCapture(event.pointerId);
    document.body.classList.remove('resizing-inspector');
  });
  inspectorResizeHandle.addEventListener('dblclick', () => applyInspectorWidth(286));
  inspectorResizeHandle.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--inspector-width')) || 286;
    applyInspectorWidth(event.key === 'Home' ? 286 : current + (event.key === 'ArrowLeft' ? 12 : -12));
  });

  for (const handle of paneResizers) {
    const pane = document.querySelector(`#${handle.dataset.pane}`);
    if (!pane) continue;
    handle.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add('dragging');
      document.body.classList.add('resizing-pane');
      handle.dataset.dragStartY = String(event.clientY);
      handle.dataset.dragStartSize = String(pane.getBoundingClientRect().height);
    });
    handle.addEventListener('pointermove', (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const startY = Number(handle.dataset.dragStartY || event.clientY);
      const startSize = Number(handle.dataset.dragStartSize || pane.getBoundingClientRect().height);
      applyPaneSize(pane, startSize + event.clientY - startY);
    });
    handle.addEventListener('pointerup', (event) => {
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      handle.classList.remove('dragging');
      document.body.classList.remove('resizing-pane');
    });
    handle.addEventListener('dblclick', () => applyPaneSize(pane, Number(handle.dataset.defaultSize || 120)));
    handle.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) return;
      event.preventDefault();
      const current = pane.getBoundingClientRect().height;
      applyPaneSize(pane, event.key === 'Home' ? Number(handle.dataset.defaultSize || 120) : current + (event.key === 'ArrowDown' ? 12 : -12));
    });
  }

  window.addEventListener('resize', () => {
    applyInspectorWidth(inspectorLayout.width || 286, false);
    paneResizers.forEach((handle) => {
      const pane = document.querySelector(`#${handle.dataset.pane}`);
      if (pane) applyPaneSize(pane, pane.getBoundingClientRect().height, false);
    });
  });
  restoreInspectorLayout();
  let compareMode = 'overlay';
  let compareLoadSerial = 0;
  let waterlineVisible = false;
  const waterline = new THREE.Mesh(
    new THREE.PlaneGeometry(2, 2),
    new THREE.MeshBasicMaterial({
      color: 0x167fb5,
      transparent: true,
      opacity: 0.18,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  waterline.name = 'SEA_SURFACE';
  waterline.rotation.x = -Math.PI / 2;
  waterline.visible = false;
  waterline.renderOrder = 5;
  scene.add(waterline);
  const measureVisuals = new THREE.Group();
  measureVisuals.name = 'MEASUREMENT_HELPERS';
  scene.add(measureVisuals);

  function allMeshes() {
    const meshes = [...core.getParts(), ...core.getArmorMeshes()];
    compareRoot?.traverse((node) => {
      if (node.isMesh) meshes.push(node);
    });
    return meshes;
  }

  function materialsOf(mesh) {
    return (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).filter(Boolean);
  }

  function applyClipping() {
    const normal = {
      x: new THREE.Vector3(1, 0, 0),
      y: new THREE.Vector3(0, 1, 0),
      z: new THREE.Vector3(0, 0, 1),
    }[clipAxis];
    clippingPlane.normal.copy(normal);
    const radius = Math.max(core.getModelRadius(), 0.01);
    clippingPlane.constant = -(Number(clipPosition.value) / 100) * radius;
    for (const mesh of allMeshes()) {
      for (const material of materialsOf(mesh)) {
        material.clippingPlanes = clippingEnabled ? [clippingPlane] : [];
        material.clipShadows = clippingEnabled;
        material.needsUpdate = true;
      }
    }
    clipButton.classList.toggle('active', clippingEnabled);
    document.querySelectorAll('[data-clip-axis]').forEach((button) => {
      button.classList.toggle('active', button.dataset.clipAxis === clipAxis);
    });
  }

  function setClipping(enabled) {
    clippingEnabled = enabled;
    applyClipping();
    core.setStatus(enabled ? `${clipAxis.toUpperCase()}축 단면 분석 중` : '단면 분석 꺼짐');
  }

  function decorateSelection() {
    const rows = [...partList.querySelectorAll('.part-row')];
    for (const row of rows) {
      const part = core.getParts().find((item) => item.userData.viewerLabel === row.title);
      row.classList.toggle('multi-selected', Boolean(part && multiSelection.has(part)));
      if (activeCategory && part) {
        row.hidden = part.userData.viewerType !== activeCategory;
      } else {
        row.hidden = false;
      }
    }
    if (multiSelection.size > 1) {
      selectionName.textContent = `${multiSelection.size}개 파트 선택`;
    }
  }

  function buildCategories() {
    const parts = core.getParts();
    const counts = new Map();
    for (const part of parts) {
      const category = part.userData.viewerType || '기타';
      counts.set(category, (counts.get(category) || 0) + 1);
    }
    const signature = JSON.stringify([...counts]);
    if (categoryBar.dataset.signature === signature) {
      decorateSelection();
      applyClipping();
      return;
    }
    categoryBar.dataset.signature = signature;
    categoryBar.replaceChildren();
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'category-chip active';
    all.textContent = `전체 ${parts.length}`;
    all.addEventListener('click', () => {
      activeCategory = null;
      categoryBar.querySelectorAll('.category-chip').forEach((item) => item.classList.remove('active'));
      all.classList.add('active');
      decorateSelection();
    });
    categoryBar.append(all);
    for (const [category, count] of [...counts].sort((a, b) => a[0].localeCompare(b[0], 'ko'))) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'category-chip';
      button.textContent = `${category} ${count}`;
      button.title = '클릭: 목록 필터 · 오른쪽 클릭: 분류 전체 숨김/표시';
      button.addEventListener('click', () => {
        activeCategory = activeCategory === category ? null : category;
        categoryBar.querySelectorAll('.category-chip').forEach((item) => item.classList.remove('active'));
        (activeCategory ? button : all).classList.add('active');
        decorateSelection();
      });
      button.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        if (hiddenCategories.has(category)) hiddenCategories.delete(category);
        else hiddenCategories.add(category);
        for (const part of core.getParts()) {
          if (part.userData.viewerType === category) part.visible = !hiddenCategories.has(category);
        }
        button.classList.toggle('hidden-category', hiddenCategories.has(category));
        core.renderPartList();
      });
      categoryBar.append(button);
    }
    decorateSelection();
    applyClipping();
  }

  const listObserver = new MutationObserver(() => buildCategories());
  listObserver.observe(partList, { childList: true });

  partList.addEventListener('click', (event) => {
    const row = event.target.closest('.part-row');
    if (!row) return;
    const part = core.getParts().find((item) => item.userData.viewerLabel === row.title);
    if (!part) return;
    if (event.ctrlKey || event.shiftKey) {
      event.stopImmediatePropagation();
      if (multiSelection.has(part)) multiSelection.delete(part);
      else multiSelection.add(part);
      if (multiSelection.size === 1) core.selectPart([...multiSelection][0]);
      else if (multiSelection.size === 0) core.selectPart(null);
      else transform.detach();
      decorateSelection();
      core.hostMessage({
        type: 'multiSelection',
        count: multiSelection.size,
        names: [...multiSelection].map((item) => item.userData.viewerLabel),
      });
    } else {
      multiSelection.clear();
      multiSelection.add(part);
      queueMicrotask(decorateSelection);
    }
  }, true);

  window.addEventListener('wows-viewer-selection', (event) => {
    multiSelection.clear();
    const part = event.detail?.part;
    if (part) multiSelection.add(part);
    queueMicrotask(decorateSelection);
  });



  modelOpacity.addEventListener('input', () => {
    const opacity = Number(modelOpacity.value) / 100;
    for (const mesh of core.getParts()) {
      for (const material of materialsOf(mesh)) {
        material.opacity = opacity;
        material.transparent = opacity < 0.999;
        material.depthWrite = opacity >= 0.999;
        material.userData.viewerOpacity = opacity;
        material.userData.viewerTransparent = material.transparent;
        material.userData.viewerDepthWrite = material.depthWrite;
        material.needsUpdate = true;
      }
    }
  });

  clipButton.addEventListener('click', () => setClipping(!clippingEnabled));
  clipReset.addEventListener('click', () => {
    clipPosition.value = '0';
    setClipping(false);
  });
  clipPosition.addEventListener('input', () => {
    if (!clippingEnabled) clippingEnabled = true;
    applyClipping();
  });
  document.querySelectorAll('[data-clip-axis]').forEach((button) => {
    button.addEventListener('click', () => {
      clipAxis = button.dataset.clipAxis;
      clippingEnabled = true;
      applyClipping();
    });
  });

  function setPointer(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    return rect;
  }

  function exactThicknessLabel(value) {
    const rounded = Math.round(value);
    return Math.abs(value - rounded) < 0.005
      ? `${rounded} mm`
      : `${value.toFixed(2).replace(/\.?0+$/, '')} mm`;
  }

  function effectiveArmor(hit) {
    const meta = hit.object.userData.armorMeta || {};
    const thickness = Number(meta.thickness_mm);
    const exact = meta.exact === true && Number.isFinite(thickness) && thickness > 0;
    const normal = hit.face?.normal?.clone() || new THREE.Vector3(0, 1, 0);
    normal.applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld)).normalize();
    const incidence = Math.abs(normal.dot(raycaster.ray.direction));
    const angle = THREE.MathUtils.radToDeg(Math.acos(Math.min(1, Math.max(0, incidence))));
    const effective = exact ? thickness / Math.max(incidence, 0.08) : null;
    return { meta, thickness, exact, angle, effective };
  }

  function renderArmorInfo(container, zone, info) {
    const title = document.createElement('strong');
    title.textContent = String(zone).slice(0, 80);
    const thicknessText = info.exact
      ? exactThicknessLabel(info.thickness)
      : '정확 두께 정보 없음';
    const materialText = info.meta.material_name
      ? ` · ${String(info.meta.material_name).slice(0, 80)}`
      : '';
    const lineOne = document.createTextNode(
      `${thicknessText}${materialText} · 입사각 ${info.angle.toFixed(1)}°`,
    );
    const lineTwo = document.createTextNode(
      info.exact
        ? `시선 기준 유효 두께 약 ${info.effective.toFixed(1)} mm`
        : '정확값을 보려면 이 버전에서 함선을 다시 추출해 주세요.',
    );
    const nodes = [
      title,
      document.createElement('br'),
      lineOne,
      document.createElement('br'),
      lineTwo,
    ];
    if (info.exact && Array.isArray(info.meta.layers_mm) && info.meta.layers_mm.length > 1) {
      nodes.push(
        document.createElement('br'),
        document.createTextNode(
          `전체 장갑층 ${info.meta.layers_mm.map(exactThicknessLabel).join(' + ')}`,
        ),
      );
    }
    container.replaceChildren(...nodes);
  }

  canvas.addEventListener('pointermove', (event) => {
    if (core.getDisplayMode() !== 'armor') {
      armorTooltip.hidden = true;
      return;
    }
    const rect = setPointer(event);
    const hit = raycaster.intersectObjects(
      core.getArmorMeshes().filter((mesh) => mesh.visible),
      false,
    )[0];
    if (!hit) {
      armorTooltip.hidden = true;
      return;
    }
    const info = effectiveArmor(hit);
    const zone = hit.object.userData.armorZone || '미분류';
    renderArmorInfo(armorTooltip, zone, info);
    armorTooltip.style.left = `${Math.min(rect.width - 250, event.clientX - rect.left + 14)}px`;
    armorTooltip.style.top = `${Math.max(78, event.clientY - rect.top + 12)}px`;
    armorTooltip.hidden = false;
    renderArmorInfo(armorHoverInfo, zone, info);
  });

  function clearMeasurement() {
    measurePoints = [];
    measureVisuals.traverse((node) => {
      node.geometry?.dispose?.();
      node.material?.dispose?.();
    });
    measureVisuals.clear();
    measureReadout.hidden = true;
  }

  function addMeasurePoint(point) {
    if (measurePoints.length >= 2) clearMeasurement();
    measurePoints.push(point.clone());
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(Math.max(core.getModelRadius() * 0.008, 0.01), 14, 10),
      new THREE.MeshBasicMaterial({ color: 0x61d7db, depthTest: false }),
    );
    marker.position.copy(point);
    marker.renderOrder = 100;
    measureVisuals.add(marker);
    if (measurePoints.length === 2) {
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(measurePoints),
        new THREE.LineBasicMaterial({ color: 0x61d7db, depthTest: false }),
      );
      line.renderOrder = 99;
      measureVisuals.add(line);
      const distance = measurePoints[0].distanceTo(measurePoints[1]);
      measureReadout.textContent = `두 점 거리 ${distance.toFixed(3)} 모델 단위`;
      measureReadout.hidden = false;
      core.setStatus(`측정 완료 · ${distance.toFixed(3)} 모델 단위`);
      core.hostMessage({ type: 'measurement', distance });
    } else {
      core.setStatus('첫 번째 점 지정 · 두 번째 점을 골라 주세요');
    }
  }

  measureButton.addEventListener('click', () => {
    measureMode = !measureMode;
    measureButton.classList.toggle('active', measureMode);
    if (measureMode) {
      transform.detach();
      orbit.enabled = true;
      core.setStatus('측정 모드 · 표면의 두 점을 차례로 선택해요');
    } else {
      clearMeasurement();
      core.setStatus('측정 모드 꺼짐');
    }
  });

  canvas.addEventListener('pointerdown', (event) => {
    if (measureMode && event.button === 0) {
      measurePointerDown = { x: event.clientX, y: event.clientY };
    }
  });

  canvas.addEventListener('pointerup', (event) => {
    if (!measureMode || event.button !== 0 || !measurePointerDown) return;
    event.stopImmediatePropagation();
    const distance = Math.hypot(
      event.clientX - measurePointerDown.x,
      event.clientY - measurePointerDown.y,
    );
    measurePointerDown = null;
    if (distance > 4) return;
    setPointer(event);
    const targets = core.getDisplayMode() === 'armor'
      ? core.getArmorMeshes().filter((mesh) => mesh.visible)
      : core.getParts().filter((mesh) => mesh.visible);
    const hit = raycaster.intersectObjects(targets, false)[0];
    if (hit) addMeasurePoint(hit.point);
  }, true);

  function activeTransformParts() {
    const currentParts = new Set(core.getParts());
    for (const part of [...multiSelection]) {
      if (!currentParts.has(part)) multiSelection.delete(part);
    }
    if (multiSelection.size) return [...multiSelection];
    const selectedRow = partList.querySelector('.part-row.selected');
    if (!selectedRow) return [];
    const part = core.getParts().find(
      (item) => item.userData.viewerLabel === selectedRow.title,
    );
    return part ? [part] : [];
  }


  function resetSelected() {
    const targets = activeTransformParts();
    if (!targets.length) {
      core.setStatus('먼저 되돌릴 파트를 선택해 주세요');
      return;
    }
    core.recordObjectEdit(targets, '파트 원위치', () => {
      for (const part of targets) {
        if (part.userData.originalPosition) part.position.copy(part.userData.originalPosition);
        if (part.userData.originalScale) part.scale.copy(part.userData.originalScale);
      }
    });
    core.setStatus(`${targets.length}개 파트를 추출 위치로 되돌렸어요`);
  }

  function disposeCompare(silent = false, invalidateLoads = true) {
    if (invalidateLoads) compareLoadSerial += 1;
    if (!compareRoot) return;
    const textures = new Set();
    compareRoot.traverse((node) => {
      node.geometry?.dispose?.();
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      materials.filter(Boolean).forEach((material) => {
        for (const value of Object.values(material)) {
          if (value?.isTexture) textures.add(value);
        }
        material.dispose?.();
      });
    });
    textures.forEach((texture) => texture.dispose?.());
    scene.remove(compareRoot);
    const main = core.getShipRoot();
    if (main) main.position.x = 0;
    compareRoot = null;
    compareLayoutButton.disabled = true;
    clearCompareButton.disabled = true;
    compareLayoutButton.classList.remove('active');
    if (!silent) {
      core.frameModel();
      core.setStatus('비교 모델을 닫았어요');
    }
  }

  function frameComparison(
    direction = new THREE.Vector3(1.25, 0.75, 1.4),
    topView = false,
  ) {
    const main = core.getShipRoot();
    if (!compareRoot || !main) return false;
    const union = new THREE.Box3()
      .setFromObject(main)
      .union(new THREE.Box3().setFromObject(compareRoot));
    const center = union.getCenter(new THREE.Vector3());
    const sphere = union.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sphere.radius, 0.01);
    const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov * 0.5)) * 1.15;
    camera.position.copy(center).addScaledVector(direction.clone().normalize(), distance);
    camera.up.set(0, 1, 0);
    if (topView) camera.up.set(0, 0, -1);
    camera.near = Math.max(radius / 100, 0.005);
    camera.far = Math.max(radius * 25, 100);
    camera.updateProjectionMatrix();
    orbit.target.copy(center);
    orbit.minDistance = radius * 0.04;
    orbit.maxDistance = radius * 30;
    orbit.update();
    return true;
  }

  function applyCompareLayout() {
    if (!compareRoot) return;
    const main = core.getShipRoot();
    main.position.x = 0;
    compareRoot.position.x = 0;
    if (compareMode === 'side') {
      const mainBox = new THREE.Box3().setFromObject(main);
      const compareBox = new THREE.Box3().setFromObject(compareRoot);
      const spacing = Math.max(
        mainBox.getSize(new THREE.Vector3()).x,
        compareBox.getSize(new THREE.Vector3()).x,
        core.getModelRadius(),
      ) * 0.65;
      main.position.x = -spacing;
      compareRoot.position.x = spacing;
    }
    frameComparison();
    compareLayoutButton.textContent = compareMode === 'overlay' ? '비교 겹침' : '비교 좌우';
    compareLayoutButton.classList.toggle('active', compareMode === 'side');
  }

  function disposeDetachedObject(root) {
    if (!root) return;
    const textures = new Set();
    root.traverse((node) => {
      node.geometry?.dispose?.();
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      materials.filter(Boolean).forEach((material) => {
        Object.values(material).forEach((value) => {
          if (value?.isTexture) textures.add(value);
        });
        material.dispose?.();
      });
    });
    textures.forEach((texture) => texture.dispose?.());
    root.removeFromParent?.();
  }

  function ensureTrustedCompareUrl(value, field) {
    if (!value) return;
    const url = new URL(value, window.location.href);
    if (url.protocol !== 'https:' || url.hostname !== 'compare.local') {
      throw new Error(`${field} 주소가 허용된 모델 폴더를 벗어났어요.`);
    }
  }

  async function loadCompare(message) {
    const loadId = ++compareLoadSerial;
    disposeCompare(true, false);
    ensureTrustedCompareUrl(message.objUrl, '비교 OBJ');
    ensureTrustedCompareUrl(message.mtlUrl, '비교 MTL');
    ensureTrustedCompareUrl(message.resourceBaseUrl, '비교 리소스');
    ensureTrustedCompareUrl(message.assemblyReportUrl, '비교 조립 방향');
    ensureTrustedCompareUrl(message.modelReportUrl, '비교 파트 원점');
    core.setStatus('비교 모델을 읽는 중이에요...');
    THREE.Cache.clear();
    const manager = new THREE.LoadingManager();
    const loader = new OBJLoader(manager);
    let materials = null;
    if (message.mtlUrl) {
      try {
        const mtlLoader = new MTLLoader(manager);
        mtlLoader.setMaterialOptions({
          ignoreTextureTypes: [
            'specularMap', 'normalMap', 'bumpMap', 'roughnessMap',
            'metalnessMap', 'aoMap', 'emissiveMap', 'displacementMap',
          ],
        });
        if (message.resourceBaseUrl) mtlLoader.setResourcePath(message.resourceBaseUrl);
        materials = await core.loadResourceWithRetry(
          mtlLoader,
          message.mtlUrl,
          () => loadId === compareLoadSerial,
          '비교 MTL',
        );
        if (loadId !== compareLoadSerial) {
          Object.values(materials.materials || {}).forEach((material) => material.dispose?.());
          return;
        }
        materials.preload();
        loader.setMaterials(materials);
      } catch (error) {
        if (loadId !== compareLoadSerial) return;
        console.warn('Compare MTL load failed.', error);
        core.hostMessage({ type: 'warning', message: `비교 MTL 불러오기 실패: ${error.message || error}` });
      }
    }
    let loaded;
    try {
      loaded = await core.loadResourceWithRetry(
        loader,
        message.objUrl,
        () => loadId === compareLoadSerial,
        '비교 OBJ',
      );
    } catch (error) {
      if (loadId !== compareLoadSerial) return;
      throw error;
    }
    if (loadId !== compareLoadSerial) {
      disposeDetachedObject(loaded);
      return;
    }
    const nextCompareRoot = new THREE.Group();
    nextCompareRoot.name = `COMPARE_${String(message.displayName || 'MODEL').slice(0, 160)}`;
    let assemblyMetadata = null;
    if (message.assemblyReportUrl) {
      try {
        assemblyMetadata = await core.loadAssemblyMetadata(message.assemblyReportUrl);
      } catch (error) {
        if (loadId !== compareLoadSerial) return;
        console.warn('Compare assembly metadata load failed.', error);
        core.hostMessage({ type: 'warning', message: `비교 조립 방향 데이터 불러오기 실패: ${error.message || error}` });
      }
    }
    let modelMetadata = null;
    if (message.modelReportUrl) {
      try {
        modelMetadata = await core.loadModelMetadata(message.modelReportUrl);
      } catch (error) {
        if (loadId !== compareLoadSerial) return;
        console.warn('Compare model metadata load failed.', error);
        core.hostMessage({ type: 'warning', message: `비교 파트 원점 데이터 불러오기 실패: ${error.message || error}` });
      }
    }
    core.normalizeModelMaterials(loaded, assemblyMetadata);
    core.applyModelMetadata(loaded, modelMetadata);
    core.orientObjForViewer(loaded, loaded, modelMetadata);
    nextCompareRoot.add(loaded);
    nextCompareRoot.updateMatrixWorld(true);
    const center = new THREE.Box3().setFromObject(loaded).getCenter(new THREE.Vector3());
    loaded.position.sub(center);
    loaded.traverse((node) => {
      if (!node.isMesh) return;
      const originals = Array.isArray(node.material) ? node.material : [node.material];
      node.material = originals.map((material) => {
        const clone = material?.clone?.() || new THREE.MeshStandardMaterial();
        if (clone.color) clone.color.lerp(new THREE.Color(0x3ed5ff), 0.55);
        clone.transparent = true;
        clone.opacity = 0.56;
        clone.depthWrite = false;
        return clone;
      });
      if (node.material.length === 1) node.material = node.material[0];
      originals.filter(Boolean).forEach((material) => material.dispose?.());
    });
    if (loadId !== compareLoadSerial) {
      disposeDetachedObject(nextCompareRoot);
      return;
    }
    compareRoot = nextCompareRoot;
    scene.add(compareRoot);
    compareMode = 'overlay';
    compareLayoutButton.disabled = false;
    clearCompareButton.disabled = false;
    applyClipping();
    applyCompareLayout();
    core.setStatus(`비교 모델 준비 완료 · ${message.displayName || '두 번째 모델'}`);
    core.hostMessage({ type: 'compareLoaded', name: message.displayName || '비교 모델' });
  }

  function updateWaterline() {
    const radius = Math.max(core.getModelRadius(), 0.01);
    // OBJ coordinates place the source waterline at Y=0. The core recenters
    // modelContent for camera framing, so the water plane must inherit that
    // translation before applying the user's relative adjustment.
    const sourceWaterline = Number(core.getModelContent()?.position?.y || 0);
    const adjustment = (Number(waterlinePosition.value) / 100) * radius;
    const height = sourceWaterline + adjustment;
    waterline.scale.setScalar(radius * 1.8);
    waterline.position.y = height;
    waterline.visible = waterlineVisible && Boolean(core.getShipRoot());
    waterlineValue.textContent = `Y ${height.toFixed(3)}`;
    waterlineButton.title = `해수면 표시 전환 · 원본 기준 Y ${height.toFixed(3)}`;
  }

  function resetAdvancedState() {
    multiSelection.clear();
    hiddenCategories.clear();
    activeCategory = null;
    categoryBar.dataset.signature = '';
    categoryBar.replaceChildren();
    measureMode = false;
    measurePointerDown = null;
    measureButton.classList.remove('active');
    clearMeasurement();
    clippingEnabled = false;
    clipAxis = 'x';
    clipPosition.value = '0';
    applyClipping();
    waterlineVisible = false;
    waterline.visible = false;
    waterline.position.set(0, 0, 0);
    waterline.scale.set(1, 1, 1);
    waterlinePosition.value = '0';
    waterlineValue.textContent = 'Y 0.000';
    waterlineButton.classList.remove('active');
    waterlineButton.setAttribute('aria-pressed', 'false');
    waterlinePanel.hidden = true;
    modelOpacity.value = '100';
    armorTooltip.hidden = true;
    disposeCompare(true);
    compareMode = 'overlay';
    compareLayoutButton.textContent = '비교 겹침';
    transform.detach();
  }

  window.addEventListener('wows-viewer-reset', resetAdvancedState);

  resetPartButton.addEventListener('click', resetSelected);
  waterlineButton.addEventListener('click', () => {
    waterlineVisible = !waterlineVisible;
    waterlineButton.classList.toggle('active', waterlineVisible);
    waterlinePanel.hidden = !waterlineVisible;
    waterlineButton.setAttribute('aria-pressed', String(waterlineVisible));
    updateWaterline();
    core.setStatus(waterlineVisible ? '해수면 표시 켜짐' : '해수면 표시 꺼짐');
  });
  waterlinePosition.addEventListener('input', updateWaterline);
  compareLayoutButton.addEventListener('click', () => {
    compareMode = compareMode === 'overlay' ? 'side' : 'overlay';
    applyCompareLayout();
  });
  clearCompareButton.addEventListener('click', () => disposeCompare());
  window.chrome?.webview?.addEventListener('message', (event) => {
    const message = event.data;
    if (!message || typeof message !== 'object') return;
    if (message.type === 'loadCompareModel') {
      loadCompare(message).catch((error) => {
        core.setStatus(`비교 모델 실패: ${error.message || error}`, true);
        core.hostMessage({ type: 'warning', message: `비교 모델 실패: ${error.message || error}` });
      });
    } else if (message.type === 'clearCompare') {
      disposeCompare();
    }
  });

  window.WoWSViewerAdvanced = {
    frameComparison,
    resetAdvancedState,
  };
  buildCategories();
}

