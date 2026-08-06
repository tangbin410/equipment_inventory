import tempfile
import unittest
from pathlib import Path

from backend import database
from backend.asset_qr import (
    batch_import_qr_assets,
    create_qr_asset,
    create_qr_asset_history,
    delete_qr_asset,
    delete_qr_asset_history,
    get_qr_asset_by_token,
    rebind_qr_asset,
    update_qr_asset,
    update_qr_asset_history,
)


class QrAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rebind_keeps_token_and_preserves_history(self):
        asset = create_qr_asset(
            asset_barcode=" asset-001 ",
            barcode_model="标签",
            room_name="核心机房A",
            current_device_name="A设备",
        )
        token = asset["token"]

        changed = rebind_qr_asset(
            asset["id"],
            device_name="B设备",
            device_ref="NE-002",
            change_reason="设备替换",
        )
        self.assertEqual(changed["token"], token)
        self.assertEqual(changed["current_device_name"], "B设备")
        self.assertEqual(len(changed["history"]), 2)
        current = next(item for item in changed["history"] if item["unbound_at"] is None)
        old = next(item for item in changed["history"] if item["device_name"] == "A设备")
        self.assertEqual(current["device_name"], "B设备")
        self.assertIsNotNone(old["unbound_at"])

        updated = update_qr_asset(asset["id"], asset_barcode="asset-002", room_name="核心机房B")
        self.assertEqual(updated["token"], token)
        self.assertEqual(updated["asset_barcode"], "ASSET-002")
        self.assertEqual(updated["history"][0]["room_name"], "核心机房B")
        public = get_qr_asset_by_token(token)
        self.assertEqual(public["current_device_name"], "B设备")

    def test_batch_import_and_multi_field_update(self):
        assets = batch_import_qr_assets([
            {
                "asset_barcode": "asset-101",
                "barcode_model": "标签A",
                "room_name": "机房A",
                "current_device_name": "C设备",
                "history_device_names": ["A设备", "B设备"],
            },
            {
                "asset_barcode": "asset-102",
                "barcode_model": "标签B",
                "room_name": "机房B",
                "current_device_name": "D设备",
                "history_device_names": [],
            },
        ])
        self.assertEqual(len(assets), 2)
        self.assertNotEqual(assets[0]["token"], assets[1]["token"])
        self.assertEqual(len(assets[0]["history"]), 3)

        token = assets[0]["token"]
        changed = update_qr_asset(
            assets[0]["id"],
            asset_barcode="asset-201",
            barcode_model="新标签",
            room_name="新机房",
            current_device_name="E设备",
            change_reason="批量字段修订",
        )
        self.assertEqual(changed["token"], token)
        self.assertEqual(changed["asset_barcode"], "ASSET-201")
        self.assertEqual(changed["barcode_model"], "新标签")
        self.assertEqual(changed["room_name"], "新机房")
        self.assertEqual(changed["current_device_name"], "E设备")
        self.assertEqual(len(changed["history"]), 4)

    def test_asset_and_history_crud_keep_token_reserved(self):
        asset = create_qr_asset("asset-301", "标签", "机房", "当前设备")
        history = create_qr_asset_history(asset["id"], "旧设备", change_reason="补录")
        self.assertEqual(history["device_name"], "旧设备")

        updated = update_qr_asset_history(
            asset["id"], history["id"], device_name="修订后的旧设备", room_name="旧机房"
        )
        self.assertEqual(updated["device_name"], "修订后的旧设备")
        self.assertEqual(updated["room_name"], "旧机房")

        detail = get_qr_asset_by_token(asset["token"])
        active = next(item for item in detail["history"] if item["unbound_at"] is None)
        self.assertIsNone(update_qr_asset_history(asset["id"], active["id"], device_name="不允许"))
        self.assertTrue(delete_qr_asset_history(asset["id"], history["id"]))
        self.assertTrue(delete_qr_asset(asset["id"]))
        self.assertIsNone(get_qr_asset_by_token(asset["token"]))
        with self.assertRaises(ValueError):
            create_qr_asset("asset-301", "新标签", "新机房")


if __name__ == "__main__":
    unittest.main()
