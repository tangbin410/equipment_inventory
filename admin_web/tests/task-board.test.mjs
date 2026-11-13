import assert from 'node:assert/strict';
import {test} from 'node:test';
import {filterTaskList, taskListRows} from '../src/taskBoard.ts';

const tasks = [
  {id:1, task_name:'机房甲盘点', room_name:'机房甲', area_name:'南区', claimed_name:'张三', import_batch_id:'BATCH-A', status:'doing', total:8, scanned:1},
  {id:2, task_name:'机房乙盘点', room_name:'机房乙', area_name:'北区', claimed_name:'李四', import_batch_id:'BATCH-B', status:'done', total:2, scanned:2},
  {id:3, task_name:'旧清单', room_name:'机房甲', status:'doing', superseded_by_task_id:4},
  {id:4, task_name:'新清单', room_name:'机房甲', status:'pending', waiting_for_task_id:3},
];

test('任务列表支持模糊文本查询，状态及片区精确筛选，不混入相似状态', () => {
  assert.deepEqual(filterTaskList(tasks, 'room_name', ' 甲 ').map(task=>task.id), [1,3,4]);
  assert.deepEqual(filterTaskList(tasks, 'claimed_name', '张').map(task=>task.id), [1]);
  assert.deepEqual(filterTaskList(tasks, 'import_batch_id', 'batch-a').map(task=>task.id), [1]);
  assert.deepEqual(filterTaskList(tasks, 'task_name', '清单').map(task=>task.id), [3,4]);
  assert.deepEqual(filterTaskList(tasks, 'field_stage', '待释放旧任务').map(task=>task.id), [3]);
  assert.deepEqual(filterTaskList(tasks, 'field_stage', '等待旧任务释放').map(task=>task.id), [4]);
  assert.deepEqual(filterTaskList(tasks, 'field_stage', '盘点中').map(task=>task.id), [1]);
  assert.deepEqual(filterTaskList(tasks, 'area_name', '汇聚及核心').map(task=>task.id), [3,4]);
  assert.deepEqual(filterTaskList(tasks, 'area_name', '南区').map(task=>task.id), [1]);
});

test('全部任务不要求条件，其余空条件或查无结果不会误导出全部任务', () => {
  assert.equal(filterTaskList(tasks, '__all__', '').length, 4);
  assert.deepEqual(filterTaskList(tasks, 'room_name', ' '), []);
  assert.deepEqual(filterTaskList(tasks, 'claimed_name', '不存在'), []);
});

test('序号跨页连续，筛选后重新编号，进度及导出范围保留完整查询结果', () => {
  const many = Array.from({length:45}, (_, index)=>({...tasks[0], id:index+1, room_name:`机房${index+1}`}));
  const original = structuredClone(many);
  const rows = taskListRows(filterTaskList(many, '__all__', ''));
  assert.equal(rows.slice(20,40)[0].序号, 21);
  assert.equal(rows.at(-1).序号, 45);
  assert.equal(rows[0].进度, '1/8');
  assert.equal(rows[0].完成率, '13%');
  assert.equal(rows.length, 45, '分页只限制展示，不能截断查询和导出范围');
  const filtered = taskListRows(filterTaskList(many, 'room_name', '45'));
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].序号, 1);
  assert.deepEqual(many, original);
});
