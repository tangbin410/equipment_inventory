"""Stable QR asset records and transmission-device binding history."""

from __future__ import annotations

import html
import secrets
import sqlite3
from datetime import datetime
from typing import Any

from .database import dict_from_row, get_conn, normalize_barcode


def _now() -> str:
    return datetime.now().isoformat()


def _new_token(conn: sqlite3.Connection) -> str:
    for _ in range(5):
        token = secrets.token_urlsafe(18)
        exists = conn.execute("SELECT 1 FROM qr_assets WHERE token = ?", (token,)).fetchone()
        if exists is None:
            return token
    raise RuntimeError("无法生成唯一二维码标识")


def _history(conn: sqlite3.Connection, asset_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, device_name, device_ref, room_name, bound_at, unbound_at,
                  changed_by, change_reason
           FROM qr_asset_bindings
           WHERE qr_asset_id = ?
           ORDER BY bound_at DESC, id DESC""",
        (asset_id,),
    ).fetchall()
    return [dict_from_row(row) for row in rows]


def _with_history(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
    asset = dict_from_row(row)
    if asset is None:
        return None
    asset["history"] = _history(conn, asset["id"])
    return asset


def list_qr_assets(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        needle = f"%{query.strip()}%"
        rows = conn.execute(
            """SELECT * FROM qr_assets
               WHERE is_active = 1
                 AND (? = '' OR asset_barcode LIKE ? OR barcode_model LIKE ?
                      OR room_name LIKE ? OR current_device_name LIKE ?)
               ORDER BY updated_at DESC, id DESC
               LIMIT ?""",
            (query.strip(), needle, needle, needle, needle, max(1, min(limit, 500))),
        ).fetchall()
        return [_with_history(conn, row) for row in rows]
    finally:
        conn.close()


def get_qr_asset(asset_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        return _with_history(conn, conn.execute(
            "SELECT * FROM qr_assets WHERE id = ? AND is_active = 1", (asset_id,)
        ).fetchone())
    finally:
        conn.close()


def get_qr_asset_by_token(token: str) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        return _with_history(conn, conn.execute(
            "SELECT * FROM qr_assets WHERE token = ? AND is_active = 1", (token.strip(),)
        ).fetchone())
    finally:
        conn.close()


def create_qr_asset(
    asset_barcode: str,
    barcode_model: str,
    room_name: str,
    current_device_name: str = "",
    current_device_ref: str = "",
    created_by: int | None = None,
) -> dict[str, Any]:
    barcode = normalize_barcode(asset_barcode)
    model = barcode_model.strip()
    room = room_name.strip()
    device = current_device_name.strip()
    if not barcode:
        raise ValueError("资产条码不能为空")
    if not room:
        raise ValueError("所在机房不能为空")

    now = _now()
    conn = get_conn()
    try:
        token = _new_token(conn)
        cur = conn.execute(
            """INSERT INTO qr_assets
               (token, asset_barcode, barcode_model, room_name,
                current_device_name, current_device_ref, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, barcode, model, room, device, current_device_ref.strip(), created_by, now, now),
        )
        asset_id = cur.lastrowid
        if device:
            conn.execute(
                """INSERT INTO qr_asset_bindings
                   (qr_asset_id, device_name, device_ref, room_name, bound_at, changed_by, change_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, device, current_device_ref.strip(), room, now, created_by, "初始绑定"),
            )
        conn.commit()
        return _with_history(conn, conn.execute("SELECT * FROM qr_assets WHERE id = ?", (asset_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "asset_barcode" in str(exc):
            raise ValueError("资产条码已存在") from exc
        raise
    finally:
        conn.close()


def update_qr_asset(
    asset_id: int,
    asset_barcode: str | None = None,
    barcode_model: str | None = None,
    room_name: str | None = None,
    current_device_name: str | None = None,
    current_device_ref: str | None = None,
    changed_by: int | None = None,
    change_reason: str = "",
) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM qr_assets WHERE id = ? AND is_active = 1", (asset_id,)
        ).fetchone()
        if current is None:
            conn.rollback()
            return None
        new_device_name = current_device_name.strip() if current_device_name is not None else current["current_device_name"]
        if current_device_ref is not None:
            new_device_ref = current_device_ref.strip()
        elif current_device_name is not None and new_device_name != (current["current_device_name"] or ""):
            new_device_ref = ""
        else:
            new_device_ref = current["current_device_ref"]
        values = {
            "asset_barcode": normalize_barcode(asset_barcode) if asset_barcode is not None else current["asset_barcode"],
            "barcode_model": barcode_model.strip() if barcode_model is not None else current["barcode_model"],
            "room_name": room_name.strip() if room_name is not None else current["room_name"],
            "current_device_name": new_device_name,
            "current_device_ref": new_device_ref,
        }
        if not values["asset_barcode"]:
            raise ValueError("资产条码不能为空")
        if not values["room_name"]:
            raise ValueError("所在机房不能为空")
        now = _now()
        device_changed = (
            values["current_device_name"] != (current["current_device_name"] or "")
            or values["current_device_ref"] != (current["current_device_ref"] or "")
        )
        if device_changed:
            conn.execute(
                """UPDATE qr_asset_bindings SET unbound_at = ?
                   WHERE qr_asset_id = ? AND unbound_at IS NULL""",
                (now, asset_id),
            )
            if values["current_device_name"]:
                conn.execute(
                    """INSERT INTO qr_asset_bindings
                       (qr_asset_id, device_name, device_ref, room_name, bound_at,
                        changed_by, change_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        asset_id,
                        values["current_device_name"],
                        values["current_device_ref"],
                        values["room_name"],
                        now,
                        changed_by,
                        change_reason.strip() or "设备关联变更",
                    ),
                )
        else:
            conn.execute(
                """UPDATE qr_asset_bindings SET room_name = ?
                   WHERE qr_asset_id = ? AND unbound_at IS NULL""",
                (values["room_name"], asset_id),
            )
        conn.execute(
            """UPDATE qr_assets
               SET asset_barcode = ?, barcode_model = ?, room_name = ?,
                   current_device_name = ?, current_device_ref = ?, updated_at = ?
               WHERE id = ?""",
            (
                values["asset_barcode"], values["barcode_model"], values["room_name"],
                values["current_device_name"], values["current_device_ref"], now, asset_id,
            ),
        )
        conn.commit()
        return _with_history(conn, conn.execute("SELECT * FROM qr_assets WHERE id = ?", (asset_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "asset_barcode" in str(exc):
            raise ValueError("资产条码已存在") from exc
        raise
    finally:
        conn.close()


def rebind_qr_asset(
    asset_id: int,
    device_name: str,
    device_ref: str = "",
    changed_by: int | None = None,
    change_reason: str = "",
) -> dict[str, Any] | None:
    return update_qr_asset(
        asset_id,
        current_device_name=device_name,
        current_device_ref=device_ref,
        changed_by=changed_by,
        change_reason=change_reason,
    )


def delete_qr_asset(asset_id: int) -> bool:
    """Soft-delete an asset while permanently reserving its token."""
    conn = get_conn()
    try:
        now = _now()
        cur = conn.execute(
            """UPDATE qr_assets SET is_active = 0, updated_at = ?
               WHERE id = ? AND is_active = 1""",
            (now, asset_id),
        )
        if cur.rowcount:
            conn.execute(
                """UPDATE qr_asset_bindings SET unbound_at = ?
                   WHERE qr_asset_id = ? AND unbound_at IS NULL""",
                (now, asset_id),
            )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def create_qr_asset_history(
    asset_id: int,
    device_name: str,
    room_name: str = "",
    bound_at: str = "",
    unbound_at: str = "",
    change_reason: str = "",
    changed_by: int | None = None,
) -> dict[str, Any] | None:
    """Add an ended historical association without changing the current device."""
    device = device_name.strip()
    if not device:
        raise ValueError("历史关联设备名称不能为空")
    conn = get_conn()
    try:
        asset = conn.execute(
            "SELECT * FROM qr_assets WHERE id = ? AND is_active = 1", (asset_id,)
        ).fetchone()
        if asset is None:
            return None
        cur = conn.execute(
            """INSERT INTO qr_asset_bindings
               (qr_asset_id, device_name, room_name, bound_at, unbound_at,
                changed_by, change_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_id,
                device,
                room_name.strip() or asset["room_name"],
                bound_at.strip(),
                unbound_at.strip() or _now(),
                changed_by,
                change_reason.strip() or "管理员补录历史记录",
            ),
        )
        conn.execute("UPDATE qr_assets SET updated_at = ? WHERE id = ?", (_now(), asset_id))
        conn.commit()
        return dict_from_row(conn.execute(
            "SELECT * FROM qr_asset_bindings WHERE id = ?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


def update_qr_asset_history(
    asset_id: int,
    history_id: int,
    device_name: str | None = None,
    room_name: str | None = None,
    bound_at: str | None = None,
    unbound_at: str | None = None,
    change_reason: str | None = None,
) -> dict[str, Any] | None:
    """Edit an ended history row; the active current binding is protected."""
    conn = get_conn()
    try:
        current = conn.execute(
            """SELECT b.* FROM qr_asset_bindings b
               JOIN qr_assets a ON a.id = b.qr_asset_id
               WHERE b.id = ? AND b.qr_asset_id = ? AND b.unbound_at IS NOT NULL
                 AND a.is_active = 1""",
            (history_id, asset_id),
        ).fetchone()
        if current is None:
            return None
        values = {
            "device_name": device_name.strip() if device_name is not None else current["device_name"],
            "room_name": room_name.strip() if room_name is not None else current["room_name"],
            "bound_at": bound_at.strip() if bound_at is not None else current["bound_at"],
            "unbound_at": unbound_at.strip() if unbound_at is not None else current["unbound_at"],
            "change_reason": change_reason.strip() if change_reason is not None else current["change_reason"],
        }
        if not values["device_name"]:
            raise ValueError("历史关联设备名称不能为空")
        if not values["room_name"]:
            raise ValueError("历史记录所在机房不能为空")
        if not values["unbound_at"]:
            raise ValueError("历史记录结束时间不能为空")
        conn.execute(
            """UPDATE qr_asset_bindings
               SET device_name = ?, room_name = ?, bound_at = ?, unbound_at = ?, change_reason = ?
               WHERE id = ? AND qr_asset_id = ?""",
            (
                values["device_name"], values["room_name"], values["bound_at"],
                values["unbound_at"], values["change_reason"], history_id, asset_id,
            ),
        )
        conn.execute("UPDATE qr_assets SET updated_at = ? WHERE id = ?", (_now(), asset_id))
        conn.commit()
        return dict_from_row(conn.execute(
            "SELECT * FROM qr_asset_bindings WHERE id = ?", (history_id,)
        ).fetchone())
    finally:
        conn.close()


def delete_qr_asset_history(asset_id: int, history_id: int) -> bool:
    """Delete only an ended history row; active bindings cannot be deleted here."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """DELETE FROM qr_asset_bindings
               WHERE id = ? AND qr_asset_id = ? AND unbound_at IS NOT NULL""",
            (history_id, asset_id),
        )
        if cur.rowcount:
            conn.execute("UPDATE qr_assets SET updated_at = ? WHERE id = ?", (_now(), asset_id))
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def batch_import_qr_assets(records: list[dict[str, Any]], created_by: int | None = None) -> list[dict[str, Any]]:
    """Create many independent QR assets in one transaction."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, record in enumerate(records, start=2):
            barcode = normalize_barcode(str(record.get("asset_barcode") or ""))
            room = str(record.get("room_name") or "").strip()
            if not barcode:
                raise ValueError(f"第 {index} 行资产条码不能为空")
            if not room:
                raise ValueError(f"第 {index} 行条码所在机房不能为空")
            if barcode in seen:
                raise ValueError(f"第 {index} 行资产条码重复：{barcode}")
            if conn.execute("SELECT 1 FROM qr_assets WHERE asset_barcode = ?", (barcode,)).fetchone():
                raise ValueError(f"第 {index} 行资产条码已存在：{barcode}")
            seen.add(barcode)
            prepared.append({
                "asset_barcode": barcode,
                "barcode_model": str(record.get("barcode_model") or "").strip(),
                "room_name": room,
                "current_device_name": str(record.get("current_device_name") or "").strip(),
                "history_device_names": [
                    str(name).strip() for name in (record.get("history_device_names") or []) if str(name).strip()
                ],
            })

        now = _now()
        created_ids: list[int] = []
        for record in prepared:
            token = _new_token(conn)
            cur = conn.execute(
                """INSERT INTO qr_assets
                   (token, asset_barcode, barcode_model, room_name,
                    current_device_name, current_device_ref, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (
                    token, record["asset_barcode"], record["barcode_model"], record["room_name"],
                    record["current_device_name"], created_by, now, now,
                ),
            )
            asset_id = cur.lastrowid
            created_ids.append(asset_id)
            for device_name in record["history_device_names"]:
                if device_name == record["current_device_name"]:
                    continue
                conn.execute(
                    """INSERT INTO qr_asset_bindings
                       (qr_asset_id, device_name, room_name, bound_at, unbound_at,
                        changed_by, change_reason)
                       VALUES (?, ?, ?, '', ?, ?, '批量导入历史记录')""",
                    (asset_id, device_name, record["room_name"], now, created_by),
                )
            if record["current_device_name"]:
                conn.execute(
                    """INSERT INTO qr_asset_bindings
                       (qr_asset_id, device_name, room_name, bound_at, changed_by, change_reason)
                       VALUES (?, ?, ?, ?, ?, '批量导入当前关联')""",
                    (asset_id, record["current_device_name"], record["room_name"], now, created_by),
                )
        conn.commit()
        return [
            _with_history(conn, conn.execute("SELECT * FROM qr_assets WHERE id = ?", (asset_id,)).fetchone())
            for asset_id in created_ids
        ]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def render_qr_png(public_url: str) -> bytes:
    """Render the stable public URL. The URL, not mutable asset data, is encoded."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - exercised only on incomplete installs
        raise RuntimeError("缺少 qrcode 依赖，请安装 requirements.txt") from exc
    image = qrcode.make(public_url)
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_public_html(asset: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value or ""))

    history_rows = "".join(
        "<tr>"
        f"<td>{esc(item['device_name'])}</td>"
        f"<td>{esc(item['room_name'])}</td>"
        f"<td>{esc(item['bound_at'])}</td>"
        f"<td>{esc(item['unbound_at'] or '当前')}</td>"
        f"<td>{esc(item['change_reason'])}</td>"
        "</tr>"
        for item in asset.get("history", [])
    ) or "<tr><td colspan='5'>暂无历史关联记录</td></tr>"
    current_device = esc(asset.get("current_device_name")) or "未绑定"
    return f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>资产信息</title><style>
body{{margin:0;background:#f4f7fb;color:#1f2d3d;font-family:Arial,'Microsoft YaHei',sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:20px 14px 40px}}h1{{font-size:22px;margin:0 0 16px}}
.card{{background:#fff;border:1px solid #e4eaf2;border-radius:8px;padding:16px;margin-bottom:14px;box-shadow:0 3px 12px rgba(31,45,61,.06)}}
.grid{{display:grid;grid-template-columns:120px 1fr;gap:10px 14px;font-size:14px}}.label{{color:#718096}}
.current{{font-size:18px;font-weight:700;color:#1677ff}}table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{border-bottom:1px solid #edf1f5;padding:9px 6px;text-align:left}}th{{color:#718096;font-weight:500}}
</style></head><body><main class='wrap'><h1>资产信息</h1>
<section class='card'><div class='grid'>
<div class='label'>资产条码</div><div>{esc(asset.get('asset_barcode'))}</div>
<div class='label'>条码型号</div><div>{esc(asset.get('barcode_model'))}</div>
<div class='label'>所在机房</div><div>{esc(asset.get('room_name'))}</div>
<div class='label'>当前传输设备</div><div class='current'>{current_device}</div>
</div></section><section class='card'><h2>历史关联设备</h2><table><thead><tr><th>设备</th><th>机房</th><th>开始时间</th><th>结束时间</th><th>变更原因</th></tr></thead><tbody>{history_rows}</tbody></table></section>
</main></body></html>"""
