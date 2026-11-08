"""资产名称包含机柜时，以同地点的综资资源关联区分设备与独立机柜。"""

import unittest

from backend import database as db, three_way as tw
from tests import test_three_way_workflow as workflow_fixture


class AssetDeviceClassificationTests(unittest.TestCase):
    def setUp(self):
        self.f = workflow_fixture.ThreeWayWorkflowTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def assets(self, *rows):
        headers = ['资产条码', '资源名称', '地点编码', '规格型号', '资源类别', '资产名称']
        db.save_asset_info_snapshot(
            file_name='资产.xlsx', sheet_name='资产', barcode_column='资产条码', headers=headers,
            records=[{'barcode': row[0], 'fields': dict(zip(headers, row))} for row in rows],
            imported_by=self.f.admin, imported_by_name='管理员',
        )

    def test_spn_device_named_transmission_cabinet_matches_all_three_sources(self):
        self.f.sql('UPDATE scan_records SET device_name=? WHERE id=?', ('218-测试网元-2', self.f.record))
        self.assets(('A1', '218-测试网元-2', '001', 'N63B型ETSI机柜2200*600*300', '传输内线-SPN', '传输综合柜'))
        result = tw.read([self.f.task])
        group = next(g for g in result['groups'] if g['device_name'] == '218-测试网元-2')
        self.assertEqual([r['barcode'] for r in group['sources']['资产']], ['A1'])
        self.assertEqual(group['checks']['综资资产']['资产条码'], '一致')
        self.assertEqual(group['checks']['综资资产']['地点编码'], '一致')
        self.assertNotIn('资产表未找到对应资源名称', group['problems'])
        self.assertEqual(group['asset_model_status'], '待判断', '型号不同仍交管理员判断')
        self.assertEqual(result['asset_counts']['cabinet'], 0)
        self.assertEqual(result['scoped_assets'][0]['entity_kind'], 'device')

    def test_explicit_cabinet_resource_prefix_remains_cabinet_even_if_a_resource_has_same_name(self):
        self.f.sql('UPDATE scan_records SET device_name=? WHERE id=?', ('设备机架-R1', self.f.record))
        self.assets(('A1', '设备机架-R1', '001', '柜规格', '传输内线-SPN', '传输综合柜'))
        result = tw.read([self.f.task])
        self.assertEqual(result['scoped_assets'][0]['entity_kind'], 'cabinet')
        self.assertEqual(result['asset_counts']['cabinet'], 1)
        self.assertEqual(result['cabinet_comparison'][0]['asset_barcodes'], 'A1')
        self.assertFalse(result['groups'][0]['sources']['资产'])

    def test_same_resource_name_in_another_location_does_not_change_cabinet_classification(self):
        other, _ = self.f.make_task(room='另一机房', code='002', name='另一网元', expected='A2', actual='A2')
        self.assets(('A1', '网元甲', '001', 'M1', '传输内线-SPN', '传输综合柜'),
                    ('C2', '网元甲', '002', '柜规格', '传输综合柜', '传输综合柜'))
        result = tw.read([self.f.task, other])
        kinds = {a['barcode']: a['entity_kind'] for a in result['scoped_assets']}
        self.assertEqual(kinds, {'A1': 'device', 'C2': 'cabinet'})
        self.assertEqual(next(g for g in result['groups'] if g['device_name'] == '网元甲')['sources']['资产'][0]['barcode'], 'A1')
        self.assertTrue(any(c['task_id'] == other and c['asset_barcodes'] == 'C2' for c in result['cabinet_comparison']))

    def test_latest_resource_baseline_name_controls_classification_instead_of_old_task_name(self):
        tw.replace_baseline(self.f.task, [dict(device_name='规范网元甲', expected_model='M1', expected_barcode='A1',
                                             room_code='001', device_extra={'设备机架': 'R1'})], self.f.admin)
        self.assets(('A1', '规范网元甲', '001', 'SPN机柜规格', '传输内线-SPN', '传输综合柜'),
                    ('OLD-CAB', '网元甲', '001', '柜规格', '传输综合柜', '传输综合柜'))
        result = tw.read([self.f.task])
        self.assertEqual({a['barcode']: a['entity_kind'] for a in result['scoped_assets']},
                         {'A1': 'device', 'OLD-CAB': 'cabinet'})
        self.assertEqual(result['groups'][0]['sources']['资产'][0]['name'], '规范网元甲')
        self.assertEqual(db.get_record_by_id(self.f.record)['device_name'], '网元甲', '历史导入原值不被分类逻辑改写')

    def test_unmatched_cabinet_asset_name_stays_in_cabinet_comparison(self):
        self.assets(('A1', '网元甲', '001', 'M1', '传输内线-SPN', '普通设备'),
                    ('C1', 'R1', '001', '柜规格', '传输综合柜', '传输综合柜'))
        result = tw.read([self.f.task])
        self.assertEqual(result['asset_counts']['cabinet'], 1)
        self.assertEqual(result['cabinet_comparison'][0]['asset_barcodes'], 'C1')
        self.assertEqual([g['barcode'] for g in result['groups']], ['A1'])

    def test_odf_category_keeps_rack_priority_over_device_name_match(self):
        self.f.sql('UPDATE scan_records SET device_name=? WHERE id=?', ('ODF-R1', self.f.record))
        self.assets(('A1', 'ODF-R1', '001', '配线架规格', 'ODF', '传输综合柜'))
        result = tw.read([self.f.task])
        self.assertEqual(result['scoped_assets'][0]['entity_kind'], 'rack')
        self.assertEqual(result['asset_counts']['rack'], 1)
        self.assertFalse(result['groups'][0]['sources']['资产'])


if __name__ == '__main__':
    unittest.main()
