"""数据模型（Pydantic）。

定义 API 请求和响应的数据格式，自动做校验。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ──────────────────────────────────────────────
# 请求模型
# ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    """小程序登录请求"""
    code: str


class ProfileBindRequest(BaseModel):
    """小程序现场人员资料绑定请求"""
    name: str = Field(..., description="现场人员姓名")
    phone: str = Field(..., description="现场人员手工填写的手机号")


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    """PC 后台新增小程序账号"""
    wx_openid: str = Field(..., description="登录编码/微信openid")
    name: str = Field(..., description="姓名")
    role: str = Field("worker", description="角色：admin/worker")
    admin_scope: Literal["full", "collaborator"] = Field("full", description="后台权限：完整管理员/协作管理员")
    password: str | None = Field(None, min_length=12, max_length=128, repr=False, description="新后台账号密码")
    area_name: str = Field("汇聚及核心", description="负责片区：汇聚及核心/南区/北区/西区")
    phone: str = Field("", description="手机号")
    employee_no: str = Field("", description="工号")
    is_active: bool = Field(True, description="是否启用")

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("密码不能只包含空格")
        return value


class UserUpdateRequest(BaseModel):
    """PC 后台修改小程序账号"""
    wx_openid: str | None = None
    name: str | None = None
    role: str | None = None
    admin_scope: Literal["full", "collaborator"] | None = None
    password: str | None = Field(None, min_length=12, max_length=128, repr=False)
    area_name: str | None = None
    phone: str | None = None
    employee_no: str | None = None
    is_active: bool | None = None

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("密码不能只包含空格")
        return value


class UserPasswordRequest(BaseModel):
    password: str = Field(..., min_length=12, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("密码不能只包含空格")
        return value


class QrAssetCreateRequest(BaseModel):
    """创建一个带固定二维码标识的资产。"""
    asset_barcode: str
    barcode_model: str = ""
    room_name: str
    current_device_name: str = ""
    current_device_ref: str = ""


class QrAssetUpdateRequest(BaseModel):
    """修改资产字段；二维码 token 不允许修改。"""
    asset_barcode: str | None = None
    barcode_model: str | None = None
    room_name: str | None = None
    current_device_name: str | None = None
    current_device_ref: str | None = None
    change_reason: str = ""


class QrAssetBindingRequest(BaseModel):
    """变更资产当前关联的传输设备。"""
    device_name: str = ""
    device_ref: str = ""
    change_reason: str = ""


class QrAssetHistoryCreateRequest(BaseModel):
    """补录一条已经结束的历史关联记录。"""
    device_name: str
    room_name: str = ""
    bound_at: str = ""
    unbound_at: str = ""
    change_reason: str = ""


class QrAssetHistoryUpdateRequest(BaseModel):
    """修订一条已经结束的历史关联记录。"""
    device_name: str | None = None
    room_name: str | None = None
    bound_at: str | None = None
    unbound_at: str | None = None
    change_reason: str | None = None


class ScanSubmitRequest(BaseModel):
    """现场人员提交盘点结果"""
    client_request_id: str = Field("", description="客户端提交幂等ID")
    record_id: int = Field(..., description="设备记录ID")
    device_name: str | None = Field(None, max_length=500, description="仅允许修改现场盘盈设备名称")
    asset_barcodes: str = Field("", description="扫码到的资产条码，多条用逗号分隔")
    asset_no_barcode: bool = Field(False, description="是否无条码")
    cabinet_barcodes: str = Field("", description="扫码到的机柜条码，多条用逗号分隔")
    cabinet_no_barcode: bool = Field(False, description="是否无条码")
    rack_no: str = Field("", description="现场设备所属机架号")
    actual_model: str = Field("", description="实际设备型号")
    notes: str = Field("", description="现场备注")


class MissingSubmitRequest(BaseModel):
    """现场人员提交盘亏结果"""
    client_request_id: str = Field("", description="客户端提交幂等ID")
    reason: str = Field(..., description="盘亏原因")
    notes: str = Field("", description="现场备注")


class TaskListExportRequest(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=10000)


class InventoryPairCreateRequest(BaseModel):
    missing_record_id: int = Field(..., gt=0)
    surplus_record_id: int = Field(..., gt=0)
    missing_fingerprint: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    surplus_fingerprint: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    note: str = Field(..., min_length=1, max_length=1000)


class InventoryPairCancelRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=1000)


class AdminRecordCorrectionRequest(BaseModel):
    """PC 管理员修正小程序现场反馈，不覆盖原始记录。"""
    actual_model: str = Field("", max_length=200, description="修正后的现场设备型号")
    device_name: str | None = Field(None, max_length=500, description="仅允许更正盘盈设备名称")
    asset_barcodes: str = Field("", max_length=2000, description="修正后的现场资产条码集合")
    asset_no_barcode: bool = Field(False, description="修正后是否无资产条码")
    cabinet_barcodes: str = Field("", max_length=2000, description="修正后的现场机柜条码")
    cabinet_no_barcode: bool = Field(False, description="修正后是否无机柜条码")
    rack_no: str = Field("", max_length=200, description="修正后的现场机架号")
    notes: str = Field("", max_length=1000, description="修正后的现场备注")
    record_type: str = Field("normal", description="normal/missing/surplus")
    missing_reason: str = Field("", max_length=500, description="现场未找到原因")
    correction_reason: str = Field(..., max_length=100, description="管理员修正原因")
    correction_note: str = Field("", max_length=500, description="管理员修正说明")


class SurplusSubmitRequest(BaseModel):
    """现场人员登记盘盈设备"""
    client_request_id: str = Field("", description="客户端提交幂等ID")
    task_id: int = Field(..., description="任务ID")
    device_name: str = Field(..., description="现场多出设备名称")
    actual_model: str = Field("", description="现场设备型号")
    asset_barcodes: str = Field("", description="扫描到的资产条码，多个用逗号分隔")
    asset_no_barcode: bool = Field(False, description="是否无资产条码")
    cabinet_barcodes: str = Field("", description="扫描到的机柜条码，多个用逗号分隔")
    cabinet_no_barcode: bool = Field(False, description="是否无机柜条码")
    rack_no: str = Field("", description="现场设备所属机架号")
    notes: str = Field("", description="现场备注")
    created_by: int | None = Field(None, description="登记人ID")


class CabinetCreateRequest(BaseModel):
    """现场单独登记 ODF 或 IODF 机架；传输综合柜随设备提交。"""
    client_request_id: str = Field("", description="客户端提交幂等ID")
    task_id: int = Field(..., description="任务ID")
    cabinet_type: str = Field(..., description="机架类型：odf/iodf")
    cabinet_barcode: str = Field("", description="机架条码；勾选无条码时为空")
    no_barcode: bool = Field(False, description="是否无机架条码")
    rack_no: str = Field(..., description="资源名称／机架编号")
    notes: str = Field("", description="现场备注")


class AdminRackCorrectionRequest(BaseModel):
    cabinet_type: str = Field(..., description="机架类型：odf/iodf")
    cabinet_barcode: str = Field("", max_length=2000)
    no_barcode: bool = False
    rack_no: str = Field(..., max_length=500)
    notes: str = Field("", max_length=1000)
    correction_reason: str = Field(..., max_length=500)


class CaptureVoidRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class AIAnalysisRequest(BaseModel):
    """PC 后台发起 AI 模型分析"""
    task_ids: list[int] = Field(..., description="需要合并分析的任务ID列表")


class AIChatMessage(BaseModel):
    """AI 对话历史消息"""
    role: str = Field(..., description="user/assistant")
    content: str = Field(..., description="消息内容")


class AIChatRequest(BaseModel):
    """PC 后台向本地 agent 发起只读对话"""
    message: str = Field(..., description="管理员问题")
    task_ids: list[int] = Field(default_factory=list, description="对话关联的任务ID列表")
    history: list[AIChatMessage] = Field(default_factory=list, description="最近对话历史")


class AIAdjustmentDispatchRequest(BaseModel):
    """管理员审核通过并下发 AI 整改任务"""
    assigned_to: int = Field(..., description="下发给现场人员ID")
    review_note: str = Field("", description="管理员审核说明")


class AIAdjustmentRejectRequest(BaseModel):
    """管理员驳回 AI 整改草稿"""
    review_note: str = Field("", description="驳回说明")


class AIAdjustmentReminderRequest(BaseModel):
    """管理员记录 AI 整改任务的系统内催办"""
    reminder_note: str = Field("", max_length=200, description="系统内催办说明")


class AdjustmentCompleteRequest(BaseModel):
    """现场人员确认整改完成"""
    completion_note: str = Field("", max_length=1500, description="整改完成说明")
    records: list[AdjustmentRecordFeedback] = Field(default_factory=list, max_length=100)
    new_record: AdjustmentNewFeedback | None = None


class AdjustmentVerifyRequest(BaseModel):
    """管理员验收现场整改结果"""
    verification_note: str = Field("", max_length=500, description="管理员验收说明")


class ManualRecordAdjustmentRequest(BaseModel):
    problem_type: str = Field(..., min_length=1, max_length=100)
    basis: str = Field(..., min_length=1, max_length=1000)
    suggestion: str = Field(..., min_length=1, max_length=1000)
    risk_level: Literal["高", "中", "低"] = "高"
    priority: Literal["P1", "P2", "P3"] = "P1"


# ──────────────────────────────────────────────
# 响应模型
# ──────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    wx_openid: str = ""
    name: str
    role: str
    admin_scope: Literal["full", "collaborator"] = "full"
    area_name: str = "汇聚及核心"
    phone: str = ""
    employee_no: str = ""
    is_active: int = 1


class ScanResponse(BaseModel):
    success: bool
    record_id: int | None = None
    match_status: str
    asset_match_status: str = ""
    model_match_status: str = ""
    record_type: str = "normal"
    message: str
    warnings: list[str] = Field(default_factory=list)


class ComparisonRequest(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=300)
    group_key: str = Field(..., min_length=1, max_length=500)
    fingerprint: str = Field(..., min_length=64, max_length=64)


DecisionStatus = Literal['待判断', '一致', '不一致', '无法确认']


class ModelDecision(BaseModel):
    field_status: DecisionStatus
    asset_status: DecisionStatus
    note: str = Field(..., min_length=1, max_length=1000)


class ManualSourceValues(BaseModel):
    barcode: str = Field(..., min_length=1, max_length=2000)
    name: str = Field(..., min_length=1, max_length=1000)
    location_code: str = Field(..., min_length=1, max_length=1000)
    model: str = Field(..., min_length=1, max_length=1000)


class ManualComparisonDecision(BaseModel):
    values: dict[Literal['资产', '综资', '现场'], ManualSourceValues]
    pair_statuses: dict[str, dict[str, DecisionStatus]]
    note: str = Field(..., min_length=1, max_length=1000)


class ComparisonDecisionRequest(ComparisonRequest):
    kind: Literal['model', 'manual']
    decision: ModelDecision | ManualComparisonDecision


class ComparisonCaseActionRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=1500)
    needs_field: bool = False
    needs_admin: bool = False
    assigned_to: int | None = None
    return_field: bool = False
    return_admin: bool = False


class LocationCodeRequest(BaseModel):
    location_code: str = Field(..., min_length=1, max_length=200)


class AdminResourceCorrectionRequest(BaseModel):
    expected_model: str = Field(..., max_length=200)
    expected_barcode: str = Field(..., max_length=2000)
    expected_rack_no: str = Field(..., max_length=200)
    fingerprint: str = Field(..., min_length=64, max_length=64)
    note: str = Field("", max_length=500)


class AdjustmentRecordFeedback(BaseModel):
    record_id: int
    actual_model: str = Field(..., max_length=200)
    asset_barcodes: str = Field(..., max_length=2000)
    cabinet_barcodes: str | None = Field(None, max_length=2000)
    cabinet_no_barcode: bool | None = None
    rack_no: str | None = Field(None, max_length=200)


class AdjustmentNewFeedback(BaseModel):
    task_id: int
    device_name: str = Field(..., min_length=1, max_length=200)
    actual_model: str = Field(..., min_length=1, max_length=200)
    asset_barcodes: str = Field(..., min_length=1, max_length=2000)
    cabinet_barcodes: str = Field('', max_length=2000)
    cabinet_no_barcode: bool = False
    rack_no: str = Field(..., min_length=1, max_length=200)
