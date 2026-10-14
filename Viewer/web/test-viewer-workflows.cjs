const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = [];
class Element {
  constructor(tag) { this.tag = tag; this.style = {}; this.listeners = {}; this.value = ''; elements.push(this); }
  append(...nodes) { (this.children ||= []).push(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const vector = () => ({value: [1,2,3], toArray() {return this.value;}, fromArray(v) {this.value = [...v];}});
const part = {name:'Hull',userData:{},visible:true,geometry:{attributes:{position:{count:6}}},material:{name:'hull'}};
let listener, sent, status;
const events = {};
const core = {viewportShell:new Element('div'), getParts:()=>[part],getShipRoot:()=>null,getCompareRoot:()=>null,
 camera:{position:vector(),up:vector()},orbit:{target:vector(),update(){}},renderPartList(){},
 setStatus(message){status=message;},hostMessage(message){sent=message;},THREE:{}};
const document = {createElement:tag=>new Element(tag),documentElement:{style:{setProperty(){}}}};
vm.runInNewContext(fs.readFileSync(__dirname+'/viewer-workflows.js','utf8'),{
 window:{WoWSViewerCore:core,chrome:{webview:{addEventListener:(name,fn)=>listener=fn}},addEventListener(name,fn){events[name]=fn;}},
 document,location:{search:'?lang=en'},URLSearchParams,getComputedStyle:()=>({getPropertyValue:()=>300})});
listener({data:{type:'loadModel',modelIdentity:'sha-a',provenance:{source:{game:'korabli'}}}});
events['wows-viewer-loaded']({detail:{identity:'sha-a'}});
core.orbit.target.value = [0,0,0];
const note = elements.find(e=>e.tag==='textarea');note.value='<script>plain note</script>';
part.visible=false;
elements.find(e=>e.textContent==='Save session').listeners.click();
const session=JSON.parse(JSON.stringify(sent.session));
part.visible=true; note.value='';
listener({data:{type:'restoreViewerSession',session}});
assert.equal(part.visible,false);assert.equal(note.value,'<script>plain note</script>');
part.visible=true;
listener({data:{type:'restoreViewerSession',session:{...session,model:'other'}}});
assert.equal(part.visible,true);assert.match(status,/identity differs/);
listener({data:{type:'restoreViewerSession',session:{...session,hidden:['missing']}}});
assert.equal(part.visible,true);assert.match(status,/unavailable/);
listener({data:{type:'restoreViewerSession',session:{...session,up:[0,0,0]}}});
assert.equal(part.visible,true);assert.match(status,/Invalid/);
elements.find(e=>e.tag==='details').listeners.toggle();
const report=JSON.parse(elements.find(e=>e.tag==='pre').textContent);
assert.equal(report.statistics.triangles,2);assert.equal(report.statistics.parts,1);
console.log('Viewer workflow: round trip, identity / orphan / invalid vector rejection, plain notes and stats passed');
