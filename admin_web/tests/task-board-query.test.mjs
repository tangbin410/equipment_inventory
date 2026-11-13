import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import {computed, effectScope, ref, watch} from 'vue';
import * as taskBoard from '../src/taskBoard.ts';
import {isOverdue, taskStage} from '../src/utils.ts';

const tasks = [
  {id:1, task_name:'机房甲盘点', room_name:'机房甲', area_name:'南区', status:'doing', total:8, scanned:1},
  {id:2, task_name:'机房乙盘点', room_name:'机房乙', area_name:'北区', status:'done', total:2, scanned:2},
];
const source = readFileSync(new URL('../src/pages/TasksPage.vue', import.meta.url), 'utf8');
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
// 暴露真实页面状态供行为验证；查询、失效及导出逻辑均执行页面原代码。
const output = ts.transpileModule(`${script}\nexport {
  tasks, listQueryField, listQueryText, listHasQueried, listLoading, listPage,
  filteredTasks, pageTaskRows, totalDevices, scannedDevices, pendingDevices,
  queryTaskList, resetTaskList, exportTaskList
};`, {compilerOptions:{module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022}}).outputText;

function page(t) {
  const mounted = [], responses = [], gets = [], posts = [], downloads = [], warnings = [];
  const scope = effectScope();
  t.after(() => scope.stop());
  const exports = {};
  const modules = {
    vue:{computed, ref, watch, onMounted:callback => mounted.push(callback)},
    'element-plus':{ElMessage:{warning:text => warnings.push(text), success(){}}, ElMessageBox:{}},
    '@element-plus/icons-vue':{},
    '../components/SectionTitle.vue':{},
    '../api':{
      apiGet(path) {
        gets.push(path);
        return responses.length ? responses.shift() : Promise.resolve(structuredClone(tasks));
      },
      apiPost:async (path, payload) => { posts.push({path, payload}); return 'workbook'; },
      apiDelete:async () => {},
    },
    '../utils':{isOverdue, taskStage},
    '../taskBoard':taskBoard,
    '../permissions':{permissions:{manageTasks:true}},
  };
  scope.run(() => runInNewContext(output, {
    exports, defineExpose(){},
    require(name) {
      assert.ok(Object.hasOwn(modules, name), `未处理的页面依赖：${name}`);
      return modules[name];
    },
    URL:{createObjectURL:() => 'blob:task-list', revokeObjectURL(){}},
    document:{createElement:() => ({click(){downloads.push(this.download);}})},
  }, {filename:'TasksPage.vue'}));
  return {
    state:exports, gets, posts, downloads, warnings,
    mount:async () => { for (const callback of mounted) await callback(); },
    respondWith:response => responses.push(response),
    ids:() => Array.from(exports.filteredTasks.value, row => row.id),
  };
}

test('初始加载统计数据，不显示任务列表且不能直接导出全部任务', async t => {
  const p = page(t), s = p.state;
  await p.mount();
  assert.equal(p.gets.length, 1);
  assert.equal(s.tasks.value.length, 2);
  assert.equal(s.totalDevices.value, 10);
  assert.equal(s.scannedDevices.value, 3);
  assert.equal(s.pendingDevices.value, 7);
  assert.equal(s.listHasQueried.value, false);
  assert.equal(s.filteredTasks.value.length, 0);
  assert.equal(s.pageTaskRows.value.length, 0);
  await s.exportTaskList();
  assert.equal(p.posts.length, 0);
  assert.equal(p.downloads.length, 0);
});

test('查询成功后才展示结果，导出仅包含本次查询范围', async t => {
  const p = page(t), s = p.state;
  await p.mount();
  s.listQueryField.value = 'room_name';
  s.listQueryText.value = ' 甲 ';
  await s.queryTaskList();
  assert.equal(s.listHasQueried.value, true);
  assert.deepEqual(p.ids(), [1]);
  assert.equal(s.pageTaskRows.value[0].机房, '机房甲');
  assert.equal(s.totalDevices.value, 10, '任务查询不能缩小上方整体统计范围');
  await s.exportTaskList();
  assert.equal(p.posts.length, 1);
  assert.equal(p.posts[0].path, '/tasks/export');
  assert.deepEqual(Array.from(p.posts[0].payload.task_ids), [1]);
  assert.deepEqual(p.downloads, ['任务列表.xlsx']);
});

test('修改查询内容、查询字段或重置，都立即隐藏旧结果并阻止导出', async t => {
  const p = page(t), s = p.state;
  await p.mount();
  s.listQueryField.value = 'room_name';
  s.listQueryText.value = '甲';
  await s.queryTaskList();
  s.listQueryText.value = '乙';
  assert.equal(s.listHasQueried.value, false, '同步失效，不能等待下一轮更新才隐藏');
  assert.deepEqual(p.ids(), []);
  await s.exportTaskList();
  await s.queryTaskList();
  assert.deepEqual(p.ids(), [2]);
  s.listQueryField.value = '__all__';
  assert.equal(s.listHasQueried.value, false);
  await s.exportTaskList();
  await s.queryTaskList();
  assert.deepEqual(p.ids(), [1,2]);
  s.listPage.value = 3;
  s.resetTaskList();
  assert.equal(s.listHasQueried.value, false);
  assert.equal(s.listPage.value, 1);
  assert.equal(s.listQueryField.value, '__all__');
  assert.equal(s.listQueryText.value, '');
  assert.equal(s.pageTaskRows.value.length, 0);
  await s.exportTaskList();
  assert.equal(p.posts.length, 0);
});

test('查无结果和缺少查询内容，都不能误导出全部任务', async t => {
  const p = page(t), s = p.state;
  await p.mount();
  s.listQueryField.value = 'room_name';
  s.listQueryText.value = '不存在';
  await s.queryTaskList();
  assert.equal(s.listHasQueried.value, true, '已查询但无结果，页面可以展示空结果');
  assert.equal(s.filteredTasks.value.length, 0);
  await s.exportTaskList();
  const getCount = p.gets.length;
  s.listQueryText.value = ' ';
  await s.queryTaskList();
  assert.equal(s.listHasQueried.value, false);
  assert.equal(p.gets.length, getCount);
  assert.equal(p.warnings.length, 1);
  await s.exportTaskList();
  assert.equal(p.posts.length, 0);
});

test('查询尚未返回时重置，晚到响应不能恢复旧结果或开放导出', async t => {
  const p = page(t), s = p.state;
  await p.mount();
  s.listQueryField.value = 'room_name';
  s.listQueryText.value = '甲';
  let resolve;
  p.respondWith(new Promise(done => {resolve = done;}));
  const query = s.queryTaskList();
  assert.equal(s.listLoading.value, true);
  assert.equal(s.listHasQueried.value, false);
  await s.exportTaskList();
  s.resetTaskList();
  resolve(structuredClone(tasks));
  await query;
  assert.equal(s.listLoading.value, false);
  assert.equal(s.listHasQueried.value, false);
  assert.equal(s.listQueryField.value, '__all__');
  assert.equal(s.pageTaskRows.value.length, 0);
  await s.exportTaskList();
  assert.equal(p.posts.length, 0);
  await s.queryTaskList();
  assert.deepEqual(p.ids(), [1,2], '重置后仍可重新执行新查询');
});
