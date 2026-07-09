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


class ScanSubmitRequest(BaseModel):
    """现场人员提交盘点结果"""
    record_id: int = Field(..., description="设备记录ID")
    asset_barcodes: str = Field("", description="扫码到的资产条码，多条用逗号分隔")
    asset_no_barcode: bool = Field(False, description="是否无条码")
    cabinet_barcodes: str = Field("", description="扫码到的机柜条码，多条用逗号分隔")
    cabinet_no_barcode: bool = Field(False, description="是否无条码")
    rack_no: str = Field("", description="现场设备所属机架号")
    actual_model: str = Field("", description="实际设备型号")
    notes: str = Field("", description="现场备注")


# ──────────────────────────────────────────────
# 响应模型
# ──────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    name: str
    role: str


class TaskOut(BaseModel):
    id: int
    task_name: str
    room_name: str
    status: str
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
    match_status: str
    asset_match_status: str = ""
    model_match_status: str = ""
    message: str
