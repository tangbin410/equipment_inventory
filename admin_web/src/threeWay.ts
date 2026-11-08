export type DecisionState = "待判断" | "一致" | "不一致" | "无法确认";
export const systems = ["综资", "现场", "资产"] as const;
export const decisionStates: DecisionState[] = ["待判断", "一致", "不一致", "无法确认"];
export interface SourceRow {
  system: string; barcode: string; name: string; location_code: string; model: string; rack_no?: string;
  entity_kind?: "device" | "cabinet" | "rack" | "unlinked"; rack_type?: string; resource_category?: string; asset_name?: string;
  barcode_state?: string;
  source_label?: string; revision_id?: number; updated_at?: string;
  task_id?: number; record_id?: number; room_name?: string;
}
export interface ComparisonGroup {
  group_key: string; barcode: string; manual: boolean; no_barcode?: boolean;
  pending_sources?: Record<string, SourceRow[]>; pending_association?: boolean;
  sources: Record<string, SourceRow[]>; fingerprint: string; model_fingerprint: string;
  scope_task_ids: number[]; task_ids: number[]; room_name: string; device_name: string; area_name: string;
  name_status: DecisionState | "待处理" | "待关联"; location_status: DecisionState | "待处理" | "待关联"; model_status: DecisionState;
  name_detail: string; location_detail: string; problems: string[];
  stage_one_status?: string;
  field_model_status: DecisionState; asset_model_status: DecisionState;
  checks: Record<string, Record<string, string>>;
  consistent: boolean; can_create_adjustment: boolean;
  manual_decision?: { values: Record<string, SourceRow>; pair_statuses?: Record<string,Record<string,DecisionState>>; note: string } | null;
  versions: {asset_batch_id: number | null; resources: Record<string, number>; field: Record<string, unknown>};
  history: Array<{id: number; kind: string; actor_id: number; actor_name?: string; created_at: string; source_fingerprint: string; decision: {note: string; status?: string}}>;
  case?: {id: number; status: string} | null;
}
export interface ResourceFieldRow {
  room_name: string; name: string; resource: SourceRow | null; field: SourceRow | null;
  feedback: SourceRow[]; status: string; barcode_status: string; rack_status: string; model_status: string; group_key?: string;
}
export interface ComparisonTask {
  id: number; room_name: string; room_code: string; import_batch_id: string; status: string; area_name: string;
  assigned_name?: string; created_at?: string; field_completed_at?: string; completed_at?: string;
}
export interface CabinetComparison {
  task_id: number; room_name: string; room_code: string; resource_name: string; position: string;
  resource_position: string; field_position: string; field_barcodes: string; asset_barcodes: string;
  asset_records: SourceRow[]; field_devices: string[]; resource_devices: string[];
  position_status: string; barcode_status: string; location_status: string; resource_barcode_status: string;
  consistent: boolean; status: string; problems: string[];
}
export interface ComparisonResult {
  tasks: ComparisonTask[]; stage_one: ResourceFieldRow[]; groups: ComparisonGroup[]; warnings: string[];
  cabinet_comparison: CabinetComparison[];
  rack_comparison: Array<{task_id: number; cabinet_id: number | null; room_name: string; room_code: string;
    rack_type: string; resource_name: string; field_barcode: string; field_location_code: string; field_status: string; asset_barcodes: string;
    asset_location_codes: string; asset_records: SourceRow[]; barcode_status: string; location_status: string; resource_status: string}>;
  unlinked_assets: SourceRow[]; scoped_assets: SourceRow[];
  asset_counts: {total: number; named: number; resource_names: number; device: number; cabinet: number; rack: number; unlinked: number};
  summary: {task_count: number; device_count: number; matched_count: number; issue_count: number; pending_model_count: number};
  asset_version: {id: number; file_name: string; imported_at: string} | null;
}
export interface ComparisonCase {
  id: number; status: string; problem_type: string; room_name: string; device_name: string;
  assigned_name?: string; assigned_to?: number; area_name: string; created_at: string;
  source_type: string; source_task_ids: number[]; basis: string; suggestion: string; completion_note: string;
  review_note: string; verification_note: string;
  comparison: {group_key: string; task_ids: number[]; snapshot: ComparisonGroup; accepted_snapshot?: ComparisonGroup;
    needs_field: boolean | null; needs_admin: boolean | null; field_done: boolean; admin_done: boolean; admin_note: string} | null;
  current?: ComparisonGroup | null; current_groups?: ComparisonGroup[]; warnings?: string[];
  history?: Array<{id: number; action: string; actor_id: number; actor_name?: string; created_at: string; data: {note?: string}}>;
}
export function statusTag(value: string): "success" | "danger" | "warning" | "info" {
  return value === "一致" ? "success" : value === "不一致" ? "danger" : value === "无法确认" ? "info" : "warning";
}

export function sourceRecords(group: ComparisonGroup, system: string): SourceRow[] {
  return [...group.sources[system], ...(group.pending_sources?.[system] || [])];
}
