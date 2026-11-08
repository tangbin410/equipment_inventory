import type { ComparisonTask } from "./threeWay";

export function comparisonTaskTime(task: ComparisonTask): string {
  return task.field_completed_at || task.completed_at || task.created_at || "";
}

export function latestCompletedRooms(tasks: ComparisonTask[]): ComparisonTask[] {
  const completed = tasks.filter(task => task.status === "done").sort((a, b) => {
    const newest = (Date.parse(comparisonTaskTime(b)) || 0) - (Date.parse(comparisonTaskTime(a)) || 0);
    return newest || b.id - a.id;
  });
  const rooms = new Map<string, ComparisonTask>();
  for (const task of completed) {
    const room = task.room_name.trim() || task.room_code.trim() || `task:${task.id}`;
    if (!rooms.has(room)) rooms.set(room, task);
  }
  return [...rooms.values()];
}

export function queryCompletedRooms(tasks: ComparisonTask[], field: string, keyword: string): ComparisonTask[] {
  const query = keyword.trim().toLowerCase();
  return latestCompletedRooms(tasks).filter(task => field === "all" ||
    String(field === "assigned_name" ? task.assigned_name || "" : task.room_name).toLowerCase().includes(query));
}
