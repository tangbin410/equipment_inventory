import tempfile
import unittest
from pathlib import Path

import openpyxl
from fastapi import HTTPException

from backend import database
from backend.main import TASK_TEMPLATE_HEADERS, _parse_excel_devices, api_summary


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
        sheet.append(TASK_TEMPLATE_HEADERS)
        sheet.append(["测试机房", "汇聚及核心", "OTN", "测试设备", "OptiX OSN", "ASSET-001", "设备机架-AC06B", "在网", "001"])

        devices = _parse_excel_devices(sheet)["测试机房"]
        task_id = database.create_task("测试任务", "测试机房", None)
        database.batch_create_records(task_id, devices)

        summary = api_summary({})

        self.assertEqual(summary[0]["records"][0]["expected_rack_no"], "设备机架-AC06B")
        self.assertEqual(devices[0]["device_extra"]["设备类别"], "OTN")

    def test_import_requires_all_nine_template_headers(self):
        values = {
            "机房名称": "测试机房",
            "机房所在片区": "汇聚及核心",
            "设备类别": "OTN",
            "网元名称": "测试设备",
            "设备型号": "OptiX OSN",
            "资产标签号": "ASSET-001",
            "设备机架": "设备机架-AC06B",
            "生命周期状态": "在网",
            "地点编码": "001",
        }
        for missing_header in TASK_TEMPLATE_HEADERS:
            with self.subTest(missing_header=missing_header):
                workbook = openpyxl.Workbook()
                sheet = workbook.active
                headers = [header for header in TASK_TEMPLATE_HEADERS if header != missing_header]
                sheet.append(headers)
                sheet.append([values[header] for header in headers])

                with self.assertRaisesRegex(HTTPException, missing_header):
                    _parse_excel_devices(sheet)

    def test_import_allows_extra_columns(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append([*TASK_TEMPLATE_HEADERS, "现场自定义列"])
        sheet.append([
            "测试机房", "汇聚及核心", "OTN", "测试设备", "OptiX OSN", "ASSET-001",
            "设备机架-AC06B", "在网", "001", "允许保留",
        ])

        devices = _parse_excel_devices(sheet)["测试机房"]

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["expected_model"], "OptiX OSN")


if __name__ == "__main__":
    unittest.main()
