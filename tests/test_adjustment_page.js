const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  let definition, modalPromise, submitted;
  const tasks = [
    {id:1,status:'dispatched',comparison:{field_done:true},field_records:[]},
    {id:2,status:'dispatched',comparison:{field_done:false},field_records:[{id:11,device_name:'设备甲',actual_model:'M1',asset_barcodes:'A1',cabinet_barcodes:'C1',rack_no:'R1'}]},
    {id:3,status:'dispatched',comparison:{field_done:false},field_records:[],field_target_tasks:[{id:9,room_name:'目标机房'}]},
  ];
  const api = {
    getMyAdjustmentTasks: async () => tasks,
    completeAdjustmentTask: async (...args) => {submitted=args;},
  };
  vm.runInNewContext(fs.readFileSync('miniprogram/pages/adjustments/adjustments.js','utf8'), {
    getApp:()=>({isLoggedIn:()=>true,globalData:{userInfo:{user_id:8,name:'现场',area_name:'南区'}}}),
    Page:value=>{definition=value;},
    require:name=>name.endsWith('/api') ? api : {getUnreadReminders:()=>[],markRemindersSeen(){}},
    wx:{stopPullDownRefresh(){},showToast(){},showLoading(){},hideLoading(){},showModal(options){modalPromise=options.success({confirm:true});}},
  });
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values){
    for(const [key,value] of Object.entries(values)){
      const path=key.replace(/\[(\d+)\]/g,'.$1').split('.');
      let target=this.data;
      for(const segment of path.slice(0,-1)) target=target[segment];
      target[path.at(-1)]=value;
    }
  }};
  await page.loadAdjustments();
  assert.equal(page.data.tasks[0]._canComplete,false,'现场已提交、管理员尚未处理时不能重复提交');
  assert.equal(page.data.pendingCount,2);
  page.completeTask({currentTarget:{dataset:{id:2}}});
  assert.equal(submitted,undefined,'空说明不能提交');
  page.inputFeedback({currentTarget:{dataset:{index:1}},detail:{value:'已核实并更新设备型号'}});
  page.editFeedbackRecord({currentTarget:{dataset:{index:1,recordindex:0,field:'actual_model'}},detail:{value:'M2'}});
  page.completeTask({currentTarget:{dataset:{id:2}}});
  await modalPromise;
  assert.equal(submitted[0],2);
  assert.equal(submitted[1],'已核实并更新设备型号');
  assert.equal(submitted[2][0].record_id,11);
  assert.equal(submitted[2][0].actual_model,'M2');
  assert.equal(submitted[3],null);
  page.inputFeedback({currentTarget:{dataset:{index:2}},detail:{value:'已补充本设备反馈'}});
  for(const [field,value] of Object.entries({device_name:'新增设备',actual_model:'M3',asset_barcodes:'A3',rack_no:'R3',cabinet_no_barcode:true})) {
    page.inputNewFeedback({currentTarget:{dataset:{index:2,field}},detail:{value}});
  }
  page.completeTask({currentTarget:{dataset:{id:3}}});
  await modalPromise;
  assert.equal(submitted[3].task_id,9,'新增反馈只发给本单对应机房');
  assert.equal(submitted[3].asset_barcodes,'A3');
  console.log('整改小程序验证通过：混合责任、必填说明、指定设备修改及本单设备补录。');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
