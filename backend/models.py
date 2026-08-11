"""数据模型（Pydantic）。

定义 API 请求和响应的数据格式，自动做校验。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    area_name: str = Field("汇聚及核心", description="负责片区：汇聚及核心/南区/北区/西区")
    phone: str = Field("", description="手机号")
    employee_no: str = Field("", description="工号")
    is_active: bool = Field(True, description="是否启用")


class UserUpdateRequest(BaseModel):
    """PC 后台修改小程序账号"""
    wx_openid: str | None = None
    name: str | None = None
    role: str | None = None
    area_name: str | None = None
    phone: str | None = None
    employee_no: str | None = None
    is_active: bool | None = None


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
    completion_note: str = Field("", description="整改完成说明")


# ──────────────────────────────────────────────
# 响应模型
# ──────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    wx_openid: str = ""
    name: str
    role: str
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
    message: str
