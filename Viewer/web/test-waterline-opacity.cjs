const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/viewer-advanced.js', 'utf8');
const context = {waterlineOpacity:{value:'18'},waterlineOpacityValue:{textContent:''},waterline:{material:{opacity:0.18,transparent:true,needsUpdate:false}}};
vm.createContext(context);
const start=source.indexOf('  function updateWaterlineOpacity() {');
const end=source.indexOf('  function updateWaterline() {',start);
assert(start >= 0 && end > start);
vm.runInContext(source.slice(start,end),context);
for(const [input,expected] of [['0',0],['18',0.18],['55',0.55],['100',1],['120',1],['-10',0],['invalid',0.18]]) {
  context.waterlineOpacity.value=input;
  context.updateWaterlineOpacity();
  assert.equal(context.waterline.material.opacity,expected);
  assert.equal(context.waterline.material.transparent,expected < 1);
  assert.equal(context.waterlineOpacityValue.textContent,`${Math.round(expected * 100)}%`);
}
assert.equal(context.waterline.material.needsUpdate,true);
console.log('Waterline opacity: 0/18/55/100%, valid limits, invalid input and blending transitions passed');
