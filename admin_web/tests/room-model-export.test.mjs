import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

const renderer = fs.readFileSync(new URL('../src/roomModelRenderer.js', import.meta.url), 'utf8');
const source = fs.readFileSync(new URL('../src/roomModelExport.ts', import.meta.url), 'utf8')
  .replace(/^import rendererSource from ['"].*\?raw['"];$/m, () => `const rendererSource = ${JSON.stringify(renderer)};`);
const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext}}).outputText;
const {buildRoomModelHtml} = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

const attack = '</script><script>globalThis.injected = 1</script><img src=x onerror="globalThis.injected = 2">&\u2028\u2029';
function fixture() {
  const device = {id:9,name:'shared-device ' + attack,category:attack,model:'MODEL-9 ' + attack,barcode:attack,positions:['AA01A','AA02A'],cabinetBarcodes:['CAB-ALL',attack]};
  const cabinet = (code,kind,row,index,face,devices=[]) => ({id:kind+':'+code,code,kind,row,rowIndex:row.charCodeAt(1)-65,index,face,barcodes:['BAR-' + code],devices,issues:[attack],pendingOverlap:false});
  return {
    cabinets:[cabinet('AA01A','equipment','AA',1,'A',[device]),cabinet('AA02A','equipment','AA',2,'A',[device]),cabinet('AB01A','odf','AB',1,'A'),cabinet('AC01B','iodf','AC',1,'B')],
    rows:['AA','AB','AC'],maxPosition:2,stats:{objectCount:4,deviceCount:1,mappedDeviceCount:1,unplacedCount:1,issueCount:1},
    issues:[{id:'global-only',title:'not-exported-global',detail:'not-exported-global',objectIds:[]}],
    unplaced:[{name:'not-exported-unplaced',rawPosition:'?',reason:'?',kind:'equipment'}],
    rawReport:'not-exported-report',token:'not-exported-token',serviceAddress:'https://private.example.invalid',
  };
}
const metadata = {roomName:attack,roomCode:attack,completedAt:'2026-11-12T00:00:00Z',exportedAt:'2026-11-12T01:02:03Z',account:'not-exported-account'};
const scripts = html => [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)].map(match=>match[1]);

class Element {
  constructor(tagName='div') {
    this.tagName=tagName;this.children=[];this.listeners=new Map();this.attributes=new Map();this.style={};this.dataset={};this.value='';this.checked=false;this.disabled=false;this.className='';this._text='';
    const classes = () => new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList={add:value=>{const values=classes();values.add(value);this.className=[...values].join(' ');},toggle:(value,enabled)=>{const values=classes();if(enabled)values.add(value);else values.delete(value);this.className=[...values].join(' ');}};
  }
  set textContent(value) {this._text=String(value);this.children=[];}
  get textContent() {return this._text + this.children.map(child=>child.textContent).join('');}
  set innerHTML(_value) {throw new Error('Dynamic HTML injection is forbidden');}
  insertAdjacentHTML() {throw new Error('Dynamic HTML injection is forbidden');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this._text='';this.children=[...nodes];}
  addEventListener(type,callback) {if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(callback);}
  removeEventListener(type,callback) {this.listeners.set(type,(this.listeners.get(type)||[]).filter(item=>item!==callback));}
  async emit(type,event={}) {await Promise.all((this.listeners.get(type)||[]).map(callback=>callback(event)));}
  setAttribute(key,value) {this.attributes.set(key,String(value));}
  hasAttribute(key) {return this.attributes.has(key);}
  querySelectorAll(tag) {return this.children.filter(child=>child.tagName===tag);}
  getContext() {return {};}
  getBoundingClientRect() {return {width:0,height:0,left:0,top:0};}
}

function openHtml(html) {
  const [json,script] = scripts(html);
  const ids = ['app','room-name','room-meta','stats','row-filter','scene','views','labels','devices','reset','object-detail','search','search-results','result-count','page-number','previous','next','fullscreen','status','room-model-data'];
  const elements = Object.fromEntries(ids.map(id=>[id,new Element(id==='scene'?'canvas':'div')]));
  elements['room-model-data'].textContent=json;
  for(const view of ['overview','top','front','back']){const button=new Element('button');button.dataset.view=view;elements.views.append(button);}
  const document = new Element('document');
  document.getElementById=id=>elements[id];
  document.createElement=tag=>{assert.notEqual(tag,'script');return new Element(tag);};
  document.fullscreenElement=null;
  elements.app.requestFullscreen=async()=>{document.fullscreenElement=elements.app;await document.emit('fullscreenchange');};
  document.exitFullscreen=async()=>{document.fullscreenElement=null;await document.emit('fullscreenchange');};
  const context = vm.createContext({document,window:new Element('window'),ResizeObserver:class{observe(){}disconnect(){}},requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{}});
  new vm.Script(script).runInContext(context);
  return {elements,document,context};
}

test('恶意字段安全嵌入且离线导出没有网络、模块或原始资料依赖',()=>{
  const html=buildRoomModelHtml(fixture(),metadata);
  const inline=scripts(html);
  assert.equal(inline.length,2,'恶意字段不能结束JSON脚本或生成新的脚本');
  assert.doesNotMatch(inline[0],/[<>&\u2028\u2029]/);
  assert.equal(JSON.parse(inline[0]).meta.roomName,attack);
  assert.equal(JSON.parse(inline[0]).data.cabinets[0].devices[0].name,'shared-device '+attack);
  assert.doesNotMatch(html,/https?:\/\/|<script\b[^>]*\bsrc\s*=|<link\b|type\s*=\s*["']module["']/i);
  assert.doesNotMatch(inline[1],/^\s*(?:import|export)\b/m);
  assert.doesNotMatch(inline[1],/\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|importScripts)\s*\(/);
  assert.doesNotMatch(html,/not-exported-(?:report|token|account|unplaced|global)/);
  const {elements,context}=openHtml(html);
  assert.equal(context.injected,undefined);
  assert.equal(elements['room-name'].textContent,attack);
  assert.match(elements['room-meta'].textContent,/2026\/11\/12 08:00:00/);
  assert.match(elements['room-meta'].textContent,/2026\/11\/12 09:02:03/);
});

test('跨柜设备在各柜都能查询与查看，统计仍去重；支持排筛选、四视角与全屏退出',async()=>{
  const data=fixture();data.cabinets[0].barcodes=[];
  const {elements,document,context}=openHtml(buildRoomModelHtml(data,metadata));
  assert.equal(elements.stats.children[1].children[1].textContent,'1');
  assert.equal(elements.stats.children[2].children[1].textContent,'1');
  assert.equal(elements['search-results'].children.length,4);
  elements.search.value='shared-device';await elements.search.emit('input');
  assert.equal(elements['search-results'].children.length,2);
  for(const [index,code] of ['AA01A','AA02A'].entries()){
    await elements['search-results'].children[index].emit('click');
    assert.ok(elements['object-detail'].textContent.includes(code));
    assert.ok(elements['object-detail'].textContent.includes('shared-device '+attack));
    assert.ok(elements['object-detail'].textContent.includes('设备反馈柜条码：CAB-ALL、'+attack));
    assert.ok(elements['object-detail'].textContent.includes('所属机柜位：AA01A、AA02A'));
    if(index===0) assert.ok(elements['object-detail'].textContent.includes('见下方设备关联条码'));
    assert.equal(context.injected,undefined);
  }
  elements.search.value='MODEL-9';await elements.search.emit('input');assert.equal(elements['search-results'].children.length,2);
  elements.search.value='CAB-ALL';await elements.search.emit('input');assert.equal(elements['search-results'].children.length,2);
  elements['row-filter'].value='AC';await elements['row-filter'].emit('change');assert.match(elements['object-detail'].textContent,/点击模型对象/);
  await elements['search-results'].children[0].emit('click');assert.equal(elements['row-filter'].value,'AA');
  for(const button of elements.views.children){await button.emit('click');assert.equal(button.attributes.get('aria-pressed'),'true');}
  await elements.fullscreen.emit('click');assert.equal(document.fullscreenElement,elements.app);assert.equal(elements.fullscreen.textContent,'退出全屏');
  await elements.fullscreen.emit('click');assert.equal(document.fullscreenElement,null);assert.equal(elements.fullscreen.textContent,'全屏查看');
  await elements.reset.emit('click');assert.equal(elements['row-filter'].value,'');
  elements.search.value='';await elements.search.emit('input');
  for(const [index,purpose] of [[2,'光缆配线'],[3,'光纤成端']]){
    await elements['search-results'].children[index].emit('click');
    assert.ok(elements['object-detail'].textContent.includes(purpose));
    assert.ok(elements['object-detail'].textContent.includes('独立机架面板示意'));
    assert.doesNotMatch(elements['object-detail'].textContent,/关联设备|没有关联的设备/);
  }
});

test('缺省导出时间写入快照，分页仅渲染当前20个对象',async()=>{
  const data=fixture();const base=data.cabinets[0];data.cabinets=Array.from({length:45},(_,i)=>({...base,id:'equipment:'+i,code:'AA'+String(i+1).padStart(2,'0')+'A',index:i+1,devices:[]}));data.stats.objectCount=45;
  const before=Date.now(),html=buildRoomModelHtml(data,{roomName:'测试机房',completedAt:'2026-11-12 10:30:00'}),after=Date.now();
  assert.ok(html.includes('1U = 44.45mm'));
  const exportedAt=Date.parse(JSON.parse(scripts(html)[0]).meta.exportedAt);assert.ok(exportedAt>=before&&exportedAt<=after);
  const {elements}=openHtml(html);assert.match(elements['room-meta'].textContent,/2026\/11\/12 10:30:00/);assert.equal(elements['search-results'].children.length,20);assert.equal(elements['page-number'].textContent,'1 / 3');
  await elements.next.emit('click');assert.equal(elements['search-results'].children.length,20);
  await elements.next.emit('click');assert.equal(elements['search-results'].children.length,5);assert.equal(elements.next.disabled,true);
});
