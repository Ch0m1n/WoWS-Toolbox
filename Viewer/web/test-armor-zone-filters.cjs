const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/viewer.js', 'utf8');
class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.listeners = {}; this.classes = new Set(); this.classList = {toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name)}; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  addEventListener(name, action) { this.listeners[name] = action; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector() { return this.children[0]; }
  querySelectorAll() { return this.children; }
  click() { this.listeners.click(); }
}
const zoneFilters = new Element(), thicknessFilters = new Element();
const all = new Element(), none = new Element(), allThickness = new Element(), noThickness = new Element();
const groups = [
  {zone:'Bow', bucket:0, triangle_count:10},
  {zone:'Citadel', bucket:0, triangle_count:20},
  {zone:'Citadel', bucket:1, triangle_count:30},
  {zone:'Stern', bucket:1, triangle_count:40},
];
const context = {
  hiddenArmorZones:new Set(), hiddenArmorBuckets:new Set(),
  armorData:{groups, zones:['Bow','Citadel','Stern'], triangle_count:100, buckets:[{id:0,color:'#fff',label:'thin'},{id:1,color:'#fff',label:'thick'}]},
  armorMeshes:groups.map(group => ({visible:true,userData:{armorZone:group.zone,armorBucket:group.bucket,triangleCount:group.triangle_count}})),
  zoneFilters, thicknessFilters, armorSummary:new Element(),
  document:{createElement:()=>new Element(),querySelector:id=>({'#allZones':all,'#noZones':none,'#allThickness':allThickness,'#noThickness':noThickness}[id])},
};
vm.createContext(context);
for (const [name,next] of [['appendArmorFilterContents','function legacyArmorPositionsToViewer'],['applyArmorFilters','function safeArmorColor'],['renderArmorFilters','async function loadArmor']]) {
  const start=source.indexOf('function '+name+'('), end=source.indexOf(next,start);
  assert(start >= 0 && end > start);
  vm.runInContext(source.slice(start,end),context);
}
const events=source.slice(source.indexOf("document.querySelector('#allThickness').addEventListener"),source.indexOf("armorOpacity.addEventListener('input'"));
vm.runInContext(events,context);
context.renderArmorFilters();
const visible=()=>context.armorMeshes.filter(mesh=>mesh.visible).map(mesh=>mesh.userData.armorZone);
const button=zone=>zoneFilters.children.find(item=>item.dataset.zone === zone);
assert.deepEqual(visible(),['Bow','Citadel','Citadel','Stern']);
assert(zoneFilters.children.every(item=>item.attributes['aria-pressed'] === 'true'));
button('Citadel').click();
assert.deepEqual(visible(),['Bow','Stern']);
assert.equal(button('Citadel').attributes['aria-pressed'],'false');
assert.equal(button('Citadel').children[0].textContent,'');
none.click();
assert.deepEqual(visible(),[]);
button('Bow').click(); button('Stern').click();
assert.deepEqual(visible(),['Bow','Stern']);
context.hiddenArmorBuckets.add(0); context.applyArmorFilters();
assert.deepEqual(visible(),['Stern']);
all.click();
assert.deepEqual(visible(),['Citadel','Stern']);
assert(zoneFilters.children.every(item=>item.attributes['aria-pressed'] === 'true'));
context.hiddenArmorBuckets.clear(); context.applyArmorFilters();
assert.deepEqual(visible(),['Bow','Citadel','Citadel','Stern']);
console.log('Armor zones: independent toggles, multiple selection, show/hide all, thickness intersection and accessibility state passed');

// Three distinct thicknesses exercise a proper subset containing two buckets.
context.armorData.buckets.push({id:2,color:'#fff',label:'extra'});
context.armorData.groups.push({zone:'Bow',bucket:2,triangle_count:50});
context.armorData.triangle_count+=50;
context.armorMeshes.push({visible:true,userData:{armorZone:'Bow',armorBucket:2,triangleCount:50}});
context.renderArmorFilters();
const thickness=id=>thicknessFilters.children.find(b=>Number(b.dataset.bucket)===id);
const buckets=()=>context.armorMeshes.filter(m=>m.visible).map(m=>m.userData.armorBucket);
assert(thicknessFilters.children.every(b=>b.attributes['aria-pressed']==='true'));
thickness(1).click();
assert.deepEqual(buckets(),[0,0,2]);
assert.equal(thickness(1).attributes['aria-pressed'],'false');
assert.equal(thickness(1).children[0].textContent,'');
noThickness.click();
assert.deepEqual(buckets(),[]);
thickness(0).click(); thickness(2).click();
assert.deepEqual(buckets(),[0,0,2]);
button('Citadel').click();
assert.deepEqual(buckets(),[0,2]);
allThickness.click();
assert.deepEqual(buckets(),[0,1,2]);
all.click();
assert.equal(context.armorMeshes.filter(m=>m.visible).length,5);
console.log('Thicknesses: multi-select subset, independent toggles, all/none, zone intersection and aria state passed');
