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


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    """PC 后台新增小程序账号"""
    wx_openid: str = Field(..., description="登录编码/微信openid")
    name: str = Field(..., description="姓名")
    role: str = Field("worker", description="角色：admin/worker")
    area_name: str = Field("默认片区", description="可负责片区，多个片区用逗号分隔")
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


# ──────────────────────────────────────────────
# 响应模型
# ──────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    wx_openid: str = ""
    name: str
    role: str
    area_name: str = "默认片区"
    phone: str = ""
    employee_no: str = ""
    is_active: int = 1


class TaskOut(BaseModel):
    id: int
    task_name: str
    room_name: str
    area_name: str = "默认片区"
    status: str
    claimed_by: int | None = None
    claimed_name: str | None = None
    field_status: str = ""
    closure_status: str = ""
    review_pending: int = 0
    total: int
    scanned: int
    created_at: str


class UploadResponse(BaseModel):
    task_id: int
    task_name: str
    device_count: int
    message: str


class ScanResponse(BaseModel):
    success: bool
    record_id: int | None = None
    match_status: str
    asset_match_status: str = ""
    model_match_status: str = ""
    message: str
