import type {Task} from './types';
import {formatTime, isOverdue, taskStage} from './utils.ts';

export function filterTaskList(tasks: Task[], field: string, query: string): Task[] {
  if (field === '__all__') return tasks;
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [];
  return tasks.filter(task => {
    const value = field === 'field_stage' ? taskStage(task)
      : field === 'area_name' ? task.area_name || '汇聚及核心'
      : field === 'claimed_name' ? task.claimed_name || '-'
      : String((task as unknown as Record<string, unknown>)[field] || '');
    return ['field_stage', 'area_name'].includes(field) ? value === query.trim() : value.toLocaleLowerCase().includes(needle);
  });
}

export function taskListRows(tasks: Task[]) {
  return tasks.map((task, index) => ({
    序号: index + 1,
    机房: task.room_name,
    导入批次: task.import_batch_id || '',
    片区: task.area_name || '汇聚及核心',
    任务名称: task.task_name,
    领取人: task.claimed_name || '-',
    现场状态: taskStage(task),
    任务更新: task.replacement_notice || '',
    进度: `${task.scanned || 0}/${task.total || 0}`,
    完成率: `${Math.round(((task.scanned || 0) / (task.total || 1)) * 100)}%`,
    领取时间: formatTime(task.claimed_at),
    是否超时: isOverdue(task) ? '是' : '否',
  }));
}
