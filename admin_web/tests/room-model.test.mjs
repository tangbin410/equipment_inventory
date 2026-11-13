import assert from 'node:assert/strict';
import {test} from 'node:test';
import {buildRoomModel, splitModelPositions, CABINET_U, U_MM} from '../src/roomModel.ts';
const task={id:1,room_name:'机房甲',status:'done',task_name:'盘点'};
const cabinet=(kind,code,barcode='',id=code)=>({id,task_id:1,room_name:'机房甲',cabinet_type:kind,rack_no:code,cabinet_barcode:barcode,no_barcode:!barcode});
const device=(id,position,extra={})=>({id,device_name:'设备'+id,scanned_at:'2026-11-12T10:00:00',rack_no:position,cabinet_no_barcode:true,record_type:'normal',...extra});
const report=(cabinet_records,records=[])=>({task,cabinet_records,records});

test('三类柜架独立建模，Z布局保留缺排；47U与柜体尺寸分开',()=>{
 const data=buildRoomModel(report([cabinet('equipment','AA01A'),cabinet('odf','AC02B'),cabinet('iodf','AE03A')],[device(1,'AA01A')]),['equipment','odf','iodf']);
 assert.deepEqual(data.rows,['AA','AB','AC','AD','AE']);
 assert.deepEqual(data.cabinets.map(c=>[c.kind,c.rowIndex,c.index,c.face]),[['equipment',0,1,'A'],['odf',2,2,'B'],['iodf',4,3,'A']]);
 assert.equal(data.stats.deviceCount,1);assert.equal(data.stats.mappedDeviceCount,1);
 assert.equal(CABINET_U,47);assert.equal(Math.round(CABINET_U*U_MM*100)/100,2089.15);
});

test('同柜多设备汇总、多柜位设备不重复计数，设备只进入综合柜',()=>{
 const data=buildRoomModel(report([cabinet('equipment','AA01A'),cabinet('equipment','AA01A'),cabinet('equipment','AA01B'),cabinet('odf','AA02A')],[device(1,'AA01A'),device(2,'AA01A、AA01B'),device(3,'AA02A')]),['equipment','odf']);
 assert.equal(data.cabinets.length,3);assert.equal(data.cabinets[0].devices.length,2);
 assert.equal(data.cabinets.find(c=>c.kind==='odf').devices.length,0);
 assert.equal(data.stats.deviceCount,3);assert.equal(data.stats.mappedDeviceCount,2);
 assert.ok(!data.issues.some(i=>i.title==='设备关联多个柜位'));
 for(const code of ['AA01A','AA01B']) {
  const object=data.cabinets.find(c=>c.code===code);
  assert.ok(object.devices.some(d=>d.id===2));
  assert.deepEqual(object.issues,[]);
  assert.equal(object.pendingOverlap,false);
 }
 assert.ok(data.unplaced.some(i=>i.name==='设备3'));
});

test('正常跨柜设备的多条码汇总不能展开为每柜身份冲突',()=>{
 const data=buildRoomModel(report([
  cabinet('equipment','AA01A、AA02A','CAB1'),
  cabinet('equipment','AA01A、AA02A','CAB2'),
 ],[device(1,'AA01A、AA02A',{cabinet_no_barcode:false,cabinet_barcodes:'CAB1,CAB2'})]),['equipment']);
 assert.equal(data.cabinets.length,2);
 assert.equal(data.stats.deviceCount,1);assert.equal(data.stats.mappedDeviceCount,1);
 assert.equal(data.stats.issueCount,0);
 for(const object of data.cabinets){
  assert.equal(object.pendingOverlap,false);
  assert.deepEqual(object.barcodes,[], '不猜测条码与位置的逐一对应');
  assert.deepEqual(object.devices.map(d=>d.id),[1]);
  assert.deepEqual(object.devices[0].cabinetBarcodes,['CAB1','CAB2']);
 }
});

test('跨柜汇总仍可使用其他设备的单柜反馈确定各柜条码',()=>{
 const data=buildRoomModel(report([
  cabinet('equipment','AA01A、AA02A','CAB1'),
  cabinet('equipment','AA01A、AA02A','CAB2'),
 ],[
  device(1,'AA01A、AA02A',{cabinet_no_barcode:false,cabinet_barcodes:'CAB1,CAB2'}),
  device(2,'AA01A',{cabinet_no_barcode:false,cabinet_barcodes:'CAB1'}),
  device(3,'AA02A',{cabinet_no_barcode:false,cabinet_barcodes:'CAB2'}),
 ]),['equipment']);
 assert.deepEqual(data.cabinets.map(c=>c.barcodes),[['CAB1'],['CAB2']]);
 assert.equal(data.stats.issueCount,0);
 assert.ok(data.cabinets.every(c=>c.devices.some(d=>d.id===1)));
});

test('全深与半深、同一面不同类型占位冲突拒绝猜测；正常背靠背不冲突',()=>{
 for(const sources of [[cabinet('equipment','AA01'),cabinet('odf','AA01B')],[cabinet('equipment','AA01A'),cabinet('iodf','AA01A')]]){
  const data=buildRoomModel(report(sources),['equipment','odf','iodf']);
  assert.equal(data.cabinets.length,2);assert.ok(data.cabinets.every(c=>c.pendingOverlap));
 }
 const normal=buildRoomModel(report([cabinet('equipment','AA01A'),cabinet('odf','AA01B')]),['equipment','odf']);
 assert.ok(normal.cabinets.every(c=>!c.pendingOverlap));
});

test('同一位置多条码与同条码多位置保留待核实，大小写和全角可规范化但不猜错号',()=>{
 assert.deepEqual(splitModelPositions('ａａ０１ａ，AA01A、AC0B'),['AA01A','AC0B']);
 const data=buildRoomModel(report([cabinet('equipment','AA01A','CAB1'),cabinet('equipment','AA01A','CAB2'),cabinet('equipment','AA02A','CAB1'),cabinet('odf','AA00A'),cabinet('iodf','AC0B')]),['equipment','odf','iodf']);
 assert.equal(data.cabinets.length,2);assert.ok(data.cabinets.every(c=>c.pendingOverlap));
 assert.deepEqual(data.unplaced.map(x=>x.rawPosition),['AA00A','AC0B']);
});

test('仅所选数据建模；不将盘亏、未扫、作废和拼销迁出当作实物',()=>{
 const records=[device(1,'AA01A'),device(2,'AA01A',{record_type:'missing'}),device(3,'AA01A',{scanned_at:''}),device(4,'AA01A',{is_void:true}),device(5,'AA01A',{pairing:{role:'outbound'}}),device(6,'AA01A',{record_type:'surplus'})];
 const data=buildRoomModel(report([cabinet('equipment','AA01A'),cabinet('odf','AB01B')],records),['equipment']);
 assert.equal(data.cabinets.length,1);assert.equal(data.stats.deviceCount,2);assert.equal(data.stats.mappedDeviceCount,2);
 const racks=buildRoomModel(report([cabinet('equipment','AA01A'),cabinet('odf','AB01B')],records),['odf']);
 assert.equal(racks.cabinets.length,1);assert.equal(racks.stats.deviceCount,0);
});
