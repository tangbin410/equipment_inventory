import type { AiAnalysisResult, PreviewIssue, Task, TaskRecord } from "./types";

export function formatTime(value?: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function taskStage(task: Task): string {
  if (task.status === "pending") return "待领取";
  if (task.status === "doing") return "盘点中";
  if (task.status === "done") return "现场完成";
  return task.status || "-";
}

export function boolText(value: unknown): string {
  return value ? "是" : "否";
}

export function isOverdue(task: Task, days = 7): boolean {
  const started = task.claimed_at || task.assigned_at;
  if (!started || task.status === "done") return false;
  const startedAt = new Date(started);
  return !Number.isNaN(startedAt.getTime()) && Date.now() - startedAt.getTime() > days * 86400000;
}

export function downloadWorkbook(filename: string, sheets: Record<string, Record<string, unknown>[]>) {
  const html = Object.entries(sheets).map(([sheetName, rows]) => {
    const headers = Object.keys(rows[0] || {});
    const headerHtml = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("");
    const bodyHtml = rows.map((row) => (
      `<tr>${headers.map((header) => `<td>${escapeHtml(row[header])}</td>`).join("")}</tr>`
    )).join("");
    return `<h3>${escapeHtml(sheetName)}</h3><table border="1"><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
  }).join("<br/>");
  const blob = new Blob([`\ufeff<html><head><meta charset="utf-8"></head><body>${html}</body></html>`], {
    type: "application/vnd.ms-excel;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.replace(/\.xlsx$/i, ".xls");
  link.click();
  URL.revokeObjectURL(url);
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function inventoryRows(tasks: Task[]) {
  const rows: Record<string, unknown>[] = [];
  tasks.forEach((task) => {
    (task.records || []).forEach((record: TaskRecord) => {
      rows.push({
        任务ID: task.id,
        机房名称: task.room_name || "",
        片区: task.area_name || "汇聚及核心",
        领取人: task.assigned_name || "-",
        任务状态: taskStage(task),
        现场完成时间: formatTime(task.field_completed_at || task.completed_at),
        设备名称: record.device_name || "",
        综资设备型号: record.expected_model || "",
        综资资产条码: record.expected_barcode || "",
        综资机架号: record.expected_rack_no || "",
        现场设备型号: record.actual_model || "",
        现场资产条码: record.asset_barcodes || (record.asset_no_barcode ? "无条码" : ""),
        现场机架号: record.rack_no || "",
        现场资产缺条码: boolText(record.asset_no_barcode),
        现场机柜条码: record.cabinet_barcodes || (record.cabinet_no_barcode ? "无条码" : ""),
        现场机柜缺条码: boolText(record.cabinet_no_barcode),      
        现场状态: record.field_status || "正常",
        盘点时间: formatTime(record.scanned_at),
        盘亏原因: record.missing_reason || "",
        现场备注: record.notes || "",
        任务名称: task.task_name || "",
        任务创建时间: formatTime(task.created_at),
        领取时间: formatTime(task.assigned_at || task.claimed_at),
      });
    });
  });
  return rows;
}

export function downloadPreviewIssues(rows: PreviewIssue[]) {
  downloadWorkbook("预检异常明细.xls", { 预检异常明细: rows as unknown as Record<string, unknown>[] });
}

export function downloadAiReport(result: AiAnalysisResult) {
  const rows = (result.analysis_items || []).map((item) => ({
    问题类型: item.问题类型 || "",
    涉及机房: item.涉及机房 || "",
    涉及设备: item.涉及设备 || "",
    风险等级: item.风险等级 || "",
    判断依据: item.判断依据 || "",
    整改建议: item.整改建议 || "",
    优先级: item.优先级 || "",
  }));
  downloadWorkbook(`AI分析报告_${result.id || Date.now()}.xls`, {
    报告信息: [
      { 字段: "报告ID", 值: result.id },
      { 字段: "状态", 值: result.status },
      { 字段: "输入摘要", 值: result.input_summary },
      { 字段: "模型来源", 值: result.model_provider },
      { 字段: "模型名称", 值: result.model_name },
    ],
    问题清单: rows,
  });
}
