import io
import unittest

import openpyxl

from backend import database as db, main, three_way as tw
import tests.test_three_way_workflow as workflow


class CabinetComparisonTests(unittest.TestCase):
    def setUp(self):
        self.f = workflow.ThreeWayWorkflowTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def add_assets(self, barcode='C1', code='001'):
        self.f.assets([('A1', '网元甲', '001', 'M1'),
                       (barcode, '设备机架-R1', code, '传输综合柜规格')])

    def read(self):
        return tw.read([self.f.task])

    def cross_cabinet_comparison(self, anchors=()):
        self.f.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.f.task,))
        self.f.sql("UPDATE scan_records SET device_extra=? WHERE id=?",
                   ('{"设备机架":"AA01A、设备机架-AA02A"}', self.f.record))
        # 条码顺序特意与柜位相反，不能按书写顺序配对。
        db.submit_scan(self.f.record, 'A1', False, 'C2,C1', False,
                       'AA01A、AA02A', 'M1', '', created_by=self.f.worker)
        for index, (position, barcode) in enumerate(anchors, 2):
            db.batch_create_records(self.f.task, [dict(device_name=f'单柜设备{index}', expected_model='M1',
                                                     expected_barcode=f'A{index}', device_extra={'设备机架': position})])
            record_id = db.get_task_items(self.f.task)[-1]['id']
            db.submit_scan(record_id, f'A{index}', False, barcode, False,
                           position, 'M1', '', created_by=self.f.worker)
        db.complete_inventory_task(self.f.task, self.f.worker)
        self.f.assets([('A1', '网元甲', '001', 'M1'),
                       ('C1', '设备机架-AA01A', '001', '传输综合柜规格'),
                       ('C2', '设备机架-AA02A', '001', '传输综合柜规格')])
        return {cabinet['position']: cabinet for cabinet in self.read()['cabinet_comparison']}

    def test_cross_cabinet_devices_are_grouped_by_position_without_guessing_barcodes(self):
        cabinets = self.cross_cabinet_comparison()
        self.assertEqual(set(cabinets), {'AA01A', 'AA02A'})
        for position, cabinet in cabinets.items():
            self.assertEqual(cabinet['resource_name'], position)
            self.assertEqual(cabinet['position_status'], '一致')
            self.assertEqual(cabinet['field_devices'], ['网元甲'])
            self.assertEqual(cabinet['barcode_status'], '待核实')
            self.assertIn('待人工对应', cabinet['field_barcodes'])
            self.assertIn('C1、C2', cabinet['field_barcodes'])
            self.assertEqual(cabinet['problems'], ['跨柜设备的机柜条码尚未逐柜对应，请管理员核实'])
            self.assertFalse(cabinet['consistent'])

    def test_single_cabinet_feedback_resolves_cross_cabinet_barcodes(self):
        cabinets = self.cross_cabinet_comparison([('AA01A', 'C1'), ('AA02A', 'C2')])
        for position, barcode in [('AA01A', 'C1'), ('AA02A', 'C2')]:
            cabinet = cabinets[position]
            self.assertEqual(cabinet['field_barcodes'], barcode)
            self.assertEqual(cabinet['asset_barcodes'], barcode)
            self.assertIn('网元甲', cabinet['field_devices'])
            self.assertTrue(cabinet['consistent'])

    def test_one_known_cabinet_does_not_infer_other_barcode_by_elimination(self):
        cabinets = self.cross_cabinet_comparison([('AA01A', 'C1')])
        self.assertTrue(cabinets['AA01A']['consistent'])
        self.assertEqual(cabinets['AA01A']['field_barcodes'], 'C1')
        self.assertFalse(cabinets['AA02A']['consistent'])
        self.assertEqual(cabinets['AA02A']['barcode_status'], '待核实')
        self.assertIn('待人工对应', cabinets['AA02A']['field_barcodes'])

    def test_single_position_multiple_barcodes_remain_a_real_conflict(self):
        self.add_assets()
        self.f.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.f.task,))
        db.submit_scan(self.f.record, 'A1', False, 'C1,C2', False, 'R1', 'M1', '', created_by=self.f.worker)
        db.complete_inventory_task(self.f.task, self.f.worker)
        cabinet = self.read()['cabinet_comparison'][0]
        self.assertIn('同一柜位登记多个机柜条码，待管理员核实', cabinet['problems'])
        self.assertEqual(cabinet['barcode_status'], '不一致')
        self.assertFalse(cabinet['consistent'])

    def test_cabinet_barcode_is_separate_from_device_and_resource_has_no_barcode(self):
        self.add_assets()
        result = self.read()
        cabinet = result['cabinet_comparison'][0]
        self.assertEqual(cabinet['field_barcodes'], 'C1')
        self.assertEqual(cabinet['asset_barcodes'], 'C1')
        self.assertEqual(cabinet['barcode_status'], '一致')
        self.assertTrue(cabinet['consistent'])
        self.assertIn('未提供', cabinet['resource_barcode_status'])
        self.assertNotEqual(cabinet['status'], '三方一致')
        self.assertEqual([g['barcode'] for g in result['groups']], ['A1'])
        self.assertEqual(result['asset_counts']['device'], 2)
        self.assertEqual(result['asset_counts']['cabinet'], 1)

    def test_cabinet_resource_prefix_is_a_type_marker_not_a_position_difference(self):
        self.add_assets()
        self.f.sql("UPDATE scan_records SET device_extra=? WHERE id=?",
                   ('{"设备机架":"设备机架-R1"}', self.f.record))
        cabinet = self.read()['cabinet_comparison'][0]
        self.assertEqual(cabinet['resource_name'], '设备机架-R1')
        self.assertEqual(cabinet['position_status'], '一致')
        self.assertEqual(len(self.read()['cabinet_comparison']), 1)

    def test_cabinet_comparison_uses_latest_admin_correction(self):
        self.add_assets()
        record = db.get_report(self.f.task)[0]
        db.create_record_correction(self.f.record, {**record, 'cabinet_barcodes': 'C2'},
                                    '条码录入错误', '按现场核实', self.f.admin)
        cabinet = self.read()['cabinet_comparison'][0]
        self.assertEqual(cabinet['field_barcodes'], 'C2')
        self.assertEqual(cabinet['barcode_status'], '不一致')
        self.assertFalse(cabinet['consistent'])
        self.add_assets('C2')
        self.assertTrue(self.read()['cabinet_comparison'][0]['consistent'])

    def test_same_position_assets_in_other_rooms_are_not_matched(self):
        self.add_assets('OTHER', '999')
        cabinet = self.read()['cabinet_comparison'][0]
        self.assertEqual(cabinet['asset_barcodes'], '')
        self.assertIn('资产表未找到对应机柜资源名称', cabinet['problems'])
        self.assertFalse(cabinet['consistent'])

    def test_repeated_devices_in_same_cabinet_are_one_cabinet_without_conflict(self):
        self.add_assets()
        self.f.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.f.task,))
        db.batch_create_records(self.f.task, [dict(device_name='设备乙', expected_model='M1',
                                                 expected_barcode='A2', device_extra={'设备机架': 'R1'})])
        second = db.get_task_items(self.f.task)[1]['id']
        db.submit_scan(second, 'A2', False, 'C1', False, 'R1', 'M1', '', created_by=self.f.worker)
        db.complete_inventory_task(self.f.task, self.f.worker)
        cabinet = self.read()['cabinet_comparison'][0]
        self.assertEqual(len(self.read()['cabinet_comparison']), 1)
        self.assertEqual(set(cabinet['field_devices']), {'网元甲', '设备乙'})
        self.assertTrue(cabinet['consistent'])

    def test_same_barcode_at_multiple_positions_is_not_silently_accepted(self):
        self.add_assets()
        self.f.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.f.task,))
        db.batch_create_records(self.f.task, [dict(device_name='设备乙', expected_model='M1',
                                                 expected_barcode='A2', device_extra={'设备机架': 'R2'})])
        second = db.get_task_items(self.f.task)[1]['id']
        db.submit_scan(second, 'A2', False, 'C1', False, 'R2', 'M1', '', created_by=self.f.worker)
        db.complete_inventory_task(self.f.task, self.f.worker)
        cabinets = self.read()['cabinet_comparison']
        self.assertEqual(len(cabinets), 2)
        self.assertTrue(all(not cabinet['consistent'] for cabinet in cabinets))
        self.assertTrue(all(any('多个柜位' in problem for problem in cabinet['problems']) for cabinet in cabinets))

    def test_model_wording_does_not_move_a_device_to_cabinet_comparison(self):
        self.f.assets([('A1', '网元甲', '001', 'SPN机架/OTN机柜')])
        self.assertEqual(self.read()['asset_counts']['cabinet'], 0)
        self.assertEqual(len(self.read()['groups']), 1)

    def test_export_includes_current_cabinet_values_and_resource_limitation(self):
        self.add_assets()
        response = main.api_export_comparison([self.f.task], {})
        workbook = openpyxl.load_workbook(io.BytesIO(response.body))
        self.assertIn('传输综合柜核对', workbook.sheetnames)
        rows = list(workbook['传输综合柜核对'].values)
        self.assertEqual([r[1] for r in rows[1:]], ['综资', '现场', '资产'])
        self.assertIn('未提供', rows[1][2])
        self.assertEqual(rows[2][2], 'C1')
        self.assertEqual(rows[3][2], 'C1')
        self.assertNotIn('三方一致', rows[1][5])

    def test_void_surplus_exits_device_and_cabinet_comparison(self):
        self.add_assets()
        self.f.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.f.task,))
        surplus = db.create_surplus_record(self.f.task, '误报设备', 'M1', 'EXTRA', False,
                                           'EXTRA-CAB', False, 'R9', '', self.f.worker)
        db.complete_inventory_task(self.f.task, self.f.worker)
        self.assertTrue(any(c['position'] == 'R9' for c in self.read()['cabinet_comparison']))
        db.void_capture_record('scan_records', surplus['id'], self.f.admin, '误登记')
        result = self.read()
        self.assertFalse(any(c['position'] == 'R9' for c in result['cabinet_comparison']))
        self.assertFalse(any(r.get('record_id') == surplus['id'] for g in result['groups']
                             for r in tw.source_records(g, '现场')))
        self.assertTrue(db.get_record_by_id(surplus['id'])['is_void'])
