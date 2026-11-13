import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import {computed,effectScope,nextTick,ref,watch} from 'vue';
import * as utils from '../src/utils.ts';
import * as selection from '../src/comparisonSelection.ts';
import * as pairing from '../src/inventoryPairing.ts';
import * as threeWay from '../src/threeWay.ts';
import * as comparisonIssues from '../src/comparisonIssues.ts';

const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});return{promise,resolve,reject};};
class FormDataStub {constructor(){this.fields={};}append(key,value){this.fields[key]=value;}}
function page(t,name,names,api={}) {
  const source=readFileSync(new URL(`../src/pages/${name}.vue`,import.meta.url),'utf8');
  const script=source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const output=ts.transpileModule(script+`\nexport {${names.join(',')}};`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const exports={},scope=effectScope(),unmounts=[];
  const modules={
    vue:{computed,nextTick,ref,watch,onMounted(){},onBeforeUnmount:callback=>unmounts.push(callback)},
    'element-plus':{ElMessage:{success(){},warning(){}},ElMessageBox:{prompt:async()=>({value:'0099'})}},
    '@element-plus/icons-vue':{},
    '../api':{apiGet:api.get||(async()=>({current:null,history:[]})),apiPost:api.post||(async()=>({})),apiDelete:async()=>{},downloadBlob:api.download||(async()=>{}),getToken:()=>''},
    '../permissions':{permissions:ref({writeGovernance:true,manageAssets:true})},
    '../utils':utils,'../inventoryPairing':pairing,'../comparisonSelection':selection,
    '../threeWay':threeWay,'../comparisonIssues':comparisonIssues,'../aiSession':{saveAiCheckSession(){},setAiChatTaskIds(){}},
  };
  scope.run(()=>runInNewContext(output,{exports,FormData:FormDataStub,URLSearchParams,require(name){
    if(/^\.\.\/components\/.+\.vue$/.test(name)) return {};
    assert.ok(Object.hasOwn(modules,name),`未处理的页面依赖：${name}`);return modules[name];
  }},{filename:name+'.vue'}));
  let unmounted=false;
  const unmount=()=>{if(unmounted)return;unmounted=true;unmounts.forEach(callback=>callback());scope.stop();};
  t.after(unmount);
  return{state:exports,unmount};
}

test('整改候选按当前批次落状态，慢旧请求不会覆盖新批次',async t=>{
  const a=deferred(),b=deferred();let calls=0;
  const p=page(t,'GovernancePage',['tasks','batch','candidates','loadCandidates'],{get:()=>++calls===1?a.promise:b.promise}),s=p.state;
  s.tasks.value=[{id:1,status:'done',import_batch_id:'A'},{id:2,status:'done',import_batch_id:'B'}];
  s.batch.value='A';const first=s.loadCandidates();
  s.candidates.value={groups:['old']};s.batch.value='B';const second=s.loadCandidates();
  assert.equal(s.candidates.value,null,'等待新批次时立即隐藏旧问题，不能继续建单');
  b.resolve({groups:['B']});await second;a.resolve({groups:['A']});await first;
  assert.equal(s.batch.value,'B');assert.deepEqual(Array.from(s.candidates.value.groups),['B']);
});

test('整改候选刷新、失败和卸载均不能恢复旧问题',async t=>{
  const old=deferred(),taskList=deferred(),fresh=deferred(),late=deferred();let reconciliations=0;
  const p=page(t,'GovernancePage',['tasks','batch','candidates','loading','loadCandidates','refresh'],{get:path=>{
    assert.notEqual(path,'/summary','仅选择批次不能读取全量现场明细');
    if(path==='/tasks')return taskList.promise;
    if(!path.startsWith('/reconciliation?'))return Promise.resolve([]);
    return [old,fresh,late][reconciliations++].promise;
  }}),s=p.state;
  const tasks=[{id:1,status:'done',import_batch_id:'A'}];s.tasks.value=tasks;s.batch.value='A';
  const first=s.loadCandidates(),refresh=s.refresh();old.resolve({groups:['old']});await first;
  assert.equal(s.candidates.value,null);taskList.resolve(tasks);
  await new Promise(resolve=>setImmediate(resolve));fresh.reject(new Error('request failed'));await assert.rejects(refresh);
  assert.equal(s.candidates.value,null);assert.equal(s.loading.value,false);
  const pending=s.loadCandidates();p.unmount();late.resolve({groups:['late']});await pending;assert.equal(s.candidates.value,null);
});

test('三方比对选机房及补编码后仅刷新任务元数据，保留最新完成筛选',async t=>{
  const calls=[],posts=[];
  const tasks=[
    {id:1,room_name:'机房甲',room_code:'0001',import_batch_id:'A',area_name:'南区',status:'done',assigned_name:'张三',completed_at:'2026-11-01T10:00:00'},
    {id:2,room_name:'机房甲',room_code:'0001',import_batch_id:'B',area_name:'南区',status:'done',assigned_name:'李四',completed_at:'2026-11-12T10:00:00'},
  ];
  const p=page(t,'AiPage',['tasks','roomCandidates','roomPickerOpen','queryRooms','fillLocation'],{
    get:async path=>{calls.push(path);assert.equal(path,'/tasks');return tasks;},
    post:async(path,payload)=>{posts.push({path,payload});},
  }),s=p.state;
  await s.queryRooms();
  assert.equal(s.roomPickerOpen.value,true);assert.deepEqual(Array.from(s.roomCandidates.value,row=>row.id),[2]);
  assert.equal(s.roomCandidates.value[0].assigned_name,'李四');assert.equal(s.roomCandidates.value[0].room_code,'0001');
  await s.fillLocation(tasks[1]);
  assert.deepEqual(calls,['/tasks','/tasks']);assert.equal(posts[0].path,'/reconciliation/tasks/2/location');assert.equal(posts[0].payload.location_code,'0099');
});

test('资产条码连点只保留最新结果，关闭或卸载后不重新打开，也不继续旧条码队列',async t=>{
  const responses=[deferred(),deferred(),deferred(),deferred()],gets=[];
  const p=page(t,'ReportPage',['assetInfoDialog','queryAssetBarcode'],{get:(_path,config)=>{gets.push(config.params.barcode);return responses[gets.length-1].promise;}}),s=p.state;
  const first=s.queryAssetBarcode('A,A2'),second=s.queryAssetBarcode('B');
  responses[1].resolve({found:true,fields:{name:'B'}});await second;
  responses[0].resolve({found:true,fields:{name:'A'}});await first;
  assert.deepEqual(gets,['A','B']);assert.equal(s.assetInfoDialog.value.results[0].barcode,'B');
  const closing=s.queryAssetBarcode('C');s.assetInfoDialog.value.open=false;
  responses[2].resolve({found:true,fields:{name:'C'}});await closing;
  assert.equal(s.assetInfoDialog.value.open,false);assert.equal(s.assetInfoDialog.value.results.length,0);
  const leaving=s.queryAssetBarcode('D');p.unmount();responses[3].resolve({found:true,fields:{name:'D'}});await leaving;
  assert.equal(s.assetInfoDialog.value.results.length,0);
});

test('资产预览绑定本次File，换文件后旧结果不能启用导入或覆盖新预览',async t=>{
  const a=deferred(),b=deferred();let calls=0;
  const p=page(t,'AssetInfoPage',['file','preview','barcodeColumn','previewing','canImport','onFileChange','previewFile','importFile'],{post:()=>++calls===1?a.promise:b.promise}),s=p.state;
  const fileA=new File(['A'],'A.xlsx'),fileB=new File(['B'],'B.xlsx');
  s.onFileChange({target:{files:[fileA]}});const first=s.previewFile();
  s.onFileChange({target:{files:[fileB]}});assert.equal(s.previewing.value,false);const second=s.previewFile();
  a.resolve({barcode_candidates:['A条码']});await first;
  assert.equal(s.preview.value,null);assert.equal(s.previewing.value,true);assert.equal(s.canImport.value,false);
  await s.importFile();assert.equal(calls,2);
  b.resolve({barcode_candidates:['B条码']});await second;
  assert.equal(s.file.value,fileB);assert.equal(s.barcodeColumn.value,'B条码');assert.equal(s.canImport.value,true);
});

test('资产导入锁定已预览文件，期间不能换文件、重复读取或重复导入',async t=>{
  const importing=deferred(),posts=[];
  const p=page(t,'AssetInfoPage',['file','preview','importing','canImport','onFileChange','previewFile','importFile'],{post:(path,data)=>{posts.push({path,fields:data.fields});return path.endsWith('/preview')?Promise.resolve({barcode_candidates:['条码']}):importing.promise;}}),s=p.state;
  const fileA=new File(['A'],'A.xlsx'),fileB=new File(['B'],'B.xlsx');
  s.onFileChange({target:{files:[fileA]}});await s.previewFile();const save=s.importFile();
  assert.equal(s.importing.value,true);assert.equal(s.canImport.value,false);
  s.onFileChange({target:{files:[fileB]}});await s.previewFile();await s.importFile();
  assert.equal(s.file.value,fileA);assert.equal(posts.length,2);assert.equal(posts[1].fields.file,fileA);assert.equal(posts[1].fields.barcode_column,'条码');
  importing.resolve({message:'ok'});await save;assert.equal(s.file.value,null);assert.equal(s.preview.value,null);assert.equal(s.importing.value,false);
});

test('资产预览卸载后晚到结果不恢复预览',async t=>{
  const response=deferred();
  const p=page(t,'AssetInfoPage',['preview','canImport','onFileChange','previewFile'],{post:()=>response.promise}),s=p.state;
  s.onFileChange({target:{files:[new File(['A'],'A.xlsx')]}});const request=s.previewFile();p.unmount();response.resolve({barcode_candidates:['条码']});await request;
  assert.equal(s.preview.value,null);assert.equal(s.canImport.value,false);
});

test('盘点三表超过500条仍可逐页查看，独立翻页，刷新保留有效页并夹紧，换任务回第一页',t=>{
  const {state:s}=page(t,'ReportPage',['selectedTasks','devicePage','cabinetPage','rackPage','rows','pageDeviceRows','pageCabinetRows','pageRackRows']);
  const records=Array.from({length:601},(_,i)=>({id:i+1,device_name:'设备'+(i+1),match_status:'一致'}));
  const cabinets=Array.from({length:601},(_,i)=>({id:i+1,cabinet_type:'equipment',cabinet_barcode:'C'+(i+1)}));
  const racks=Array.from({length:601},(_,i)=>({id:i+1001,cabinet_type:'odf',cabinet_barcode:'R'+(i+1)}));
  s.selectedTasks.value=[{id:1,room_name:'A',status:'done',records,cabinets:[...cabinets,...racks]}];
  assert.equal(s.pageDeviceRows.value.length,20);assert.equal(s.rows.value.length,601);
  s.devicePage.value=s.cabinetPage.value=s.rackPage.value=31;
  assert.equal(s.pageDeviceRows.value[0].__record.id,601);
  assert.equal(s.pageCabinetRows.value[0].__rack.id,601);
  assert.equal(s.pageRackRows.value[0].__rack.id,1601);
  s.devicePage.value=2;assert.equal(s.pageDeviceRows.value[0].__record.id,21);assert.equal(s.rackPage.value,31);
  s.selectedTasks.value=[{id:1,room_name:'A',status:'done',records,cabinets:[...cabinets.slice(0,600),...racks]}];
  assert.equal(s.devicePage.value,2);assert.equal(s.cabinetPage.value,30);assert.equal(s.rackPage.value,31);
  s.selectedTasks.value=[{id:2,room_name:'B',status:'done',records,cabinets:[...cabinets,...racks]}];
  assert.deepEqual([s.devicePage.value,s.cabinetPage.value,s.rackPage.value],[1,1,1]);
});

test('盘点修正刷新回包时保留管理员最新勾选，下载整个选中范围且空选择不发起导出',async t=>{
  const response=deferred(),downloads=[];
  const {state:s}=page(t,'ReportPage',['selectedTasks','refreshSelectedTasks','downloadData'],{
    get:()=>response.promise,download:async url=>downloads.push(url),
  });
  s.selectedTasks.value=[{id:1}];const refresh=s.refreshSelectedTasks();
  s.selectedTasks.value=[{id:2},{id:3}];response.resolve([{id:1},{id:2},{id:3}]);await refresh;
  assert.deepEqual(Array.from(s.selectedTasks.value,row=>row.id),[2,3]);
  await s.downloadData();assert.equal(downloads[0],'/ai/export/inventory?task_ids=2&task_ids=3');
  s.selectedTasks.value=[];await s.downloadData();assert.equal(downloads.length,1);
});

test('盘点明细刷新只接收最新请求，离开页面后晚到数据也不恢复',async t=>{
  const responses=[deferred(),deferred(),deferred()];let i=0;
  const p=page(t,'ReportPage',['summary','loadSummary'],{get:()=>responses[i++].promise}),s=p.state;
  const old=s.loadSummary(),fresh=s.loadSummary();responses[1].resolve([{id:2}]);await fresh;
  responses[0].resolve([{id:1}]);await old;assert.equal(s.summary.value[0].id,2);
  const late=s.loadSummary();p.unmount();responses[2].resolve([{id:3}]);await late;assert.equal(s.summary.value[0].id,2);
});
