import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { TransformControls } from './vendor/TransformControls.js';
import { OBJLoader } from './vendor/OBJLoader.js';
import { MTLLoader } from './vendor/MTLLoader.js';

const canvas = document.querySelector('#viewport');
const viewportShell = document.querySelector('.viewport-shell');
const backgroundButton = document.querySelector('#backgroundButton');
const modelName = document.querySelector('#modelName');
const emptyState = document.querySelector('#emptyState');
const loading = document.querySelector('#loading');
const loadingTitle = document.querySelector('#loadingTitle');
const loadingDetail = document.querySelector('#loadingDetail');
const statusDot = document.querySelector('#statusDot');
const statusText = document.querySelector('#statusText');
const meshStats = document.querySelector('#meshStats');
const partList = document.querySelector('#partList');
const partSearch = document.querySelector('#partSearch');
const visibleCount = document.querySelector('#visibleCount');
const selectionName = document.querySelector('#selectionName');
const modelPanel = document.querySelector('#modelPanel');
const armorPanel = document.querySelector('#armorPanel');
const armorModeButton = document.querySelector('#armorModeButton');
const modelModeButton = document.querySelector('#modelModeButton');
const thicknessFilters = document.querySelector('#thicknessFilters');
const zoneFilters = document.querySelector('#zoneFilters');
const armorOpacity = document.querySelector('#armorOpacity');
const armorSummary = document.querySelector('#armorSummary');
const inspectorTitle = document.querySelector('.inspector-head strong');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: false, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.toneMappingExposure = 1.08;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 50000);
camera.position.set(7, 5, 9);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.enableDamping = true;
orbit.dampingFactor = 0.075;
orbit.screenSpacePanning = true;
orbit.minDistance = 0.05;
orbit.maxDistance = 50000;
orbit.target.set(0, 0, 0);

scene.add(new THREE.AmbientLight(0xffffff, 1.3));
scene.add(new THREE.HemisphereLight(0xf3f5f8, 0x526174, 1.35));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.15);
keyLight.position.set(6, 10, 8);
keyLight.castShadow = true;
scene.add(keyLight);
const rimLight = new THREE.DirectionalLight(0xd9e5f5, 0.72);
rimLight.position.set(-8, 4, -7);
scene.add(rimLight);
const fillLight = new THREE.DirectionalLight(0xffffff, 0.58);
fillLight.position.set(0, -2, 8);
scene.add(fillLight);

const grid = new THREE.GridHelper(2000, 80, 0x2b6585, 0x173149);
grid.material.transparent = true;
grid.material.opacity = 0.36;
scene.add(grid);

const transform = new TransformControls(camera, renderer.domElement);
const transformHelper = transform.getHelper();
scene.add(transformHelper);
transform.setMode('translate');
transform.addEventListener('dragging-changed', (event) => { orbit.enabled = !event.value; });
transform.addEventListener('mouseDown', beginTransformEdit);
transform.addEventListener('mouseUp', commitTransformEdit);
transform.addEventListener('objectChange', () => updateSelectionBox());

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const selectionBox = new THREE.BoxHelper(undefined, 0x56a8ff);
selectionBox.material.depthTest = false;
selectionBox.material.transparent = true;
selectionBox.material.opacity = 0.95;
selectionBox.visible = false;
scene.add(selectionBox);

let shipRoot = null;
let modelContent = null;
let armorRoot = null;
let armorMeshes = [];
let armorData = null;
let activeArmorBucket = null;
let activeArmorZone = null;
let displayMode = 'model';
let parts = [];
let selected = null;
let modelRadius = 10;
let wireframe = false;
let isolated = false;
let ready = false;
let pointerDown = null;
let modelLoadSerial = 0;
const EDIT_HISTORY_LIMIT = 100;
const undoHistory = [];
const redoHistory = [];
let pendingTransformEdit = null;
const BACKGROUND_VISIBILITY_KEY = 'wows-toolbox-viewer-background';
let backgroundVisible = true;
const ARMOR_GHOST_RENDER_ORDER = 100;
const ARMOR_RENDER_ORDER_BASE = 1000;

function hostMessage(payload) {
  try { window.chrome?.webview?.postMessage(payload); } catch (_) {}
}

function setStatus(text, isError = false) {
  statusText.textContent = text;
  statusDot.classList.toggle('error', isError);
}

function readBackgroundVisibility() {
  try { return localStorage.getItem(BACKGROUND_VISIBILITY_KEY) !== 'hidden'; }
  catch (_) { return true; }
}

function setBackgroundVisible(visible, { persist = true, announce = false } = {}) {
  backgroundVisible = Boolean(visible);
  viewportShell.classList.toggle('background-hidden', !backgroundVisible);
  backgroundButton.classList.toggle('active', backgroundVisible);
  backgroundButton.setAttribute('aria-pressed', String(backgroundVisible));
  if (persist) {
    try {
      localStorage.setItem(BACKGROUND_VISIBILITY_KEY, backgroundVisible ? 'visible' : 'hidden');
    } catch (_) {}
  }
  if (announce) setStatus(backgroundVisible ? '배경 표시' : '배경 숨김');
}

function showLoading(title, detail) {
  loadingTitle.textContent = title;
  loadingDetail.textContent = detail;
  loading.hidden = false;
}

function hideLoading() { loading.hidden = true; }

function normalizeViewerMaterial(material) {
  if (!material || material.userData.viewerMaterialPolicy === 'paint-v2') return false;
  material.userData.viewerMaterialPolicy = 'paint-v2';
  material.userData.viewerSourceShininess = Number(material.shininess ?? 0);
  material.userData.viewerSourceSpecular = material.specular?.getHex?.() ?? null;
  if (material.map) {
    material.map.colorSpace = THREE.SRGBColorSpace;
    material.color?.setRGB(1, 1, 1);
  }
  if (material.isMeshPhongMaterial) {
    material.specular?.setRGB(0.025, 0.025, 0.025);
    material.shininess = 16;
  }
  if (material.bumpMap && !material.normalMap) {
    material.normalMap = material.bumpMap;
    material.bumpMap = null;
    material.normalMap.colorSpace = THREE.NoColorSpace;
    material.normalScale?.set(0.62, 0.62);
  }
  material.needsUpdate = true;
  return true;
}


function matrixRowsDeterminant(rows) {
  if (!Array.isArray(rows) || rows.length < 3 || rows.slice(0, 3).some((row) => !Array.isArray(row) || row.length < 3)) {
    return null;
  }
  const m = rows.slice(0, 3).map((row) => row.slice(0, 3).map(Number));
  if (m.flat().some((value) => !Number.isFinite(value))) return null;
  return (
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
    - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
    + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
  );
}

function normalizeAssemblyMetadata(data) {
  const nativeCorrected = data?.engine === 'native_python_obj/v1'
    && data?.combined_obj?.checks?.native_no_blender === true;
  const corrected = nativeCorrected
    || data?.combined_obj?.checks?.mirrored_winding_corrected === true;
  const records = Array.isArray(data?.mounts?.records) ? data.mounts.records : [];
  const mirroredPrefixes = corrected ? [] : records
    .filter((record) => {
      const determinant = matrixRowsDeterminant(record?.blender_matrix_rows);
      return determinant !== null && determinant < -1e-7;
    })
    .map((record) => String(record?.object || '').trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  return { corrected, mirroredPrefixes };
}

async function loadAssemblyMetadata(url) {
  if (!url) return null;
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`조립 방향 데이터 HTTP ${response.status}`);
  const data = await response.json();
  const legacy = Array.isArray(data?.mounts?.records);
  const native = data?.engine === 'native_python_obj/v1'
    && data?.combined_obj?.checks?.native_no_blender === true;
  if (!data || (!legacy && !native)) {
    throw new Error('지원하지 않는 조립 방향 데이터 형식이에요.');
  }
  return normalizeAssemblyMetadata(data);
}

function isAssemblyMirroredNode(node, assemblyMetadata) {
  if (!assemblyMetadata || assemblyMetadata.corrected) return false;
  const names = [node?.name, node?.parent?.name].map((value) => String(value || ''));
  return assemblyMetadata.mirroredPrefixes.some(
    (prefix) => names.some((name) => name === prefix || name.startsWith(`${prefix}_`)),
  );
}

function cloneNodeMaterialsForSide(node, side, policy) {
  const originals = Array.isArray(node.material) ? node.material : [node.material];
  const clones = originals.map((material) => {
    if (!material) return material;
    const clone = material.clone();
    clone.userData = { ...(material.userData || {}) };
    clone.side = side;
    clone.userData.viewerWindingPolicy = policy;
    clone.needsUpdate = true;
    return clone;
  });
  node.material = Array.isArray(node.material) ? clones : clones[0];
  return clones.filter(Boolean).length;
}

function normalizeModelMaterials(root, assemblyMetadata = null) {
  const normalized = new Set();
  let mirroredPartMaterials = 0;
  root?.traverse((node) => {
    if (!node.isMesh) return;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    materials.filter(Boolean).forEach((material) => {
      if (!normalized.has(material)) {
        normalizeViewerMaterial(material);
        normalized.add(material);
      }
    });
    const reportMirrored = isAssemblyMirroredNode(node, assemblyMetadata);
    const unverifiedObj = !assemblyMetadata;
    if (reportMirrored || unverifiedObj) {
      mirroredPartMaterials += cloneNodeMaterialsForSide(
        node,
        THREE.DoubleSide,
        reportMirrored ? 'assembly-mirrored-double-sided-v2' : 'unverified-obj-double-sided-v3',
      );
    }
  });
  return normalized.size + mirroredPartMaterials;
}

function disposeMaterial(material) {
  if (!material) return;
  for (const key of Object.keys(material)) {
    const value = material[key];
    if (value?.isTexture) value.dispose();
  }
  material.dispose?.();
}

function disposeObject3D(root) {
  if (!root) return;
  root.traverse((node) => {
    node.geometry?.dispose?.();
    if (Array.isArray(node.material)) node.material.forEach(disposeMaterial);
    else disposeMaterial(node.material);
  });
  root.removeFromParent?.();
}

function disposeMaterialCreator(creator) {
  if (!creator?.materials) return;
  Object.values(creator.materials).forEach(disposeMaterial);
}

function snapshotObjects(objects) {
  return [...new Set(objects || [])]
    .filter((object) => object?.isObject3D)
    .map((object) => ({
      object,
      position: object.position.clone(),
      quaternion: object.quaternion.clone(),
      scale: object.scale.clone(),
      visible: object.visible,
      traverseDegrees: Number(object.userData.viewerTraverseDegrees || 0),
      barrelDegrees: Number(object.userData.viewerBarrelDegrees || 0),
    }));
}

function snapshotChanged(before, after) {
  if (before.length !== after.length) return true;
  return before.some((entry, index) => {
    const next = after[index];
    return entry.object !== next.object
      || !entry.position.equals(next.position)
      || !entry.quaternion.equals(next.quaternion)
      || !entry.scale.equals(next.scale)
      || entry.visible !== next.visible
      || entry.traverseDegrees !== next.traverseDegrees
      || entry.barrelDegrees !== next.barrelDegrees;
  });
}

function applySnapshot(snapshot) {
  snapshot.forEach((entry) => {
    entry.object.position.copy(entry.position);
    entry.object.quaternion.copy(entry.quaternion);
    entry.object.scale.copy(entry.scale);
    entry.object.visible = entry.visible;
    entry.object.userData.viewerTraverseDegrees = entry.traverseDegrees;
    entry.object.userData.viewerBarrelDegrees = entry.barrelDegrees;
    entry.object.userData.viewerApplyBarrelElevation?.(entry.barrelDegrees);
    entry.object.updateMatrix();
  });
  shipRoot?.updateMatrixWorld(true);
}

function publishHistoryState() {
  hostMessage({
    type: 'history',
    canUndo: undoHistory.length > 0,
    canRedo: redoHistory.length > 0,
    undoLabel: undoHistory.at(-1)?.label || '',
    redoLabel: redoHistory.at(-1)?.label || '',
  });
}

function clearEditHistory() {
  undoHistory.length = 0;
  redoHistory.length = 0;
  pendingTransformEdit = null;
  publishHistoryState();
}

function pushObjectEdit(label, before, after) {
  if (!snapshotChanged(before, after)) return false;
  undoHistory.push({ label, before, after });
  if (undoHistory.length > EDIT_HISTORY_LIMIT) undoHistory.shift();
  redoHistory.length = 0;
  publishHistoryState();
  return true;
}

function refreshAfterEdit() {
  if (selected && !selected.visible) selectPart(null);
  else {
    renderPartList();
    updateSelectionBox();
  }
  window.dispatchEvent(new CustomEvent('wows-viewer-edit', {
    detail: { part: selected },
  }));
}

function recordObjectEdit(objects, label, mutate) {
  const before = snapshotObjects(objects);
  mutate();
  const after = snapshotObjects(before.map((entry) => entry.object));
  const changed = pushObjectEdit(label, before, after);
  if (changed) refreshAfterEdit();
  return changed;
}

function rememberOriginalTransform(object) {
  if (!object?.isObject3D || object.userData.originalQuaternion) return;
  object.userData.originalPosition = object.position.clone();
  object.userData.originalQuaternion = object.quaternion.clone();
  object.userData.originalScale = object.scale.clone();
}

const VIEWER_WEAPON_TYPES = new Set(['주함포', '부포', '대공포', '어뢰', '미사일']);
const VIEWER_WEAPON_CATEGORIES = new Set([
  'main_gun', 'secondary', 'anti_air', 'torpedo', 'missile_launcher',
]);

function isViewerWeaponPart(object) {
  return Boolean(object?.isMesh) && (
    VIEWER_WEAPON_CATEGORIES.has(object.userData.viewerCategory)
    || VIEWER_WEAPON_TYPES.has(object.userData.viewerType)
  );
}

function getPartUpAxisName(object) {
  return object?.userData?.viewerUpAxis === 'z' ? 'z' : 'y';
}

function getPartUpAxis(object) {
  return getPartUpAxisName(object) === 'z'
    ? new THREE.Vector3(0, 0, 1)
    : new THREE.Vector3(0, 1, 0);
}

function ensurePartPivot(object) {
  if (!object?.isMesh || object.userData.viewerPivotSource) return false;
  object.geometry.computeBoundingBox();
  const center = object.geometry.boundingBox?.getCenter(new THREE.Vector3());
  if (!center || ![center.x, center.y, center.z].every(Number.isFinite)) return false;
  object.geometry = object.geometry.clone();
  object.geometry.translate(-center.x, -center.y, -center.z);
  object.position.add(center);
  object.userData.viewerPivot = center.clone();
  object.userData.viewerPivotSource = 'geometry-center';
  return true;
}

function configureTransformAxes(object = selected) {
  const weaponRotation = transform.getMode() === 'rotate' && isViewerWeaponPart(object);
  const upAxis = getPartUpAxisName(object);
  transform.setSpace('local');
  transform.showX = !weaponRotation || upAxis === 'x';
  transform.showY = !weaponRotation || upAxis === 'y';
  transform.showZ = !weaponRotation || upAxis === 'z';
}

function normalizeDegrees(value) {
  let normalized = Number(value) || 0;
  while (normalized > 180) normalized -= 360;
  while (normalized < -180) normalized += 360;
  return normalized;
}

function setPartTraverseDegrees(object, degrees) {
  if (!isViewerWeaponPart(object)) return false;
  ensurePartPivot(object);
  rememberOriginalTransform(object);
  const value = normalizeDegrees(degrees);
  object.quaternion.copy(object.userData.originalQuaternion);
  object.rotateOnAxis(getPartUpAxis(object), THREE.MathUtils.degToRad(value));
  object.userData.viewerTraverseDegrees = value;
  return true;
}

function rotatePartAroundUpAxis(object, deltaRadians) {
  if (!isViewerWeaponPart(object)) return false;
  ensurePartPivot(object);
  rememberOriginalTransform(object);
  object.rotateOnAxis(getPartUpAxis(object), deltaRadians);
  object.userData.viewerTraverseDegrees = normalizeDegrees(
    Number(object.userData.viewerTraverseDegrees || 0) + THREE.MathUtils.radToDeg(deltaRadians),
  );
  return true;
}

function traverseDegreesFromQuaternion(object) {
  if (!isViewerWeaponPart(object) || !object.userData.originalQuaternion) return 0;
  const delta = object.userData.originalQuaternion.clone().invert().multiply(object.quaternion).normalize();
  const axis = getPartUpAxis(object);
  const sine = delta.x * axis.x + delta.y * axis.y + delta.z * axis.z;
  return normalizeDegrees(THREE.MathUtils.radToDeg(2 * Math.atan2(sine, delta.w)));
}

function beginTransformEdit() {
  if (!transform.object) return;
  ensurePartPivot(transform.object);
  rememberOriginalTransform(transform.object);
  pendingTransformEdit = snapshotObjects([transform.object]);
}

function commitTransformEdit() {
  if (!pendingTransformEdit?.length) return;
  const before = pendingTransformEdit;
  pendingTransformEdit = null;
  if (transform.getMode() === 'rotate' && isViewerWeaponPart(transform.object)) {
    transform.object.userData.viewerTraverseDegrees = traverseDegreesFromQuaternion(transform.object);
  }
  const after = snapshotObjects(before.map((entry) => entry.object));
  const label = transform.getMode() === 'rotate' ? '파트 회전' : '파트 이동';
  if (pushObjectEdit(label, before, after)) refreshAfterEdit();
}

function undoViewerEdit() {
  const edit = undoHistory.pop();
  if (!edit) {
    setStatus('실행 취소할 편집이 없어요');
    return false;
  }
  applySnapshot(edit.before);
  redoHistory.push(edit);
  refreshAfterEdit();
  publishHistoryState();
  setStatus(edit.label + ' 실행 취소');
  return true;
}

function redoViewerEdit() {
  const edit = redoHistory.pop();
  if (!edit) {
    setStatus('다시 실행할 편집이 없어요');
    return false;
  }
  applySnapshot(edit.after);
  undoHistory.push(edit);
  refreshAfterEdit();
  publishHistoryState();
  setStatus(edit.label + ' 다시 실행');
  return true;
}

function clearModel(invalidateLoads = true) {
  if (invalidateLoads) modelLoadSerial += 1;
  window.dispatchEvent(new CustomEvent('wows-viewer-reset'));
  clearEditHistory();
  transform.detach();
  selectionBox.visible = false;
  selected = null;
  if (shipRoot) disposeObject3D(shipRoot);
  shipRoot = null;
  modelContent = null;
  armorRoot = null;
  armorMeshes = [];
  armorData = null;
  activeArmorBucket = null;
  activeArmorZone = null;
  displayMode = 'model';
  armorModeButton.disabled = true;
  thicknessFilters.replaceChildren();
  zoneFilters.replaceChildren();
  parts = [];
  isolated = false;
  wireframe = false;
  modelRadius = 10;
  pointerDown = null;
  const wireButton = document.querySelector('#wireButton');
  const isolateButton = document.querySelector('#isolateButton');
  wireButton.classList.remove('active');
  isolateButton.classList.remove('active');
  isolateButton.textContent = '단독 보기';
  setDisplayMode('model');
  renderPartList();
}

function getPartLabel(node, index) {
  const raw = (node.name || node.parent?.name || `PART_${String(index + 1).padStart(3, '0')}`).trim();
  return raw.replace(/^mesh_\d+_?/i, '') || `PART_${index + 1}`;
}

function classifyPart(name) {
  const token = name.toUpperCase();
  if (/(VERTICAL_LAUNCH|GUIDED_MISSILE|MISSILE_LAUNCHER|HP_[AB]GR|VLS)/.test(token)) return '미사일';
  if (/(HP_[AB]GS|SECONDARY_ARTILLERY|SECONDARY|SECGUN|CASEMATE)/.test(token)) return '부포';
  if (/(HP_[AB]GA|ANTIAIR|ANTI_AIR|AIR_DEFENSE|AA_|AAGUN|MACHINEGUN)/.test(token)) return '대공포';
  if (/(HP_[AB]GT|TORPEDO|TTUBE|TORP)/.test(token)) return '어뢰';
  if (/(HP_[AB]D|HP_ARS|RADAR|DIRECTOR|RANGEFINDER|SENSOR)/.test(token)) return '레이더';
  if (/(HP_[AB]GM|MAIN_GUN|MAIN_ARTILLERY|MAIN_BATTERY|TURRET|SHIPMAT_PBS_GUN)/.test(token)) return '주함포';
  if (/^HULL_|(^|[^A-Z])[A-Z]SC\d{3}|SHIPMAT_PBS_HULL/.test(token)) return '선체';
  if (/(DECK|SUPERSTRUCTURE|DECKHOUSE|BRIDGE)/.test(token)) return '상부구조';
  if (/(AIRCRAFT|PLANE|CATAPULT)/.test(token)) return '항공기';
  if (/(FLAG|ROPE|WIRE|ANCHOR|DECOR)/.test(token)) return '장식';
  return '기타';
}

const metadataCategoryLabels = {
  hull: '선체',
  deck_superstructure: '상부구조',
  main_gun: '주함포',
  secondary: '부포',
  anti_air: '대공포',
  torpedo: '어뢰',
  missile_launcher: '미사일',
  radar_sensor: '레이더',
  aircraft: '항공기',
  decoration: '장식',
  other: '기타',
};

async function loadModelMetadata(url) {
  if (!url) return null;
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`파트 원점 데이터 HTTP ${response.status}`);
  const data = await response.json();
  if (data.schema === 'wows-toolbox-model/v1' && Array.isArray(data.objects)) {
    return data;
  }
  if (data.schema === 'wows-toolbox-native-object-layout/v1' && Array.isArray(data.parts)) {
    const nativeCategory = (part) => {
      const token = [
        part?.assembly_kind || '',
        part?.category || '',
        part?.hardpoint || '',
      ].join(' ').toLowerCase();
      if (token.includes('hull')) return 'hull';
      if (token.includes('main_artillery') || token.includes('hp_agm')) return 'main_gun';
      if (token.includes('secondary_artillery') || token.includes('hp_ags')) return 'secondary';
      if (token.includes('air_defense') || token.includes('hp_aga')) return 'anti_air';
      if (token.includes('torpedo')) return 'torpedo';
      if (token.includes('missile')) return 'missile_launcher';
      if (token.includes('radar') || token.includes('director')) return 'radar_sensor';
      if (token.includes('air_armament') || token.includes('aircraft')) return 'aircraft';
      if (token.includes('misc')) return 'decoration';
      return 'other';
    };
    return {
      schema: 'wows-toolbox-model/v1',
      obj_axis_forward: data.obj_axis_forward || '-Z',
      obj_axis_up: data.obj_axis_up || 'Y',
      pivot_space: data.pivot_space || 'obj',
      objects: data.parts.map((part) => ({
        name: part.name,
        pivot: part.pivot,
        matrix_rows: part.matrix_rows,
        category: nativeCategory(part),
        hardpoint: part.hardpoint,
        source_model: part.source_model,
      })),
    };
  }
  throw new Error('지원하지 않는 파트 원점 데이터 형식이에요.');
}

function applyModelMetadata(root, metadata) {
  if (!metadata) return 0;
  const normalizedObjects = metadata.objects
    .map((item) => ({
      item,
      token: String(item.name).trim().replace(/\s+/g, '_'),
    }))
    .sort((a, b) => b.token.length - a.token.length);
  const findItem = (name) => {
    const token = String(name || '');
    return normalizedObjects.find(
      (entry) => token === entry.token || token.startsWith(`${entry.token}_`),
    )?.item;
  };
  let applied = 0;
  root.traverse((node) => {
    if (!node.isMesh) return;
    const item = findItem(node.name) || findItem(node.parent?.name);
    if (!item || !Array.isArray(item.pivot) || item.pivot.length !== 3) return;
    const sourcePivot = item.pivot.map(Number);
    const pivotInObjSpace = metadata.pivot_space === 'obj';
    const standardYUp = metadata.obj_axis_forward === '-Z' && metadata.obj_axis_up === 'Y';
    const legacyZUp = metadata.obj_axis_forward === '-Y' || !metadata.obj_axis_forward;
    const pivot = pivotInObjSpace
      ? new THREE.Vector3(sourcePivot[0], sourcePivot[1], sourcePivot[2])
      : standardYUp
      ? new THREE.Vector3(sourcePivot[0], sourcePivot[2], -sourcePivot[1])
      : new THREE.Vector3(
        sourcePivot[0],
        legacyZUp ? -sourcePivot[1] : sourcePivot[1],
        sourcePivot[2],
      );
    if (![pivot.x, pivot.y, pivot.z].every(Number.isFinite)) return;
    node.geometry = node.geometry.clone();
    node.geometry.translate(-pivot.x, -pivot.y, -pivot.z);
    node.position.add(pivot);
    node.userData.viewerPivot = pivot.clone();
    node.userData.viewerPivotSource = 'model-report';
    node.userData.viewerType = metadataCategoryLabels[item.category] || null;
    node.userData.viewerCategory = item.category || null;
    node.userData.viewerUpAxis = standardYUp ? 'y' : 'z';
    applied += 1;
  });
  return applied;
}

function prepareParts() {
  parts = [];
  const inferredUpAxis = Math.abs(modelContent?.rotation?.x || 0) > Math.PI / 4 ? 'z' : 'y';
  modelContent.traverse((node) => {
    if (!node.isMesh) return;
    node.castShadow = true;
    node.receiveShadow = true;
    node.userData.viewerIndex = parts.length;
    node.userData.viewerLabel = getPartLabel(node, parts.length);
    node.userData.viewerType = node.userData.viewerType || classifyPart(node.userData.viewerLabel);
    node.userData.viewerUpAxis = node.userData.viewerUpAxis || inferredUpAxis;
    node.userData.viewerTraverseDegrees = 0;
    node.userData.viewerBarrelDegrees = 0;
    parts.push(node);
  });
  parts.sort((a, b) => a.userData.viewerLabel.localeCompare(b.userData.viewerLabel, 'ko'));
  renderPartList();
}

function triangleCount() {
  return parts.reduce((sum, mesh) => {
    const geometry = mesh.geometry;
    return sum + (geometry.index ? geometry.index.count / 3 : (geometry.attributes.position?.count || 0) / 3);
  }, 0);
}

function renderPartList() {
  const query = partSearch.value.trim().toLocaleLowerCase('ko');
  const shown = parts.filter((part) => part.userData.viewerLabel.toLocaleLowerCase('ko').includes(query));
  partList.replaceChildren();
  if (!shown.length) {
    const empty = document.createElement('div');
    empty.className = 'no-parts';
    empty.textContent = parts.length ? '검색 결과가 없어요.' : '모델을 열면 개별 파트가 여기에 표시돼요.';
    partList.append(empty);
  }
  for (const part of shown) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'part-row';
    row.classList.toggle('selected', part === selected);
    row.classList.toggle('hidden-part', !part.visible);
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', part === selected ? 'true' : 'false');
    row.title = part.userData.viewerLabel;
    const eye = document.createElement('span');
    eye.className = 'visibility-toggle';
    eye.textContent = part.visible ? '●' : '○';
    eye.title = part.visible ? '숨기기' : '보이기';
    eye.addEventListener('click', (event) => {
      event.stopPropagation();
      const label = part.visible ? '파트 숨김' : '파트 표시';
      recordObjectEdit([part], label, () => { part.visible = !part.visible; });
    });
    const name = document.createElement('span');
    name.className = 'part-name';
    name.textContent = part.userData.viewerLabel;
    const type = document.createElement('span');
    type.className = 'part-type';
    type.textContent = part.userData.viewerType;
    row.append(eye, name, type);
    row.addEventListener('click', () => selectPart(part));
    partList.append(row);
  }
  visibleCount.textContent = `${parts.filter((p) => p.visible).length} / ${parts.length}`;
}

function updateSelectionBox() {
  if (!selected || !selected.visible) {
    selectionBox.visible = false;
    return;
  }
  selectionBox.setFromObject(selected);
  selectionBox.visible = true;
}

function selectPart(part) {
  selected = part?.visible ? part : null;
  if (selected) {
    transform.attach(selected);
    configureTransformAxes(selected);
    selectionName.textContent = selected.userData.viewerLabel;
    updateSelectionBox();
    hostMessage({ type: 'selection', name: selected.userData.viewerLabel, partType: selected.userData.viewerType });
  } else {
    transform.detach();
    configureTransformAxes(null);
    selectionBox.visible = false;
    selectionName.textContent = '선택 없음';
  }
  renderPartList();
  window.dispatchEvent(new CustomEvent('wows-viewer-selection', {
    detail: { part: selected },
  }));
}

function setModelGhost(enabled) {
  if (!modelContent) return;
  modelContent.traverse((node) => {
    if (!node.isMesh) return;
    if (node.userData.viewerRenderOrder === undefined) {
      node.userData.viewerRenderOrder = node.renderOrder;
    }
    node.renderOrder = enabled
      ? ARMOR_GHOST_RENDER_ORDER
      : node.userData.viewerRenderOrder;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    materials.filter(Boolean).forEach((material) => {
      if (material.userData.viewerOpacity === undefined) {
        material.userData.viewerOpacity = material.opacity;
        material.userData.viewerTransparent = material.transparent;
        material.userData.viewerDepthWrite = material.depthWrite;
      }
      material.opacity = enabled ? 0.13 : material.userData.viewerOpacity;
      material.transparent = enabled ? true : material.userData.viewerTransparent;
      material.depthWrite = enabled ? false : material.userData.viewerDepthWrite;
      material.needsUpdate = true;
    });
  });
}

function setDisplayMode(mode) {
  if (mode === 'armor' && !armorData) mode = 'model';
  displayMode = mode;
  const armorMode = mode === 'armor';
  modelModeButton.classList.toggle('active', !armorMode);
  armorModeButton.classList.toggle('active', armorMode);
  modelPanel.hidden = armorMode;
  armorPanel.hidden = !armorMode;
  inspectorTitle.textContent = armorMode ? '장갑 분석' : '파트 목록';
  if (armorRoot) armorRoot.visible = armorMode;
  setModelGhost(armorMode);
  if (armorMode) selectPart(null);
  if (shipRoot) frameModel();
}

function applyArmorFilters() {
  let visibleTriangles = 0;
  for (const mesh of armorMeshes) {
    const matchesBucket = activeArmorBucket === null || mesh.userData.armorBucket === activeArmorBucket;
    const matchesZone = activeArmorZone === null || mesh.userData.armorZone === activeArmorZone;
    mesh.visible = matchesBucket && matchesZone;
    if (mesh.visible) visibleTriangles += mesh.userData.triangleCount;
  }
  thicknessFilters.querySelectorAll('.armor-filter').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.bucket) === activeArmorBucket);
  });
  zoneFilters.querySelectorAll('.armor-filter').forEach((button) => {
    button.classList.toggle('active', button.dataset.zone === activeArmorZone);
  });
  if (armorData) {
    armorSummary.textContent = `표시 ${visibleTriangles.toLocaleString()} / 전체 ${armorData.triangle_count.toLocaleString()} 삼각형 · ${armorData.zones.length}개 구역`;
  }
}

function safeArmorColor(value) {
  const text = String(value || '');
  return /^#[0-9a-f]{6}$/i.test(text) ? text : '#65a8ed';
}

function appendArmorFilterContents(button, color, label, count) {
  const swatch = document.createElement('span');
  swatch.className = 'armor-swatch';
  swatch.style.color = color;
  swatch.style.background = color;
  const name = document.createElement('span');
  name.textContent = label;
  const total = document.createElement('small');
  total.textContent = count.toLocaleString();
  button.append(swatch, name, total);
}

function legacyArmorPositionsToViewer(positions) {
  const converted = [];
  for (let triangle = 0; triangle < positions.length; triangle += 9) {
    const points = [];
    for (let vertex = 0; vertex < 3; vertex += 1) {
      const offset = triangle + vertex * 3;
      points.push([
        positions[offset],
        positions[offset + 2],
        positions[offset + 1],
      ]);
    }
    // Swapping Y/Z changes handedness, so reverse the last two vertices to
    // preserve the original face direction and raycast normal.
    for (const point of [points[0], points[2], points[1]]) converted.push(...point);
  }
  return converted;
}

function normalizeArmorData(raw) {
  if (!raw || ![
    'wows-toolbox-armor-viewer/v1',
    'wows-toolbox-armor-viewer/v2',
    'wows-toolbox-armor-viewer/v3',
  ].includes(raw.schema)) {
    throw new Error('지원하지 않는 장갑 데이터 형식이에요.');
  }
  if (!Array.isArray(raw.buckets) || !Array.isArray(raw.groups)) {
    throw new Error('장갑 데이터에 두께 또는 구역 배열이 없어요.');
  }
  if (raw.buckets.length > 512 || raw.groups.length > 8192) {
    throw new Error('장갑 데이터의 그룹 수가 안전 한도를 넘었어요.');
  }
  const buckets = [];
  const bucketIds = new Set();
  for (const item of raw.buckets) {
    const id = Number(item?.id);
    if (!Number.isSafeInteger(id) || bucketIds.has(id)) continue;
    bucketIds.add(id);
    buckets.push({
      id,
      label: String(item?.label || '두께 미상').slice(0, 80),
      color: safeArmorColor(item?.color),
      min_mm: Number.isFinite(Number(item?.min_mm)) ? Number(item.min_mm) : 0,
      max_mm: Number.isFinite(Number(item?.max_mm)) ? Number(item.max_mm) : 0,
      thickness_mm: Number.isFinite(Number(item?.thickness_mm))
        ? Number(item.thickness_mm)
        : null,
      exact: item?.exact === true,
    });
  }
  if (!buckets.length) throw new Error('유효한 장갑 두께 구간이 없어요.');
  const groups = [];
  const zones = new Set();
  let totalFloats = 0;
  let triangleCount = 0;
  for (const item of raw.groups) {
    const bucket = Number(item?.bucket);
    const positions = item?.positions;
    if (!bucketIds.has(bucket) || !Array.isArray(positions) || !positions.length) continue;
    if (positions.length % 9 !== 0 || !positions.every(Number.isFinite)) {
      throw new Error('장갑 메시 좌표 형식이 잘못됐어요.');
    }
    totalFloats += positions.length;
    if (totalFloats > 60000000) throw new Error('장갑 메시가 안전 크기 한도를 넘었어요.');
    const zone = String(item?.zone || '미분류').slice(0, 80);
    const triangles = positions.length / 9;
    const viewerSpace = (
      raw.schema === 'wows-toolbox-armor-viewer/v3'
      && raw.coordinate_system?.space === 'viewer'
      && raw.coordinate_system?.axis_up === 'Y'
      && raw.coordinate_system?.axis_forward === '-Z'
    );
    const normalizedPositions = viewerSpace
      ? positions
      : legacyArmorPositionsToViewer(positions);
    zones.add(zone);
    triangleCount += triangles;
    groups.push({
      bucket,
      positions: normalizedPositions,
      zone,
      triangle_count: triangles,
      thickness_mm: Number.isFinite(Number(item?.thickness_mm))
        ? Number(item.thickness_mm)
        : null,
      layers_mm: Array.isArray(item?.layers_mm)
        ? item.layers_mm.map(Number).filter(Number.isFinite)
        : [],
      material_id: Number.isFinite(Number(item?.material_id))
        ? Number(item.material_id)
        : null,
      material_name: String(item?.material_name || '').slice(0, 120),
      exact: raw.exact_thickness === true && Number(item?.thickness_mm) > 0,
    });
  }
  if (!groups.length) throw new Error('표시할 수 있는 장갑 메시가 없어요.');
  return {
    schema: raw.schema,
    buckets,
    groups,
    zones: [...zones].sort((a, b) => a.localeCompare(b, 'ko')),
    triangle_count: triangleCount,
    exact_thickness: raw.exact_thickness === true,
    coordinate_system: raw.coordinate_system || null,
  };
}

function renderArmorFilters() {
  thicknessFilters.replaceChildren();
  zoneFilters.replaceChildren();
  const bucketCounts = new Map();
  const zoneCounts = new Map();
  for (const group of armorData.groups) {
    bucketCounts.set(group.bucket, (bucketCounts.get(group.bucket) || 0) + group.triangle_count);
    zoneCounts.set(group.zone, (zoneCounts.get(group.zone) || 0) + group.triangle_count);
  }
  for (const bucket of armorData.buckets) {
    if (!bucketCounts.has(bucket.id)) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'armor-filter';
    button.dataset.bucket = String(bucket.id);
    appendArmorFilterContents(button, bucket.color, bucket.label, bucketCounts.get(bucket.id));
    button.addEventListener('click', () => {
      activeArmorBucket = activeArmorBucket === bucket.id ? null : bucket.id;
      applyArmorFilters();
    });
    thicknessFilters.append(button);
  }
  for (const zone of armorData.zones) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'armor-filter';
    button.dataset.zone = zone;
    appendArmorFilterContents(button, '#65a8ed', zone, zoneCounts.get(zone) || 0);
    button.addEventListener('click', () => {
      activeArmorZone = activeArmorZone === zone ? null : zone;
      applyArmorFilters();
    });
    zoneFilters.append(button);
  }
  applyArmorFilters();
}

async function loadArmor(url, loadId) {
  if (!url) return false;
  const response = await fetch(url, { cache: 'no-store' });
  if (loadId !== modelLoadSerial) return false;
  if (!response.ok) throw new Error(`장갑 데이터 HTTP ${response.status}`);
  const raw = await response.json();
  if (loadId !== modelLoadSerial) return false;
  const data = normalizeArmorData(raw);
  const nextArmorRoot = new THREE.Group();
  const nextArmorMeshes = [];
  nextArmorRoot.name = 'ARMOR_ROOT';
  nextArmorRoot.visible = false;
  const bucketMap = new Map(data.buckets.map((bucket) => [bucket.id, bucket]));
  for (const [groupIndex, group] of data.groups.entries()) {
    const bucket = bucketMap.get(group.bucket);
    if (!bucket) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(group.positions, 3));
    geometry.computeVertexNormals();
    const material = new THREE.MeshBasicMaterial({
      color: bucket.color,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: Number(armorOpacity.value) / 100,
      depthTest: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -1,
    });
    // Transparent double-sided armor normally participates in camera-distance
    // sorting and may be drawn in two passes. Keep both the model/armor layers
    // and armor groups in a deterministic order so orbiting the camera does not
    // change their blend order or apparent color.
    material.forceSinglePass = true;
    material.userData.stableArmorTransparency = true;
    const mesh = new THREE.Mesh(geometry, material);
    mesh.renderOrder = ARMOR_RENDER_ORDER_BASE + groupIndex;
    mesh.name = `Armor_${group.zone}_${bucket.label}`;
    mesh.userData.armorZone = group.zone;
    mesh.userData.armorBucket = group.bucket;
    mesh.userData.triangleCount = group.triangle_count;
    mesh.userData.armorMeta = {
      ...bucket,
      thickness_mm: group.thickness_mm ?? bucket.thickness_mm,
      layers_mm: group.layers_mm,
      material_id: group.material_id,
      material_name: group.material_name,
      exact: group.exact || bucket.exact,
    };
    nextArmorMeshes.push(mesh);
    nextArmorRoot.add(mesh);
  }
  if (loadId !== modelLoadSerial) {
    disposeObject3D(nextArmorRoot);
    return false;
  }
  if (!nextArmorMeshes.length) {
    disposeObject3D(nextArmorRoot);
    return false;
  }
  armorData = data;
  armorRoot = nextArmorRoot;
  armorMeshes = nextArmorMeshes;
  shipRoot.add(armorRoot);
  armorModeButton.disabled = false;
  renderArmorFilters();
  return true;
}

function frameModel(direction = new THREE.Vector3(1.25, 0.75, 1.4), topView = false) {
  if (window.WoWSViewerAdvanced?.frameComparison?.(direction, topView)) return;
  if (!shipRoot) return;
  const box = new THREE.Box3().setFromObject(shipRoot);
  const center = box.getCenter(new THREE.Vector3());
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  modelRadius = Math.max(sphere.radius, 0.01);
  const distance = modelRadius / Math.sin(THREE.MathUtils.degToRad(camera.fov * 0.5)) * 1.12;
  const normalized = direction.clone().normalize();
  camera.position.copy(center).addScaledVector(normalized, distance);
  camera.up.set(0, 1, 0);
  if (topView) camera.up.set(0, 0, -1);
  camera.near = Math.max(modelRadius / 100, 0.005);
  camera.far = Math.max(modelRadius * 25, 100);
  camera.updateProjectionMatrix();
  orbit.target.copy(center);
  orbit.minDistance = modelRadius * 0.04;
  orbit.maxDistance = modelRadius * 30;
  orbit.update();
  grid.position.y = box.min.y - Math.max(modelRadius * 0.015, 0.01);
  grid.scale.setScalar(Math.max(modelRadius / 25, 0.1));
}

function setView(view) {
  document.querySelectorAll('[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  if (!shipRoot) return;
  if (view === 'front') frameModel(new THREE.Vector3(0, 0, 1));
  else if (view === 'side') frameModel(new THREE.Vector3(1, 0, 0));
  else if (view === 'top') frameModel(new THREE.Vector3(0, 1, 0.001), true);
  else frameModel();
}

function ensureTrustedModelUrl(value, field) {
  if (!value) return;
  const url = new URL(value, window.location.href);
  if (url.protocol !== 'https:' || url.hostname !== 'model.local') {
    throw new Error(`${field} 주소가 허용된 모델 폴더를 벗어났어요.`);
  }
}

const RESOURCE_RETRY_LIMIT = 3;

function isTransientResourceError(error) {
  const message = String(error?.message || error || '');
  return /failed to fetch|networkerror|network error|err_failed|load failed/i.test(message);
}

function retryUrl(value, attempt) {
  if (!attempt) return value;
  const url = new URL(value, window.location.href);
  url.searchParams.set('_viewer_retry', String(attempt));
  return url.href;
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function loadResourceWithRetry(loader, url, isCurrent, label, onRetry = null) {
  let lastError = null;
  for (let attempt = 0; attempt < RESOURCE_RETRY_LIMIT; attempt += 1) {
    if (!isCurrent()) throw new Error(`${label} 불러오기가 취소됐어요.`);
    try {
      return await loader.loadAsync(retryUrl(url, attempt));
    } catch (error) {
      lastError = error;
      if (!isCurrent() || !isTransientResourceError(error) || attempt + 1 >= RESOURCE_RETRY_LIMIT) throw error;
      onRetry?.(attempt + 1, RESOURCE_RETRY_LIMIT);
      await delay(140 * (attempt + 1));
    }
  }
  throw lastError || new Error(`${label} 불러오기에 실패했어요.`);
}

function orientObjForViewer(target, geometryRoot = target, modelMetadata = null) {
  target.rotation.x = 0;
  geometryRoot.updateMatrixWorld(true);
  const size = new THREE.Box3().setFromObject(geometryRoot).getSize(new THREE.Vector3());
  const declaredUp = String(modelMetadata?.obj_axis_up || '').trim().toUpperCase();
  let legacyZUp;
  let mode;
  if (declaredUp === 'Y') {
    legacyZUp = false;
    mode = 'metadata-y-up';
  } else if (declaredUp === 'Z') {
    legacyZUp = true;
    mode = 'metadata-z-up';
  } else {
    legacyZUp = size.y > Math.max(size.z * 1.25, 0.0001);
    mode = legacyZUp ? 'heuristic-z-up' : 'heuristic-y-up';
  }
  target.rotation.x = legacyZUp ? -Math.PI / 2 : 0;
  target.updateMatrixWorld(true);
  return {
    mode,
    sourceSize: { x: size.x, y: size.y, z: size.z },
  };
}

async function loadShip(message) {
  const loadId = ++modelLoadSerial;
  clearModel(false);
  emptyState.classList.add('hidden');
  showLoading('모델 읽는 중', '재질과 OBJ 파트를 준비하고 있어요.');
  setStatus('모델 로딩 중');
  modelName.textContent = message.displayName || message.objName || '함선 모델';
  const manager = new THREE.LoadingManager();
  manager.onProgress = (_, loaded, total) => {
    if (loadId === modelLoadSerial) {
      loadingDetail.textContent = `리소스 ${loaded} / ${Math.max(total, loaded)} 불러오는 중`;
    }
  };
  manager.onError = (url) => {
    if (loadId === modelLoadSerial) console.warn('Resource load failed', url);
  };
  let materials = null;
  let root = null;
  try {
    for (const [field, value] of [
      ['OBJ', message.objUrl],
      ['MTL', message.mtlUrl],
      ['리소스', message.resourceBaseUrl],
      ['파트 원점', message.modelReportUrl],
      ['조립 방향', message.assemblyReportUrl],
      ['장갑', message.armorUrl],
    ]) ensureTrustedModelUrl(value, field);
    const objLoader = new OBJLoader(manager);
    if (message.mtlUrl) {
      try {
        const mtlLoader = new MTLLoader(manager);
        if (message.resourceBaseUrl) mtlLoader.setResourcePath(message.resourceBaseUrl);
        materials = await loadResourceWithRetry(
          mtlLoader,
          message.mtlUrl,
          () => loadId === modelLoadSerial,
          'MTL',
          (attempt, limit) => { loadingDetail.textContent = `MTL 연결 재시도 ${attempt} / ${limit - 1}`; },
        );
        if (loadId !== modelLoadSerial) {
          disposeMaterialCreator(materials);
          return;
        }
        materials.preload();
        objLoader.setMaterials(materials);
      } catch (materialError) {
        if (loadId !== modelLoadSerial) return;
        console.warn('MTL load failed; OBJ will use fallback materials.', materialError);
        hostMessage({ type: 'warning', message: `MTL 불러오기 실패: ${materialError.message || materialError}` });
      }
    }
    try {
      root = await loadResourceWithRetry(
        objLoader,
        message.objUrl,
        () => loadId === modelLoadSerial,
        'OBJ',
        (attempt, limit) => { loadingDetail.textContent = `OBJ 연결 재시도 ${attempt} / ${limit - 1}`; },
      );
    } catch (objError) {
      throw new Error(`OBJ 불러오기 실패 (${message.objUrl}): ${objError.message || objError}`);
    }
    if (loadId !== modelLoadSerial) {
      disposeObject3D(root);
      return;
    }
    let assemblyMetadata = null;
    if (message.assemblyReportUrl) {
      try {
        assemblyMetadata = await loadAssemblyMetadata(message.assemblyReportUrl);
        if (loadId !== modelLoadSerial) {
          disposeObject3D(root);
          return;
        }
      } catch (assemblyError) {
        if (loadId !== modelLoadSerial) {
          disposeObject3D(root);
          return;
        }
        console.warn('Assembly metadata load failed.', assemblyError);
        hostMessage({ type: 'warning', message: `조립 방향 데이터 불러오기 실패: ${assemblyError.message || assemblyError}` });
      }
    }
    normalizeModelMaterials(root, assemblyMetadata);
    let modelMetadata = null;
    if (message.modelReportUrl) {
      try {
        modelMetadata = await loadModelMetadata(message.modelReportUrl);
        if (loadId !== modelLoadSerial) {
          disposeObject3D(root);
          return;
        }
      } catch (metadataError) {
        if (loadId !== modelLoadSerial) {
          disposeObject3D(root);
          return;
        }
        console.warn('Model metadata load failed.', metadataError);
        hostMessage({ type: 'warning', message: `파트 원점 데이터 불러오기 실패: ${metadataError.message || metadataError}` });
      }
    }
    if (loadId !== modelLoadSerial) {
      disposeObject3D(root);
      return;
    }
    const pivotCount = applyModelMetadata(root, modelMetadata);
    shipRoot = new THREE.Group();
    shipRoot.name = String(message.displayName || 'SHIP_ROOT').slice(0, 160);
    modelContent = new THREE.Group();
    modelContent.name = 'MODEL_ROOT';
    modelContent.add(root);
    const axisOrientation = orientObjForViewer(modelContent, root, modelMetadata);
    shipRoot.add(modelContent);
    scene.add(shipRoot);
    let armorLoaded = false;
    if (message.armorUrl) {
      try {
        loadingDetail.textContent = '장갑 구역과 두께 데이터를 준비하는 중';
        armorLoaded = await loadArmor(message.armorUrl, loadId);
      } catch (armorError) {
        if (loadId !== modelLoadSerial) return;
        console.warn('Armor load failed.', armorError);
        hostMessage({ type: 'warning', message: `장갑 데이터 불러오기 실패: ${armorError.message || armorError}` });
      }
    }
    if (loadId !== modelLoadSerial) return;
    shipRoot.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(modelContent);
    const center = box.getCenter(new THREE.Vector3());
    modelContent.position.sub(center);
    if (armorRoot) armorRoot.position.sub(center);
    shipRoot.updateMatrixWorld(true);
    prepareParts();
    setDisplayMode('model');
    frameModel();
    const triangles = Math.round(triangleCount());
    const armorText = armorLoaded ? ` · 장갑 ${armorData.triangle_count.toLocaleString()}` : '';
    meshStats.textContent = `파트 ${parts.length.toLocaleString()} · 삼각형 ${triangles.toLocaleString()}${armorText}`;
    setStatus(armorLoaded ? '모델·장갑 준비 완료' : '모델 준비 완료 · 장갑 데이터 없음');
    hideLoading();
    hostMessage({
      type: 'loaded',
      parts: parts.length,
      triangles,
      armor: armorLoaded,
      armorTriangles: armorLoaded ? armorData.triangle_count : 0,
      armorGroups: armorLoaded ? armorData.groups.length : 0,
      armorZones: armorLoaded ? armorData.zones.length : 0,
      name: modelName.textContent,
      pivotParts: pivotCount,
      axisMode: axisOrientation.mode,
    });
  } catch (error) {
    if (loadId !== modelLoadSerial) {
      if (root && !root.parent) disposeObject3D(root);
      return;
    }
    console.error(error);
    hideLoading();
    emptyState.classList.remove('hidden');
    setStatus(`불러오기 실패: ${error.message || error}`, true);
    hostMessage({ type: 'error', message: String(error.message || error) });
  }
}

canvas.addEventListener('pointerdown', (event) => { pointerDown = { x: event.clientX, y: event.clientY }; });
canvas.addEventListener('pointerup', (event) => {
  if (!shipRoot || displayMode !== 'model' || !pointerDown || transform.dragging) return;
  const distance = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y);
  pointerDown = null;
  if (distance > 4 || event.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(parts.filter((part) => part.visible), false)[0];
  selectPart(hit?.object || null);
});

modelModeButton.addEventListener('click', () => setDisplayMode('model'));
armorModeButton.addEventListener('click', () => setDisplayMode('armor'));
document.querySelector('#allThickness').addEventListener('click', () => { activeArmorBucket = null; applyArmorFilters(); });
document.querySelector('#allZones').addEventListener('click', () => { activeArmorZone = null; applyArmorFilters(); });
armorOpacity.addEventListener('input', () => {
  const opacity = Number(armorOpacity.value) / 100;
  for (const mesh of armorMeshes) mesh.material.opacity = opacity;
});
partSearch.addEventListener('input', renderPartList);
document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
backgroundButton.addEventListener('click', () => setBackgroundVisible(!backgroundVisible, { announce: true }));
document.querySelector('#gridButton').addEventListener('click', (event) => { grid.visible = !grid.visible; event.currentTarget.classList.toggle('active', grid.visible); });
document.querySelector('#wireButton').addEventListener('click', (event) => {
  wireframe = !wireframe;
  for (const part of parts) {
    const materials = Array.isArray(part.material) ? part.material : [part.material];
    materials.filter(Boolean).forEach((material) => { material.wireframe = wireframe; material.needsUpdate = true; });
  }
  event.currentTarget.classList.toggle('active', wireframe);
});
document.querySelector('#captureButton').addEventListener('click', () => {
  renderer.render(scene, camera);
  const anchor = document.createElement('a');
  anchor.download = `${(modelName.textContent || 'ship').replace(/[\\/:*?"<>|]+/g, '_')}.png`;
  anchor.href = renderer.domElement.toDataURL('image/png');
  anchor.click();
});
function setTransformMode(mode) {
  if (mode === 'rotate' && selected) ensurePartPivot(selected);
  transform.setMode(mode);
  configureTransformAxes(selected);
  document.querySelector('#moveButton').classList.toggle('active', mode === 'translate');
  document.querySelector('#rotateButton').classList.toggle('active', mode === 'rotate');
}

function hideSelectedPart() {
  if (!selected) return false;
  return recordObjectEdit([selected], '파트 숨김', () => { selected.visible = false; });
}

function isTextEditingTarget(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

document.querySelector('#moveButton').addEventListener('click', () => setTransformMode('translate'));
document.querySelector('#rotateButton').addEventListener('click', () => setTransformMode('rotate'));
document.querySelector('#hideButton').addEventListener('click', hideSelectedPart);

window.addEventListener('keydown', (event) => {
  if (isTextEditingTarget(event.target)) return;
  const accelerator = event.ctrlKey || event.metaKey;
  const key = event.key.toLocaleLowerCase('en');
  if (accelerator && !event.altKey && key === 'z') {
    event.preventDefault();
    event.stopPropagation();
    if (event.shiftKey) redoViewerEdit();
    else undoViewerEdit();
    return;
  }
  if (accelerator && !event.altKey && key === 'y') {
    event.preventDefault();
    event.stopPropagation();
    redoViewerEdit();
    return;
  }
  if (accelerator || event.altKey) return;
  if (event.code === 'KeyG') {
    event.preventDefault();
    setTransformMode('translate');
    setStatus('파트 이동 도구');
  } else if (event.code === 'KeyR') {
    event.preventDefault();
    setTransformMode('rotate');
    setStatus('파트 회전 도구');
  } else if (event.code === 'KeyB') {
    event.preventDefault();
    setBackgroundVisible(!backgroundVisible, { announce: true });
  } else if (event.code === 'Delete') {
    event.preventDefault();
    hideSelectedPart();
  } else if (event.code === 'Home') {
    event.preventDefault();
    frameModel();
  } else if (event.code === 'Escape') {
    event.preventDefault();
    selectPart(null);
  }
}, true);
document.querySelector('#isolateButton').addEventListener('click', (event) => {
  if (!selected) return;
  isolated = !isolated;
  for (const part of parts) part.visible = isolated ? part === selected : true;
  event.currentTarget.classList.toggle('active', isolated);
  event.currentTarget.textContent = isolated ? '전체 보기' : '단독 보기';
  renderPartList();
  updateSelectionBox();
});

window.chrome?.webview?.addEventListener('message', (event) => {
  const message = event.data;
  if (!message || typeof message !== 'object') return;
  if (message.type === 'loadModel') loadShip(message);
  else if (message.type === 'setView') setView(message.view || 'perspective');
  else if (message.type === 'clear') { clearModel(); emptyState.classList.remove('hidden'); modelName.textContent = '모델을 열어 주세요'; }
});

function resize() {
  const width = viewportShell.clientWidth;
  const height = viewportShell.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewportShell);

function animate() {
  orbit.update();
  if (selected) updateSelectionBox();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
resize();
animate();
setBackgroundVisible(readBackgroundVisibility(), { persist: false });
ready = true;
setStatus('뷰어 준비됨');
window.WoWSViewerCore = {
  THREE,
  renderer,
  scene,
  camera,
  canvas,
  viewportShell,
  orbit,
  transform,
  raycaster,
  pointer,
  getParts: () => parts,
  getArmorMeshes: () => armorMeshes,
  getArmorData: () => armorData,
  getModelContent: () => modelContent,
  getShipRoot: () => shipRoot,
  getDisplayMode: () => displayMode,
  getModelRadius: () => modelRadius,
  getBackgroundVisible: () => backgroundVisible,
  getSelected: () => selected,
  selectPart,
  renderPartList,
  frameModel,
  orientObjForViewer,
  normalizeModelMaterials,
  loadAssemblyMetadata,
  loadModelMetadata,
  applyModelMetadata,
  recordObjectEdit,
  undoViewerEdit,
  redoViewerEdit,
  ensurePartPivot,
  isWeaponPart: isViewerWeaponPart,
  getPartUpAxisName,
  setPartTraverseDegrees,
  rotatePartAroundUpAxis,
  loadResourceWithRetry,
  setStatus,
  hostMessage,
};
hostMessage({ type: 'ready', version: '5.0.30' });
