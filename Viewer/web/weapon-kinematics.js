import * as THREE from './vendor/three.module.js';

const WEAPON_CATEGORIES = new Set([
  'main_gun', 'secondary', 'anti_air', 'torpedo', 'missile_launcher',
]);

const WEAPON_LABELS = new Set(['주함포', '부포', '대공포', '어뢰', '미사일']);

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function isWeaponPart(part) {
  if (!part?.isMesh) return false;
  return WEAPON_CATEGORIES.has(part.userData.viewerCategory)
    || WEAPON_LABELS.has(part.userData.viewerType);
}

export function partUpAxisName(part) {
  return part?.userData?.viewerUpAxis === 'z' ? 'z' : 'y';
}

function componentCandidates(geometry, upAxisName, category) {
  const position = geometry?.attributes?.position;
  if (!position || position.count < 12) return { candidates: [], reason: '정점 데이터가 부족해요' };

  const count = position.count;
  const parent = new Int32Array(count);
  const rank = new Uint8Array(count);
  for (let index = 0; index < count; index += 1) parent[index] = index;

  const find = (value) => {
    let root = value;
    while (parent[root] !== root) root = parent[root];
    while (parent[value] !== value) {
      const next = parent[value];
      parent[value] = root;
      value = next;
    }
    return root;
  };
  const union = (left, right) => {
    let a = find(left);
    let b = find(right);
    if (a === b) return;
    if (rank[a] < rank[b]) [a, b] = [b, a];
    parent[b] = a;
    if (rank[a] === rank[b]) rank[a] += 1;
  };

  // OBJ files often duplicate vertices along normals and UV seams. Welding only
  // equal positions restores the actual disconnected mechanical components.
  const welded = new Map();
  for (let index = 0; index < count; index += 1) {
    const key = `${Math.round(position.getX(index) * 100000)},${Math.round(position.getY(index) * 100000)},${Math.round(position.getZ(index) * 100000)}`;
    const previous = welded.get(key);
    if (previous === undefined) welded.set(key, index);
    else union(previous, index);
  }

  const indices = geometry.index?.array || null;
  const triangleValues = indices?.length || count;
  for (let offset = 0; offset + 2 < triangleValues; offset += 3) {
    const a = indices ? Number(indices[offset]) : offset;
    const b = indices ? Number(indices[offset + 1]) : offset + 1;
    const c = indices ? Number(indices[offset + 2]) : offset + 2;
    if (a < count && b < count && c < count) {
      union(a, b);
      union(a, c);
    }
  }

  const components = new Map();
  const overallMin = [Infinity, Infinity, Infinity];
  const overallMax = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < count; index += 1) {
    const point = [position.getX(index), position.getY(index), position.getZ(index)];
    for (let axis = 0; axis < 3; axis += 1) {
      overallMin[axis] = Math.min(overallMin[axis], point[axis]);
      overallMax[axis] = Math.max(overallMax[axis], point[axis]);
    }
    const root = find(index);
    let component = components.get(root);
    if (!component) {
      component = {
        indices: [],
        min: [Infinity, Infinity, Infinity],
        max: [-Infinity, -Infinity, -Infinity],
      };
      components.set(root, component);
    }
    component.indices.push(index);
    for (let axis = 0; axis < 3; axis += 1) {
      component.min[axis] = Math.min(component.min[axis], point[axis]);
      component.max[axis] = Math.max(component.max[axis], point[axis]);
    }
  }

  const upAxis = upAxisName === 'z' ? 2 : 1;
  const horizontalAxes = upAxis === 2 ? [0, 1] : [0, 2];
  const overallSpans = overallMax.map((value, axis) => value - overallMin[axis]);
  const overallHorizontal = Math.max(...horizontalAxes.map((axis) => overallSpans[axis]), 0.000001);
  const antiAir = category === 'anti_air';
  const minimumAspect = antiAir ? 1.35 : 2.15;
  const minimumLength = overallHorizontal * (antiAir ? 0.20 : 0.16);
  const candidates = [];

  for (const component of components.values()) {
    if (component.indices.length < 12) continue;
    const spans = component.max.map((value, axis) => value - component.min[axis]);
    const axis = spans[horizontalAxes[0]] >= spans[horizontalAxes[1]]
      ? horizontalAxes[0]
      : horizontalAxes[1];
    const crossAxis = horizontalAxes.find((value) => value !== axis);
    const length = spans[axis];
    const thickness = Math.max(spans[crossAxis], spans[upAxis], overallHorizontal * 0.003);
    const aspect = length / thickness;
    const center = component.min.map((value, coordinate) => (value + component.max[coordinate]) / 2);
    if (length < minimumLength || aspect < minimumAspect) continue;
    if (Math.abs(center[axis]) < length * 0.15) continue;
    const sign = center[axis] >= 0 ? 1 : -1;
    const reach = sign > 0 ? component.max[axis] : -component.min[axis];
    candidates.push({
      ...component,
      axis,
      crossAxis,
      upAxis,
      spans,
      center,
      sign,
      reach,
      aspect,
      length,
      score: component.indices.length * length * Math.min(aspect, 16),
    });
  }
  return { candidates, reason: candidates.length ? '' : '분리 가능한 포신 형상을 찾지 못했어요' };
}

export function inferBarrelRig(part) {
  if (!isWeaponPart(part)) return { available: false, reason: '무장 파트가 아니에요' };
  if (part.userData.viewerBarrelRig) return part.userData.viewerBarrelRig;

  const geometry = part.geometry;
  const upAxisName = partUpAxisName(part);
  const category = part.userData.viewerCategory || '';
  const analysis = componentCandidates(geometry, upAxisName, category);
  if (!analysis.candidates.length) {
    const unavailable = { available: false, reason: analysis.reason };
    part.userData.viewerBarrelRig = unavailable;
    return unavailable;
  }

  const groups = new Map();
  let totalScore = 0;
  for (const candidate of analysis.candidates) {
    const key = `${candidate.axis}:${candidate.sign}`;
    const group = groups.get(key) || { score: 0, candidates: [] };
    group.score += candidate.score;
    group.candidates.push(candidate);
    groups.set(key, group);
    totalScore += candidate.score;
  }
  const dominant = [...groups.values()].sort((left, right) => right.score - left.score)[0];
  const maximumReach = Math.max(...dominant.candidates.map((item) => item.reach));
  const maximumLength = Math.max(...dominant.candidates.map((item) => item.length));
  const barrels = dominant.candidates.filter(
    (item) => item.reach >= maximumReach * 0.76 && item.length >= maximumLength * 0.52,
  );
  const dominance = dominant.score / Math.max(totalScore, 0.000001);
  if (!barrels.length || dominance < 0.34) {
    const unavailable = { available: false, reason: '포신 방향을 확실하게 판별하지 못했어요' };
    part.userData.viewerBarrelRig = unavailable;
    return unavailable;
  }

  const axisIndex = barrels[0].axis;
  const upIndex = barrels[0].upAxis;
  const crossIndex = barrels[0].crossAxis;
  const sign = barrels[0].sign;
  const selectedIndices = [...new Set(barrels.flatMap((item) => item.indices))].sort((a, b) => a - b);
  const weight = barrels.reduce((sum, item) => sum + item.indices.length, 0);
  const weighted = (getter) => barrels.reduce(
    (sum, item) => sum + getter(item) * item.indices.length,
    0,
  ) / Math.max(weight, 1);

  const pivotValues = [0, 0, 0];
  pivotValues[axisIndex] = weighted((item) => (sign > 0 ? item.min[axisIndex] : item.max[axisIndex]));
  pivotValues[upIndex] = weighted((item) => item.center[upIndex]);
  pivotValues[crossIndex] = weighted((item) => item.center[crossIndex]);
  const directionValues = [0, 0, 0];
  directionValues[axisIndex] = sign;
  const upValues = [0, 0, 0];
  upValues[upIndex] = 1;
  const direction = new THREE.Vector3(...directionValues);
  const up = new THREE.Vector3(...upValues);
  const rotationAxis = direction.clone().cross(up).normalize();
  const position = geometry.attributes.position;
  const normal = geometry.attributes.normal;
  const rig = {
    available: true,
    axisIndex,
    upIndex,
    sign,
    pivot: new THREE.Vector3(...pivotValues),
    direction,
    rotationAxis,
    vertexIndices: Uint32Array.from(selectedIndices),
    basePositions: new Float32Array(position.array),
    baseNormals: normal ? new Float32Array(normal.array) : null,
    componentCount: barrels.length,
    confidence: Math.min(1, dominance),
  };
  part.userData.viewerBarrelRig = rig;
  part.userData.viewerBarrelDegrees = finiteNumber(part.userData.viewerBarrelDegrees);
  return rig;
}

function rotateVector(array, offset, axis, cosine, sine, pivot = null) {
  let x = array[offset];
  let y = array[offset + 1];
  let z = array[offset + 2];
  if (pivot) {
    x -= pivot.x;
    y -= pivot.y;
    z -= pivot.z;
  }
  const dot = axis.x * x + axis.y * y + axis.z * z;
  const crossX = axis.y * z - axis.z * y;
  const crossY = axis.z * x - axis.x * z;
  const crossZ = axis.x * y - axis.y * x;
  const inverse = 1 - cosine;
  return [
    x * cosine + crossX * sine + axis.x * dot * inverse + (pivot?.x || 0),
    y * cosine + crossY * sine + axis.y * dot * inverse + (pivot?.y || 0),
    z * cosine + crossZ * sine + axis.z * dot * inverse + (pivot?.z || 0),
  ];
}

export function applyBarrelElevation(part, degrees) {
  const rig = inferBarrelRig(part);
  if (!rig.available) return false;
  const angle = THREE.MathUtils.degToRad(Math.max(-15, Math.min(60, finiteNumber(degrees))));
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const position = part.geometry.attributes.position;
  position.array.set(rig.basePositions);
  const normal = part.geometry.attributes.normal;
  if (normal && rig.baseNormals) normal.array.set(rig.baseNormals);

  for (const index of rig.vertexIndices) {
    const offset = index * 3;
    const point = rotateVector(rig.basePositions, offset, rig.rotationAxis, cosine, sine, rig.pivot);
    position.array[offset] = point[0];
    position.array[offset + 1] = point[1];
    position.array[offset + 2] = point[2];
    if (normal && rig.baseNormals) {
      const vector = rotateVector(rig.baseNormals, offset, rig.rotationAxis, cosine, sine);
      normal.array[offset] = vector[0];
      normal.array[offset + 1] = vector[1];
      normal.array[offset + 2] = vector[2];
    }
  }
  position.needsUpdate = true;
  if (normal) normal.needsUpdate = true;
  part.geometry.computeBoundingBox();
  part.geometry.computeBoundingSphere();
  part.userData.viewerBarrelDegrees = THREE.MathUtils.radToDeg(angle);
  part.userData.viewerApplyBarrelElevation = (value) => applyBarrelElevation(part, value);
  return true;
}
