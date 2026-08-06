export type UserRole = "admin" | "worker";

export interface AdminUser {
  id: number;
  wx_openid: string;
  name: string;
  role: UserRole;
  area_name: string;
  phone?: string;
  employee_no?: string;
  is_active: number;
}

export interface TaskRecord {
  id: number;
  device_name: string;
  expected_model?: string;
  expected_barcode?: string;
  actual_model?: string;
  asset_barcodes?: string;
  asset_no_barcode?: boolean | number;
  cabinet_barcodes?: string;
  cabinet_no_barcode?: boolean | number;
  rack_no?: string;
  match_status?: string;
  asset_match_status?: string;
  model_match_status?: string;
  scanned_at?: string;
  record_type?: string;
  field_status?: string;
  missing_reason?: string;
  notes?: string;
}

export interface QrAssetBinding {
  id?: number;
  device_name: string;
  device_ref?: string;
  room_name?: string;
  bound_at?: string;
  unbound_at?: string | null;
  changed_by?: number | null;
  changed_by_name?: string;
  change_reason?: string;
}

export interface QrAsset {
  id: number;
  token: string;
  asset_barcode: string;
  barcode_model: string;
  room_name: string;
  current_device_name: string;
  current_device_ref: string;
  history: QrAssetBinding[];
}

export interface QrAssetForm {
  asset_barcode: string;
  barcode_model: string;
  room_name: string;
  current_device_name?: string;
  current_device_ref?: string;
  change_reason?: string;
}

export interface Task {
  id: number;
  task_name: string;
  room_name: string;
  area_name?: string;
  status: "pending" | "doing" | "done" | string;
  assigned_name?: string;
  claimed_name?: string;
  created_at?: string;
  assigned_at?: string;
  claimed_at?: string;
  completed_at?: string;
  field_completed_at?: string;
  field_status?: string;
  total?: number;
  scanned?: number;
  records?: TaskRecord[];
}

export interface PreviewIssue {
  级别: string;
  Excel行号: string | number;
  机房名称: string;
  设备名称: string;
  问题类型: string;
  处理建议: string;
}

export interface UploadPreview {
  valid: boolean;
  room_count: number;
  device_count: number;
  missing_barcode: number;
  rooms: Array<{ room_name: string; area_name?: string; device_count: number }>;
  issue_rows: PreviewIssue[];
}

export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  available: boolean;
  api_key_configured: boolean;
  model_provider: string;
  model_name: string;
  base_url: string;
  external_agent_url: string;
}

export interface AiAnalysisItem {
  问题类型?: string;
  涉及机房?: string;
  涉及设备?: string;
  风险等级?: string;
  判断依据?: string;
  整改建议?: string;
  优先级?: string;
}

export interface AiAnalysisResult {
  id: number;
  status: string;
  error_message?: string;
  analysis_items: AiAnalysisItem[];
  input_summary: string;
  model_provider: string;
  model_name: string;
  adjustment_drafts?: AIAdjustmentTask[];
}

export interface AiChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AiChatResponse {
  answer: string;
  status?: string;
  pending_tool_calls?: Array<{ id?: string; name?: string; arguments?: Record<string, unknown> }>;
  model_provider: string;
  model_name: string;
  input_summary: string;
  context_meta?: Record<string, unknown>;
}

export interface AIAdjustmentTask {
  id: number;
  analysis_id: number;
  source_item_index: number;
  source_task_ids?: number[];
  problem_type: string;
  room_name: string;
  device_name: string;
  area_name: string;
  risk_level: string;
  basis: string;
  suggestion: string;
  priority: string;
  status: "pending_review" | "dispatched" | "done" | "rejected" | string;
  assigned_to?: number;
  assigned_name?: string;
  reviewed_name?: string;
  completed_name?: string;
  review_note?: string;
  completion_note?: string;
  created_at?: string;
  reviewed_at?: string;
  dispatched_at?: string;
  completed_at?: string;
  reminder_count?: number;
  reminded_at?: string;
  reminder_note?: string;
  reminded_by?: number;
}
