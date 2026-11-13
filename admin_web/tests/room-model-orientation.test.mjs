import assert from 'node:assert/strict';
import {test} from 'node:test';
import RoomRenderer from '../src/roomModelRenderer.js';

function renderer(){
 const r=Object.create(RoomRenderer.prototype);
 Object.assign(r,{render(){},selected:null,showDevices:true,scale:64,center:{x:0,y:0},origin:{x:0,y:0}});
 return r;
}
const cabinet=(rowIndex,face,kind='equipment')=>({id:`${kind}:${rowIndex}:${face}`,code:'AA01'+face,rowIndex,index:1,face,kind,issues:[],devices:[{id:1,name:'跨柜设备'}]});

test('所有排、所有柜架类型的俯视上半均为A、下半均为B，且背靠背相接',()=>{
 const r=renderer();r.setView('top');
 for(const kind of ['equipment','odf','iodf'])for(const row of [0,1,2,7,25]){
  const a=r._geometry(cabinet(row,'A',kind)),b=r._geometry(cabinet(row,'B',kind));
  assert.equal(a.y+a.d,b.y,'A与B背部在同一平面相接');
  const upper=r._project(a.x+.5,a.y+a.d/2,a.h),lower=r._project(b.x+.5,b.y+b.d/2,b.h);
  assert.ok(upper.y<lower.y,`${kind} 第${row+1}排A面在B面上方`);
  const full=r._geometry(cabinet(row,'full',kind));
  assert.equal(full.y,a.y);assert.equal(full.y+full.d,b.y+b.d);
 }
});

test('正面展示A面前板，背面展示B面前板；设备与配线面板朝向同步',()=>{
 const r=renderer();
 for(const kind of ['equipment','odf','iodf'])for(const row of [0,1]){
  for(const [view,face] of [['front','A'],['back','B']]){
   r.setView(view);
   const planes=[];r._box=()=>{};r._frontPlane=(x,y)=>planes.push(y);
   const object=cabinet(row,face,kind),g=r._geometry(object);
   r._cabinet(object);
   assert.ok(planes.length>0,`${view}应能看到${kind}${face}面的前板`);
   assert.ok(planes.every(y=>face==='A'?y<g.y:y>g.y+g.d),'前板在柜体外侧，不在背靠背中间');
   planes.length=0;r._cabinet(cabinet(row,face==='A'?'B':'A',kind));
   assert.equal(planes.length,0,'背部不显示前板');
  }
 }
});

test('总览默认从A面外侧观察，鼠标选择仍返回各自对象ID',()=>{
 const r=renderer();r.setView('overview');assert.ok(Math.cos(r.yaw)<0);
 const a=cabinet(0,'A'),b=cabinet(0,'B');
 r.hitFaces=[{cabinet:a,vertices:[{x:0,y:0},{x:100,y:0},{x:100,y:50},{x:0,y:50}]},{cabinet:b,vertices:[{x:0,y:50},{x:100,y:50},{x:100,y:100},{x:0,y:100}]}];
 assert.equal(r._pick({x:50,y:25}),a.id);assert.equal(r._pick({x:50,y:75}),b.id);
});
