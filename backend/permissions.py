"""协作管理员只开放盘点查看、比对及资料修正，新增接口默认不授权。"""

import re


_COLLABORATOR_READ_PATHS = (
    r"/api/admin/me",
    r"/api/me/profile",
    r"/api/tasks(?:/(?:area|my))?",
    r"/api/tasks/\d+/items",
    r"/api/report/\d+",
    r"/api/summary",
    r"/api/export/\d+",
    r"/api/asset-info/(?:status|query)",
    r"/api/qr-assets(?:/\d+(?:/qrcode)?|/import-template)?",
    r"/api/audit_logs",
    r"/api/admin/records/\d+/corrections",
    r"/api/admin/records/\d+/resource",
    r"/api/reconciliation(?:/export|/cases/\d+)?",
    r"/api/ai/export/inventory",
    r"/api/ai/results(?:/\d+(?:/download)?|/latest/download)?",
    r"/api/ai/adjustments",
)

_COLLABORATOR_WRITE_PATHS = (
    ("POST", r"/api/admin/records/\d+/corrections"),
    ("POST", r"/api/admin/records/\d+/resource"),
    ("DELETE", r"/api/admin/records/\d+"),
    ("POST", r"/api/admin/tasks/\d+/racks"),
    ("PUT", r"/api/admin/tasks/\d+/racks/\d+"),
    ("DELETE", r"/api/admin/tasks/\d+/racks/\d+"),
    ("POST", r"/api/reconciliation/decisions"),
    ("POST", r"/api/reconciliation/tasks/\d+/(?:location|resources)"),
)


def admin_request_allowed(user: dict, method: str, path: str) -> bool:
    """权限取数据库当前值；完整管理员沿用原有权限，未知级别拒绝。"""
    scope = user.get("admin_scope", "full")
    if scope == "full":
        return True
    if scope != "collaborator":
        return False
    if method == "GET":
        return any(re.fullmatch(pattern, path) for pattern in _COLLABORATOR_READ_PATHS)
    return any(
        method == allowed_method and re.fullmatch(pattern, path)
        for allowed_method, pattern in _COLLABORATOR_WRITE_PATHS
    )
