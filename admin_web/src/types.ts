export type UserRole = "admin" | "worker";

export interface AdminUser {
  id: number;
  wx_openid: string;
  name: string;
  role: UserRole;
  admin_scope?: "full" | "collaborator";
  area_name: string;
  phone?: string;
  employee_no?: string;
  is_active: number;
}

export interface TaskRecord {
  id: number;
  device_name: string;
  expected_rack_no?: string;
  expected_model?: string;
  expected_barcode?: string;
  resource_editable?: boolean;
  resource_unavailable_reason?: string;
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
  has_admin_correction?: boolean;
  correction_count?: number;
  latest_correction_id?: number;
  correction_reason?: string;
  correction_note?: string;
  corrected_by?: number;
  corrected_by_name?: string;
  corrected_at?: string;
  original_actual_model?: string;
  original_asset_barcodes?: string;
  original_asset_no_barcode?: boolean | number;
  original_cabinet_barcodes?: string;
  original_cabinet_no_barcode?: boolean | number;
  original_rack_no?: string;
  original_notes?: string;
  original_record_type?: string;
  original_missing_reason?: string;
}

export interface CabinetRecord {
  id: number | string;
  task_id: number;
  room_name: string;
  area_name?: string;
  cabinet_type: "equipment" | "odf" | "iodf" | string;
  cabinet_type_label?: string;
  cabinet_barcode?: string;
  no_barcode?: boolean | number;
  rack_no?: string;
  cabinet_name?: string;
  location?: string;
  source?: string;
  source_label?: string;
  notes?: string;
  created_by?: number | null;
  created_by_name?: string;
  scanned_at?: string;
  has_admin_correction?: boolean;
  corrected_at?: string;
}

export interface RecordCorrection {
  id: number;
  record_id: number;
  actual_model: string;
  asset_barcodes: string;
  asset_no_barcode: number;
  cabinet_barcodes: string;
  cabinet_no_barcode: number;
  rack_no: string;
  notes: string;
  record_type: string;
  missing_reason: string;
  correction_reason: string;
  correction_note: string;
  corrected_by?: number;
  corrected_by_name?: string;
  corrected_at: string;
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

export interface AssetInfoPreview {
  file_name: string;
  sheet_name: string;
  sheets: string[];
  headers: string[];
  row_count: number;
  barcode_candidates: string[];
}

export interface AssetInfoStatus {
  id: number;
  file_name: string;
  sheet_name: string;
  barcode_column: string;
  headers: string[];
  row_count: number;
  imported_by?: number | null;
  imported_by_name: string;
  imported_at: string;
  display_fields: string[];
}

export interface AssetInfoImportItem {
  id: number;
  file_name: string;
  sheet_name: string;
  barcode_column: string;
  row_count: number;
  imported_by_name: string;
  imported_at: string;
}

export interface AssetInfoQueryResult {
  found?: boolean;
  barcode: string;
  batch_id: number;
  barcode_column: string;
  imported_at: string;
  fields: Record<string, string>;
}

export interface AssetInfoLookupItem {
  barcode: string;
  found: boolean;
  fields: Record<string, string>;
}

export interface Task {
  room_code?: string;
  id: number;
  import_batch_id?: string;
  task_name: string;
  room_name: string;
  area_name?: string;
  status: "pending" | "doing" | "done" | string;
  superseded_by_task_id?: number | null;
  waiting_for_task_id?: number | null;
  replacement_notice?: string;
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
  cabinets?: CabinetRecord[];
  cabinet_summary?: {
    total: number;
    equipment: number;
    odf: number;
    iodf: number;
  };
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

export interface AiReportSummary {
  id: number; task_ids: number[]; analysis_scope: string; status: string;
  model_provider: string; model_name: string; created_at: string; created_by_name?: string;
}

export interface AiSavedReport extends AiReportSummary {
  analysis_items: Array<Record<string, string>>; error_message: string; input_summary: string;
  reused?: boolean;
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
  source_type?: "ai" | "rule" | string;
  source_key?: string;
  status: "pending_review" | "dispatched" | "pending_verify" | "done" | "rejected" | string;
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
  verified_name?: string;
  verification_note?: string;
  verified_at?: string;
  reminder_count?: number;
  reminded_at?: string;
  reminder_note?: string;
  reminded_by?: number;
}

export interface ReconciliationIssue {
  issue_key: string;
  task_id: number;
  import_batch_id?: string;
  record_id: number;
  source_task_ids: number[];
  task_name: string;
  room_name: string;
  area_name: string;
  device_name: string;
  issue_type: string;
  category: string;
  basis: string;
  suggestion: string;
  source_value: string;
  field_value: string;
  risk_level: string;
  priority: string;
  can_create_adjustment: boolean;
  scanned_at?: string;
}

export interface ReconciliationResult {
  summary: {
    task_count: number;
    complete_task_count: number;
    device_count: number;
    matched_count: number;
    issue_count: number;
    actionable_count: number;
    waiting_count: number;
    batch_complete: boolean;
  };
  issues: ReconciliationIssue[];
  rules: string[];
}
