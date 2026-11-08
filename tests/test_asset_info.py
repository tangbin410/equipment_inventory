import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
import openpyxl

from backend import database
from backend.main import _asset_info_preview, _validate_asset_info_rows


class AssetInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        root = Path(self.temp_dir.name)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    @staticmethod
    def make_preview(rows):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        return _asset_info_preview(workbook, "资产台账.xlsx")

    def test_custom_columns_and_selected_barcode_column(self):
        preview = self.make_preview([
            ["序号", "资产编号", "设备型号", "责任人"],
            [1, "A-001", "PTN-1", "张三"],
        ])

        records = _validate_asset_info_rows(preview, "资产编号")

        self.assertEqual(preview["headers"], ["序号", "资产编号", "设备型号", "责任人"])
        self.assertEqual(records[0]["barcode"], "A-001")
        self.assertEqual(records[0]["fields"]["责任人"], "张三")

    def test_duplicate_normalized_barcodes_reject_entire_import(self):
        preview = self.make_preview([
            ["资产编码", "型号"],
            ["a-001", "型号一"],
            [" A-001 ", "型号二"],
        ])

        with self.assertRaisesRegex(HTTPException, "必须唯一"):
            _validate_asset_info_rows(preview, "资产编码")

    def test_failed_duplicate_import_preserves_current_snapshot(self):
        database.save_asset_info_snapshot(
            file_name="old.xlsx",
            sheet_name="Sheet",
            barcode_column="资产条码",
            headers=["资产条码", "型号"],
            records=[{"barcode": "OLD-001", "fields": {"资产条码": "OLD-001", "型号": "旧型号"}}],
            imported_by=1,
            imported_by_name="管理员",
        )
        preview = self.make_preview([
            ["资产条码", "型号"],
            ["NEW-001", "新型号一"],
            ["NEW-001", "新型号二"],
        ])

        with self.assertRaises(HTTPException):
            _validate_asset_info_rows(preview, "资产条码")

        self.assertEqual(database.query_asset_info("OLD-001")["fields"]["型号"], "旧型号")
        self.assertIsNone(database.query_asset_info("NEW-001"))

    def test_snapshot_replacement_and_case_insensitive_lookup(self):
        first = database.save_asset_info_snapshot(
            file_name="first.xlsx",
            sheet_name="资产",
            barcode_column="资产条码",
            headers=["资产条码", "自定义型号"],
            records=[{"barcode": "A-001", "fields": {"资产条码": "A-001", "自定义型号": "型号一"}}],
            imported_by=1,
            imported_by_name="管理员",
        )
        second = database.save_asset_info_snapshot(
            file_name="second.xlsx",
            sheet_name="设备",
            barcode_column="设备编码",
            headers=["设备编码", "责任人"],
            records=[{"barcode": "B-002", "fields": {"设备编码": "B-002", "责任人": "李四"}}],
            imported_by=1,
            imported_by_name="管理员",
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(database.query_asset_info("A-001"))
        self.assertEqual(database.query_asset_info("b-002")["fields"]["责任人"], "李四")
        self.assertEqual(len(database.list_asset_info_imports()), 2)

    def test_display_fields_are_saved_and_filter_query_result(self):
        database.save_asset_info_snapshot(
            file_name="asset.xlsx",
            sheet_name="资产",
            barcode_column="资产条码",
            headers=["资产条码", "资产名称", "责任人", "所属机房"],
            records=[{
                "barcode": "A-001",
                "fields": {"资产条码": "A-001", "资产名称": "传输设备", "责任人": "张三", "所属机房": "珠海机房"},
            }],
            imported_by=1,
            imported_by_name="管理员",
        )

        status = database.set_asset_info_display_fields(["责任人", "所属机房", "资产条码", "不存在字段"])
        result = database.query_asset_info("A-001")

        self.assertEqual(status["display_fields"], ["责任人", "所属机房"])
        self.assertEqual(result["fields"], {"责任人": "张三", "所属机房": "珠海机房"})

    def test_empty_barcode_and_invalid_column_are_rejected(self):
        preview = self.make_preview([["资产条码", "型号"], ["", "型号一"]])
        with self.assertRaisesRegex(HTTPException, "空值"):
            _validate_asset_info_rows(preview, "资产条码")
        with self.assertRaisesRegex(HTTPException, "有效的资产条码列"):
            _validate_asset_info_rows(preview, "不存在")


if __name__ == "__main__":
    unittest.main()
