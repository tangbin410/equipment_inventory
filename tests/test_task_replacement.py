import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi import HTTPException

from backend import database as db, main


class TaskReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / 'test.db'
        db.BACKUP_DIR = db.DB_DIR / 'backups'
        db.init_db()
        self.worker = db.create_user('replace-worker', '现场', 'worker', '南区')
        self.other = db.create_user('replace-other', '同区人员', 'worker', '南区')
        self.admin = db.create_user('replace-admin', '管理员', 'admin', '南区')

    def tearDown(self):
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def devices(self, names=('网元甲',), **overrides):
        return [dict(_area_name='南区', room_code='001', device_name=name, expected_model='M1',
                     expected_barcode='A1', device_extra={'设备机架': 'R1', '设备类别': '传输设备'}, **overrides)
                for name in names]

    def import_task(self, devices=None, room='机房'):
        return db.import_room_tasks({room: devices or self.devices()})[1][0]

    def claim(self, task_id):
        self.assertTrue(db.claim_task(task_id, self.worker['id'])[0])

    def scan(self, record_id, **overrides):
        values = dict(asset_barcodes='A1', asset_no_barcode=False, cabinet_barcodes='C1',
                      cabinet_no_barcode=False, rack_no='R1', actual_model='M1', notes='', created_by=self.worker['id'])
        values.update(overrides)
        return db.submit_scan(record_id, **values)

    def sql(self, sql, args=()):
        conn = db.get_conn()
        try:
            result = conn.execute(sql, args).fetchall()
            conn.commit()
            return [dict(r) for r in result]
        finally:
            conn.close()

    def test_unclaimed_task_is_voided_but_raw_rows_are_preserved(self):
        first = self.import_task()['task_id']
        original = db.get_task_items(first)[0]['id']
        latest = self.import_task()
        self.assertEqual(latest['replaced_task_ids'], [first])
        self.assertIsNone(db.get_task_by_id(first))
        self.assertEqual(self.sql('SELECT is_deleted,superseded_by_task_id FROM tasks WHERE id=?', (first,))[0],
                         dict(is_deleted=1, superseded_by_task_id=latest['task_id']))
        self.assertEqual(self.sql('SELECT id FROM scan_records WHERE id=?', (original,))[0]['id'], original)
        self.assertEqual([t['id'] for t in db.get_area_tasks(self.worker['id'])], [latest['task_id']])

    def test_import_matches_only_exact_room_name_for_legacy_empty_code(self):
        old = db.create_task('legacy', '机房', None, '南区')
        other = db.create_task('不同机房', '机房2', None, '南区')
        db.batch_create_records(old, self.devices())
        latest = self.import_task()['task_id']
        self.assertIsNone(db.get_task_by_id(old))
        self.assertIsNotNone(db.get_task_by_id(other))
        self.assertIsNone(db.get_task_by_id(latest)['waiting_for_task_id'])

    def test_import_marks_legacy_claimed_room_as_superseded_and_inherits_on_release(self):
        old = db.create_task('legacy', '机房', self.worker['id'], '南区')
        db.batch_create_records(old, self.devices())
        self.scan(db.get_task_items(old)[0]['id'])
        latest = self.import_task()['task_id']
        self.assertEqual(db.get_task_by_id(old)['superseded_by_task_id'], latest)
        self.assertEqual(db.get_task_by_id(latest)['waiting_for_task_id'], old)
        db.release_task(old, self.worker['id'])
        self.assertNotEqual(db.get_task_items(latest)[0]['match_status'], '未扫')

    def test_import_does_not_replace_nonempty_conflicting_room_code(self):
        old = db.create_task('legacy', '机房', None, '南区')
        self.sql('UPDATE tasks SET room_code=? WHERE id=?', ('OTHER', old))
        with self.assertRaisesRegex(ValueError, '已有地点编码'):
            self.import_task()
        self.assertIsNotNone(db.get_task_by_id(old))

    def test_claimed_old_task_is_readable_but_all_capture_writes_are_locked(self):
        first = self.import_task()['task_id']
        self.claim(first)
        record = db.get_task_items(first)[0]['id']
        latest = self.import_task()['task_id']
        self.assertEqual(db.get_task_by_id(first)['superseded_by_task_id'], latest)
        self.assertEqual(db.get_task_by_id(latest)['waiting_for_task_id'], first)
        self.assertTrue(db.get_task_items(first))
        self.assertTrue(main.api_task_items(first, self.worker)['items'][0]['write_locked'])
        writes = [lambda: self.scan(record),
                  lambda: db.mark_record_missing(record, '未找到', created_by=self.worker['id']),
                  lambda: db.create_cabinet(first, 'odf', 'ODF1', False, 'ODF-A1', '', self.worker['id']),
                  lambda: db.create_surplus_record(first, '盘盈', 'M2', 'A2', False, 'C2', False, 'R2', '', self.worker['id']),
                  lambda: db.complete_inventory_task(first, self.worker['id'])]
        for write in writes:
            with self.subTest(write=write), self.assertRaisesRegex(ValueError, '最新任务'):
                write()
        with self.assertRaises(HTTPException) as error:
            main._require_task_write_access(self.worker, first)
        self.assertEqual(error.exception.status_code, 409)

    def test_new_task_cannot_be_claimed_or_assigned_until_old_release(self):
        first = self.import_task()['task_id']
        self.claim(first)
        latest = self.import_task()['task_id']
        for worker in (self.worker, self.other):
            success, message = db.claim_task(latest, worker['id'])
            self.assertFalse(success)
            self.assertIn('等待', message)
        with self.assertRaisesRegex(ValueError, '等待'):
            db.assign_task(latest, self.other['id'])
        self.assertFalse(db.release_task(first, self.other['id'])[0])
        response = main.api_release_task(first, '', self.worker)
        self.assertEqual(response['replacement_task_id'], latest)
        self.assertIsNone(db.get_task_by_id(first))
        self.assertIsNone(db.get_task_by_id(latest)['waiting_for_task_id'])
        self.assertTrue(db.claim_task(latest, self.other['id'])[0])

    def test_release_inherits_unchanged_and_missing_but_requires_changed_and_added_devices(self):
        devices = self.devices(('未变化', '型号变', '柜位变', '条码变', '类别变', '生命周期变', '未找到', '删除设备'))
        first = self.import_task(devices)['task_id']
        self.claim(first)
        old = {r['device_name']: r for r in db.get_task_items(first)}
        for name, row in old.items():
            if name == '未找到':
                db.mark_record_missing(row['id'], '现场无设备', '已查看', self.worker['id'])
            else:
                self.scan(row['id'])
        updated = self.devices(('未变化', '型号变', '柜位变', '条码变', '类别变', '生命周期变', '未找到', '新增设备'))
        updated[1]['expected_model'] = 'M2'
        updated[2]['device_extra']['设备机架'] = 'R2'
        updated[3]['expected_barcode'] = 'A2'
        updated[4]['device_extra']['设备类别'] = '其他设备'
        updated[5]['device_extra']['生命周期状态'] = '退网'
        latest = self.import_task(updated)['task_id']
        response = main.api_release_task(first, '', self.worker)
        self.assertEqual(response['inherited_count'], 2)
        rows = {r['device_name']: r for r in db.get_task_items(latest)}
        self.assertEqual(rows['未变化']['scanned_at'], old['未变化']['scanned_at'] or db.get_task_items(first)[0]['scanned_at'])
        self.assertEqual(rows['未变化']['inherited_from_task_id'], first)
        self.assertEqual(rows['未变化']['inherited_from_record_id'], old['未变化']['id'])
        self.assertEqual(rows['未找到']['record_type'], 'missing')
        self.assertEqual(rows['未找到']['missing_reason'], '现场无设备')
        for name in ('型号变', '柜位变', '条码变', '类别变', '生命周期变', '新增设备'):
            self.assertEqual(rows[name]['match_status'], '未扫', name)

    def test_release_reads_admin_latest_effective_values_including_post_import_correction(self):
        first = self.import_task()['task_id']
        self.claim(first)
        row = self.scan(db.get_task_items(first)[0]['id'])
        latest = self.import_task()['task_id']
        db.create_record_correction(row['id'], {**row, 'actual_model': '管理员型号', 'asset_barcodes': 'NEW',
                                              'rack_no': 'NEW-R', 'cabinet_barcodes': 'NEW-C'},
                                    '修正采集', '', self.admin['id'])
        db.release_task(first, self.worker['id'])
        inherited = db.get_task_items(latest)[0]
        self.assertEqual((inherited['actual_model'], inherited['asset_barcodes'], inherited['rack_no'], inherited['cabinet_barcodes']),
                         ('管理员型号', 'NEW', 'NEW-R', 'NEW-C'))
        self.assertEqual(inherited['inherited_source_label'], '管理员修正')
        self.assertEqual(inherited['scanned_at'], row['scanned_at'])

    def test_barcode_order_does_not_trigger_rescan_and_duplicate_names_never_inherit(self):
        devices = self.devices(('唯一', '同名', '同名'))
        devices[0]['expected_barcode'] = 'A2,A1'
        first = self.import_task(devices)['task_id']
        self.claim(first)
        for row in db.get_task_items(first):
            self.scan(row['id'])
        devices[0]['expected_barcode'] = 'A1,A2'
        latest = self.import_task(devices)['task_id']
        db.release_task(first, self.worker['id'])
        rows = db.get_task_items(latest)
        self.assertTrue(rows[0]['inherited_from_record_id'])
        self.assertTrue(all(r['match_status'] == '未扫' for r in rows[1:]))

    def test_racks_and_surplus_inherit_once_excluding_void_rows(self):
        first = self.import_task()['task_id']
        self.claim(first)
        rack, _ = db.create_cabinet(first, 'odf', 'ODF1', False, 'ODF-A1', '', self.worker['id'])
        other, _ = db.create_cabinet(first, 'iodf', 'VOID', False, 'VOID', '', self.worker['id'])
        db.delete_task_rack(first, other['id'], self.worker['id'])
        surplus = db.create_surplus_record(first, '盘盈', 'M2', 'A2', False, 'C2', False, 'R2', '', self.worker['id'])
        latest = self.import_task()['task_id']
        result = main.api_release_task(first, '', self.worker)
        self.assertEqual((result['inherited_rack_count'], result['inherited_surplus_count']), (1, 1))
        inherited = next(r for r in db.get_task_items(latest) if r['record_type'] == 'surplus')
        self.assertNotEqual(inherited['id'], surplus['id'])
        self.assertEqual(inherited['inherited_from_record_id'], surplus['id'])
        racks = db.get_task_cabinets(latest)
        self.assertEqual(len(racks), 1)
        self.assertEqual(racks[0]['inherited_from_cabinet_id'], rack['id'])
        self.assertNotIn('沿用任务', racks[0]['source_label'])
        self.assertFalse(db.release_task(first, self.worker['id'])[0])

    def test_surplus_now_in_manifest_is_not_duplicated_and_requires_confirmation(self):
        first = self.import_task()['task_id']
        self.claim(first)
        db.create_surplus_record(first, '新设备', 'M2', 'A2', False, 'C2', False, 'R2', '', self.worker['id'])
        latest = self.import_task(self.devices(('网元甲', '新设备')))['task_id']
        result = main.api_release_task(first, '', self.worker)
        self.assertEqual(result['surplus_conflict_count'], 1)
        rows = db.get_task_items(latest)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['match_status'] == '未扫' for r in rows))

    def test_waiting_latest_task_blocks_admin_rack_write_but_old_admin_correction_is_allowed(self):
        first = self.import_task()['task_id']
        self.claim(first)
        rack, _ = db.create_cabinet(first, 'odf', 'ODF1', False, 'AA01', '', self.worker['id'])
        latest = self.import_task()['task_id']
        values = dict(cabinet_type='odf', cabinet_barcode='ODF1', no_barcode=False,
                      rack_no='AA02', notes='', correction_reason='修正编号')
        with self.assertRaisesRegex(ValueError, '等待'):
            db.save_admin_rack(latest, values, self.admin['id'])
        db.save_admin_rack(first, values, self.admin['id'], rack['id'])
        self.assertTrue(db.release_task(first, self.worker['id'])[0])
        self.assertEqual(db.get_task_cabinets(latest)[0]['rack_no'], 'AA02')

    def test_two_same_name_surplus_records_are_both_preserved(self):
        first = self.import_task()['task_id']
        self.claim(first)
        for barcode in ('S1', 'S2'):
            db.create_surplus_record(first, '同名盘盈', 'M2', barcode, False, 'C2', False, 'R2', '', self.worker['id'])
        latest = self.import_task()['task_id']
        details = {}
        db.release_task(first, self.worker['id'], details=details)
        self.assertEqual(details['inherited_surplus_count'], 2)
        surplus = [r for r in db.get_task_items(latest) if r['record_type'] == 'surplus']
        self.assertEqual({r['asset_barcodes'] for r in surplus}, {'S1', 'S2'})

    def test_repeated_import_points_old_claim_to_latest_and_preserves_pending_inheritance(self):
        first = self.import_task()['task_id']
        self.claim(first)
        self.scan(db.get_task_items(first)[0]['id'])
        middle = self.import_task()['task_id']
        latest = self.import_task()['task_id']
        self.assertIsNone(db.get_task_by_id(middle))
        self.assertEqual(db.get_task_by_id(first)['superseded_by_task_id'], latest)
        details = {}
        db.release_task(first, self.worker['id'], details=details)
        self.assertEqual(details['replacement_task_id'], latest)
        final = self.import_task()['task_id']
        self.assertIsNone(db.get_task_by_id(latest))
        self.assertTrue(db.get_task_items(final)[0]['inherited_from_record_id'])
        self.assertNotEqual(db.get_task_items(final)[0]['match_status'], '未扫')

    def test_completed_history_does_not_supply_new_inventory_results(self):
        first = self.import_task()['task_id']
        self.claim(first)
        self.scan(db.get_task_items(first)[0]['id'])
        db.complete_inventory_task(first, self.worker['id'])
        latest = self.import_task()['task_id']
        self.assertEqual(db.get_task_by_id(first)['status'], 'done')
        self.assertEqual(db.get_task_items(latest)[0]['match_status'], '未扫')
        self.assertIsNone(db.get_task_by_id(latest)['waiting_for_task_id'])

    def test_recollection_removes_inheritance_marker(self):
        first = self.import_task()['task_id']
        self.claim(first)
        self.scan(db.get_task_items(first)[0]['id'])
        latest = self.import_task()['task_id']
        db.release_task(first, self.worker['id'])
        self.claim(latest)
        row = db.get_task_items(latest)[0]
        self.assertTrue(row['inherited_from_record_id'])
        self.scan(row['id'])
        self.assertIsNone(db.get_task_items(latest)[0]['inherited_from_record_id'])

    def test_open_adjustment_blocks_whole_import_without_partial_task_changes(self):
        first = self.import_task()['task_id']
        self.sql("INSERT INTO ai_adjustment_tasks(source_task_ids_json,comparison_json,created_at) VALUES(?,?,'now')",
                 (f'[{first}]', f'{{"task_ids":[{first}]}}'))
        before = len(self.sql('SELECT id FROM tasks'))
        with self.assertRaisesRegex(ValueError, '未闭环整改单'):
            self.import_task()
        self.assertEqual(len(self.sql('SELECT id FROM tasks')), before)
        self.assertIsNotNone(db.get_task_by_id(first))

    def test_multiple_legacy_doing_tasks_block_import_until_extra_claims_released(self):
        first = self.import_task()['task_id']
        self.claim(first)
        second = db.create_task('历史重复任务', '机房', self.other['id'], '南区')
        self.sql('UPDATE tasks SET room_code=? WHERE id=?', ('001', second))
        before = len(self.sql('SELECT id FROM tasks'))
        with self.assertRaisesRegex(ValueError, '多份已领取未完成任务'):
            self.import_task()
        self.assertEqual(len(self.sql('SELECT id FROM tasks')), before)
        self.assertTrue(db.release_task(second, self.other['id'])[0])
        latest = self.import_task()['task_id']
        self.assertEqual(db.get_task_by_id(first)['superseded_by_task_id'], latest)

    def test_remaining_doing_task_is_authoritative_over_released_legacy_pending(self):
        first = self.import_task()['task_id']
        self.claim(first)
        self.scan(db.get_task_items(first)[0]['id'], asset_barcodes='CURRENT')
        second = db.create_task('历史重复任务', '机房', self.other['id'], '南区')
        self.sql('UPDATE tasks SET room_code=? WHERE id=?', ('001', second))
        db.batch_create_records(second, self.devices())
        self.scan(db.get_task_items(second)[0]['id'], asset_barcodes='OLD', created_by=self.other['id'])
        self.assertTrue(db.release_task(second, self.other['id'])[0])
        latest = self.import_task()['task_id']
        self.assertEqual(db.get_task_items(latest)[0]['match_status'], '未扫')
        self.assertTrue(db.release_task(first, self.worker['id'])[0])
        self.assertEqual(db.get_task_items(latest)[0]['asset_barcodes'], 'CURRENT')

    def test_claim_vs_import_is_serialized_without_two_claimable_tasks(self):
        first = self.import_task()['task_id']
        barrier = Barrier(2)
        def claim():
            barrier.wait()
            return db.claim_task(first, self.worker['id'])
        def upload():
            barrier.wait()
            return self.import_task()['task_id']
        with ThreadPoolExecutor(2) as executor:
            claim_result = executor.submit(claim)
            upload_result = executor.submit(upload)
            accepted, _ = claim_result.result()
            latest = upload_result.result()
        if accepted:
            self.assertEqual(db.get_task_by_id(first)['superseded_by_task_id'], latest)
            self.assertFalse(db.claim_task(latest, self.other['id'])[0])
        else:
            self.assertIsNone(db.get_task_by_id(first))
            self.assertTrue(db.claim_task(latest, self.other['id'])[0])


if __name__ == '__main__':
    unittest.main()
