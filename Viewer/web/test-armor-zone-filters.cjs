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
const all = new Element(), none = new Element();
const groups = [
  {zone:'Bow', bucket:0, triangle_count:10},
  {zone:'Citadel', bucket:0, triangle_count:20},
  {zone:'Citadel', bucket:1, triangle_count:30},
  {zone:'Stern', bucket:1, triangle_count:40},
];
const context = {
  hiddenArmorZones:new Set(), activeArmorBucket:null,
  armorData:{groups, zones:['Bow','Citadel','Stern'], triangle_count:100, buckets:[{id:0,color:'#fff',label:'thin'},{id:1,color:'#fff',label:'thick'}]},
  armorMeshes:groups.map(group => ({visible:true,userData:{armorZone:group.zone,armorBucket:group.bucket,triangleCount:group.triangle_count}})),
  zoneFilters, thicknessFilters, armorSummary:new Element(),
  document:{createElement:()=>new Element(),querySelector:id=>id === '#allZones' ? all : none},
};
vm.createContext(context);
for (const [name,next] of [['appendArmorFilterContents','function legacyArmorPositionsToViewer'],['applyArmorFilters','function safeArmorColor'],['renderArmorFilters','async function loadArmor']]) {
  const start=source.indexOf('function '+name+'('), end=source.indexOf(next,start);
  assert(start >= 0 && end > start);
  vm.runInContext(source.slice(start,end),context);
}
const events=source.slice(source.indexOf("document.querySelector('#allZones').addEventListener"),source.indexOf("armorOpacity.addEventListener('input'"));
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
context.activeArmorBucket=1; context.applyArmorFilters();
assert.deepEqual(visible(),['Stern']);
all.click();
assert.deepEqual(visible(),['Citadel','Stern']);
assert(zoneFilters.children.every(item=>item.attributes['aria-pressed'] === 'true'));
context.activeArmorBucket=null; context.applyArmorFilters();
assert.deepEqual(visible(),['Bow','Citadel','Citadel','Stern']);
console.log('Armor zones: independent toggles, multiple selection, show/hide all, thickness intersection and accessibility state passed');
