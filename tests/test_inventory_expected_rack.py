import tempfile
import unittest
from pathlib import Path

import openpyxl

from backend import database
from backend.main import _parse_excel_devices, api_summary


class InventoryExpectedRackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_imported_rack_is_returned_by_inventory_summary(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["机房名称", "机房所在片区", "网元名称", "设备型号", "资产标签号", "设备机架"])
        sheet.append(["测试机房", "汇聚及核心", "测试设备", "OptiX OSN", "ASSET-001", "设备机架-AC06B"])

        devices = _parse_excel_devices(sheet)["测试机房"]
        task_id = database.create_task("测试任务", "测试机房", None)
        database.batch_create_records(task_id, devices)

        summary = api_summary({})

        self.assertEqual(summary[0]["records"][0]["expected_rack_no"], "设备机架-AC06B")


if __name__ == "__main__":
    unittest.main()
