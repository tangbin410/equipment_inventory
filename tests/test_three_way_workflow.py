import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
from fastapi import HTTPException
from backend import database as db, three_way as tw, main, ai_agent
from local_agent.context_policy import apply_context_policy
from local_agent.rules import run_rules


class ThreeWayWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / 'test.db'
        db.BACKUP_DIR = db.DB_DIR / 'backups'
        db.init_db()
        self.worker = db.create_user('w', '现场', 'worker', '南区')['id']
        self.admin = db.create_user('a', '管理员', 'admin', '南区')['id']
        self.task, self.record = self.make_task()
        self.assets()

    def tearDown(self):
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def sql(self, sql, args=()):
        conn = db.get_conn()
        try:
            conn.execute(sql, args)
            conn.commit()
        finally:
            conn.close()

    def make_task(self, room='机房', code='001', expected='A1', actual='A1', name='网元甲', model='M1', batch='batch'):
        task = db.create_task(room+'盘点', room, self.worker, '南区')
        self.sql('UPDATE tasks SET import_batch_id=?,room_code=? WHERE id=?', (batch, code, task))
        db.batch_create_records(task, [dict(device_name=name, expected_model='M1', expected_barcode=expected, device_extra={'设备机架':'R1'})])
        record = db.get_task_items(task)[0]['id']
        db.submit_scan(record, actual, False, 'C1', False, 'R1', model, '', created_by=self.worker)
        db.complete_inventory_task(task, self.worker)
        return task, record

    def assets(self, rows=None):
        rows = rows or [('A1','网元甲','001','M1')]
        return db.save_asset_info_snapshot(file_name='资产.xlsx',sheet_name='资产',barcode_column='资产条码',
            headers=['资产条码','资源名称','地点编码','规格型号'],
            records=[dict(barcode=b,fields=dict(zip(['资产条码','资源名称','地点编码','规格型号'],[b,n,c,m]))) for b,n,c,m in rows],
            imported_by=self.admin, imported_by_name='管理员')

    def groups(self, ids=None):
        return tw.read(ids or [self.task])['groups']

    def group(self, barcode='A1'):
        return next(g for g in self.groups() if g['barcode']==barcode)

    def judge(self, status='一致', barcode='A1'):
        g=self.group(barcode)
        tw.save_decision([self.task], g['group_key'], g['fingerprint'], 'model',dict(field_status=status,asset_status=status,note='已查型号资料'),self.admin)
        return self.group(barcode)

    def case(self):
        g=self.group()
        return tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)

    def action(self, case_id, action, actor=None, **data):
        return tw.case_action(case_id,action,actor or self.admin,dict(note='实际处理依据',**data))

    def test_models_are_always_manual_and_pending_is_not_a_confirmed_problem(self):
        g=self.group()
        self.assertEqual(g['name_status'],'一致')
        self.assertEqual(g['model_status'],'待判断')
        self.assertFalse(g['can_create_adjustment'])
        with self.assertRaisesRegex(ValueError,'人工判断'):
            self.case()
        self.assertTrue(self.judge()['consistent'])

    def test_not_applicable_field_model_is_explicit_and_keeps_manual_judgment(self):
        record=db.get_report(self.task)[0]
        db.create_record_correction(self.record,{**record,'actual_model':'不涉及'},'根据现场凭证修正','该设备确无型号',self.admin)
        group=self.group()
        self.assertEqual(tw.source_records(group,'现场')[0]['model'],'不涉及')
        self.assertEqual(group['field_model_status'],'待判断')
        self.assertFalse(group['consistent'])
        response=main.api_export_comparison([self.task],{})
        rows=list(openpyxl.load_workbook(io.BytesIO(response.body)).active.values)
        self.assertEqual(next(row for row in rows[1:] if row[1]=='现场')[5],'不涉及')
        self.assertTrue(self.judge()['consistent'])

    def test_unlinked_asset_is_listed_outside_device_comparison(self):
        self.assets([('A1','网元甲','001','M1'), ('U1','','001','备用型号')])
        result = tw.read([self.task])
        self.assertEqual([a['barcode'] for a in result['unlinked_assets']], ['U1'])
        self.assertFalse(any(g['barcode'] == 'U1' for g in result['groups']))

    def test_manual_odf_rack_has_separate_pending_resource_comparison(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.task,))
        db.create_cabinet(self.task, 'odf', 'ODF-1', False, 'ODF-A1', '', self.worker)
        self.sql("UPDATE tasks SET status='done' WHERE id=?", (self.task,))
        self.assets([('A1','网元甲','001','M1'), ('ODF-1','ODF-A1','001','')])
        result = tw.read([self.task])
        rack = result['rack_comparison'][0]
        self.assertEqual((rack['rack_type'], rack['resource_name']), ('ODF', 'ODF-A1'))
        self.assertEqual(rack['barcode_status'], '一致')
        self.assertEqual(rack['location_status'], '一致')
        self.assertEqual(rack['resource_status'], '综资机架基线待补')
        self.assertFalse(any(g['barcode'] == 'ODF-1' for g in result['groups']))

    def test_rack_number_reused_in_other_room_matches_current_room_asset(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.task,))
        db.create_cabinet(self.task, 'odf', 'ODF-1', False, 'A01', '', self.worker)
        self.sql("UPDATE tasks SET status='done' WHERE id=?", (self.task,))
        self.assets([('A1','网元甲','001','M1'), ('ODF-1','A01','001',''),
                     ('ODF-OTHER','A01','999','')])
        rack = tw.read([self.task])['rack_comparison'][0]
        self.assertEqual(rack['asset_barcodes'], 'ODF-1')
        self.assertEqual(rack['barcode_status'], '一致')
        self.assertEqual(rack['location_status'], '一致')

    def test_deleting_current_rack_removes_feedback_without_deleting_asset(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?",(self.task,))
        rack,_=db.create_cabinet(self.task,'odf','R1',False,'ODF-A01','',self.worker)
        self.assets([('A1','网元甲','001','M1'),('R1','ODF-A01','001','')])
        self.assertEqual(tw.read([self.task])['rack_comparison'][0]['barcode_status'],'一致')
        db.delete_task_rack(self.task,rack['id'],self.worker)
        result=tw.read([self.task])
        self.assertEqual(result['rack_comparison'][0]['field_barcode'],'未反馈')
        self.assertEqual(result['rack_comparison'][0]['asset_barcodes'],'R1')
        self.assertEqual(result['asset_counts']['total'],2)
        self.assertEqual([c['cabinet_type'] for c in db.get_task_cabinets(self.task)],['equipment'])

    def test_asset_barcode_accounting_separates_devices_racks_and_unlinked(self):
        self.assets([
            ('A1','网元甲','001','SPN机架\\7900E'),
            ('D2','设备乙','001','M2'),('D3','设备乙','001','M3'),
            ('D4','设备丙','001','M4'),('D5','设备丁','001','M5'),
            ('D6','设备戊','001','M6'),('D7','设备己','001','M7'),
            ('D8','设备庚','001','M8'),('D9','设备庚','001','M9'),
            ('R1','机房/ODF-AA09B','001','光纤配线架'),
            ('R2','机房/ODF-AA10B','001','光纤配线架'),
            ('R3','机房/ODF-AA06','001','光纤配线架'),
            ('R4','机房/ODF-AA06','001','光纤配线架'),
            ('U1','','001','M10'),('U2','','001','M11'),
            ('OUT','其他机房/ODF-AA01','999','光纤配线架'),
        ])
        result=tw.read([self.task])
        self.assertEqual(result['asset_counts'],dict(total=15,named=13,resource_names=10,device=9,cabinet=0,rack=4,unlinked=2))
        self.assertEqual(len(result['groups']),7)
        self.assertEqual(len(result['rack_comparison']),3)
        devices={a['barcode'] for g in result['groups'] for a in tw.source_records(g,'资产')}
        racks={a['barcode'] for rack in result['rack_comparison'] for a in rack['asset_records']}
        unlinked={a['barcode'] for a in result['unlinked_assets']}
        self.assertEqual((len(devices),len(racks),len(unlinked)),(9,4,2))
        self.assertFalse(devices & racks or devices & unlinked or racks & unlinked)
        self.assertEqual(devices | racks | unlinked,{a['barcode'] for a in result['scoped_assets']})
        self.assertTrue(all(r['field_status']=='资产表有记录，现场未反馈' for r in result['rack_comparison']))
        duplicate=next(r for r in result['rack_comparison'] if r['resource_name']=='机房/ODF-AA06')
        self.assertEqual(duplicate['asset_barcodes'],'R3、R4')
        self.assertEqual(duplicate['barcode_status'],'同名多条，待管理员关联')
        self.assertFalse(any(g['can_create_adjustment'] and 'ODF-' in g['device_name'] for g in result['groups']))

    def test_rack_asset_category_and_name_do_not_reclassify_device_models(self):
        import json
        self.assets([('A1','网元甲','001','SPN机架\\7900E'),('R1','AA01','001',''),
                     ('R2','机房/IODF-AA02','001',''),('U1','','001','ODF')])
        conn=db.get_conn()
        try:
            for barcode,category in [('R1','公共-ODF机架'),('U1','公共-ODF机架')]:
                row=conn.execute('SELECT id,fields_json FROM asset_info_records WHERE barcode_norm=?',(barcode,)).fetchone()
                fields=json.loads(row['fields_json'])
                fields['资源类别']=category
                conn.execute('UPDATE asset_info_records SET fields_json=? WHERE id=?',(json.dumps(fields),row['id']))
            conn.commit()
        finally:
            conn.close()
        result=tw.read([self.task])
        self.assertEqual(result['asset_counts'],dict(total=4,named=3,resource_names=3,device=1,cabinet=0,rack=2,unlinked=1))
        self.assertEqual({r['rack_type'] for r in result['rack_comparison']},{'ODF','IODF'})
        self.assertEqual([g['barcode'] for g in result['groups']],['A1'])
        self.assertEqual([a['barcode'] for a in result['unlinked_assets']],['U1'])

    def test_prior_rack_can_classify_asset_but_cannot_become_current_feedback(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?",(self.task,))
        db.create_cabinet(self.task,'odf','R1',False,'A01','',self.worker)
        self.sql("UPDATE tasks SET status='done' WHERE id=?",(self.task,))
        new_task,_=self.make_task(batch='next-batch')
        self.assets([('A1','网元甲','001','M1'),('R1','A01','001','')])
        result=tw.read([new_task])
        self.assertEqual(len(result['rack_comparison']),1)
        rack=result['rack_comparison'][0]
        self.assertIsNone(rack['cabinet_id'])
        self.assertEqual(rack['field_barcode'],'未反馈')
        self.assertEqual(rack['field_location_code'],'')
        self.assertEqual(rack['barcode_status'],'现场未反馈')
        self.assertEqual(result['asset_counts']['rack'],1)
        self.assertFalse(any(g['barcode']=='R1' for g in result['groups']))

    def test_rack_number_compares_device_rack_not_cabinet_barcode(self):
        g=self.group()
        self.assertEqual(g['checks']['综资现场']['机架号'],'一致')
        self.sql("UPDATE scan_records SET rack_no='R2',cabinet_barcodes='R1' WHERE id=?",(self.record,))
        g=self.group()
        self.assertEqual(g['checks']['综资现场']['机架号'],'不一致')
        self.assertIn('R1 / R2','；'.join(g['problems']))

    def test_baseline_prefers_unique_net_name_when_old_barcodes_repeat(self):
        import json
        self.sql("""INSERT INTO scan_records(task_id,device_name,device_extra,expected_model,expected_barcode,
                 actual_model,asset_barcodes,cabinet_barcodes,cabinet_no_barcode,rack_no,notes,
                 record_type,field_status,match_status,scanned_at,created_by)
                 SELECT task_id,'网元乙',device_extra,expected_model,expected_barcode,actual_model,
                 asset_barcodes,cabinet_barcodes,cabinet_no_barcode,rack_no,notes,record_type,
                 field_status,match_status,scanned_at,created_by FROM scan_records WHERE id=?""",(self.record,))
        tw.replace_baseline(self.task,[
            dict(device_name='网元甲',expected_model='M1',expected_barcode='A1',room_code='001',device_extra={'设备机架':'R1'}),
            dict(device_name='网元乙',expected_model='M1',expected_barcode='A1',room_code='001',device_extra={'设备机架':'R1'})],self.admin)
        result=tw.read([self.task])
        self.assertEqual(len(result['stage_one']),2)
        self.assertTrue(all(row['resource'] and row['field'] for row in result['stage_one']))
        conn=db.get_conn()
        try:
            baseline=conn.execute('SELECT id,rows_json FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1',(self.task,)).fetchone()
            rows=json.loads(baseline['rows_json'])
            self.assertEqual(len({row['record_id'] for row in rows}),2)
            for index,row in enumerate(rows):
                row['record_id']=None
                row['entity_id']=f'updated-{index}'
            conn.execute('UPDATE resource_baselines SET rows_json=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),baseline['id']))
            conn.commit()
        finally:
            conn.close()
        result=tw.read([self.task])
        self.assertEqual(len(result['stage_one']),2)
        self.assertTrue(all(row['resource'] and row['field'] for row in result['stage_one']))

    def test_first_group_uses_name_even_when_barcode_or_record_link_differs(self):
        task={'id':1,'room_name':'机房'}
        resource=tw.source_row('综资','A1','网元甲','001','M1',task,1)
        wrong_name=tw.source_row('现场','A1','网元乙','001','M1',task,1)
        unmatched=tw.resource_field_comparison([resource],[wrong_name],set())
        self.assertEqual(len(unmatched),2)
        self.assertEqual(unmatched[0]['status'],'网元名称与设备记录归属冲突')
        self.assertIsNone(unmatched[1]['resource'])
        self.assertIn('名称',unmatched[1]['status'])
        same_name=tw.source_row('现场','A2','网元甲','001','M1',task,2)
        matched=tw.resource_field_comparison([resource],[same_name],set())
        self.assertEqual(len(matched),1)
        self.assertEqual(matched[0]['status'],'条码不一致')

    def test_first_group_duplicate_name_waits_for_admin_association(self):
        task={'id':1,'room_name':'机房'}
        resources=[tw.source_row('综资',code,'网元甲','001','M1',task,index)
                   for index,code in ((1,'A1'),(2,'A2'))]
        field=tw.source_row('现场','A1','网元甲','001','M1',task,1)
        rows=tw.resource_field_comparison(resources,[field],set())
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['status']=='同名多条，待管理员关联' for row in rows))

    def test_baseline_does_not_claim_one_old_record_for_two_new_names(self):
        tw.replace_baseline(self.task,[
            dict(device_name='新网元甲',expected_model='M1',expected_barcode='A1',room_code='001'),
            dict(device_name='新网元乙',expected_model='M1',expected_barcode='A1',room_code='001')],self.admin)
        result=tw.read([self.task])
        self.assertEqual(len(result['stage_one']),3)
        self.assertTrue(all(not row['feedback'] for row in result['stage_one'] if row['resource']))
        self.assertEqual(len({g['group_key'] for g in result['groups']}),len(result['groups']))

    def test_asset_name_must_match_net_name_even_if_barcode_matches(self):
        self.assets([('A1','另一网元','001','M1')])
        g=self.group()
        self.assertFalse(g['sources']['资产'])
        self.assertIn('资产表未找到对应资源名称',g['problems'])
        self.assertEqual(g['checks']['综资资产']['资产条码'],'待处理')

    def test_second_and_third_groups_match_by_name_when_all_barcodes_differ(self):
        self.sql("UPDATE scan_records SET asset_barcodes='A3' WHERE id=?",(self.record,))
        self.assets([('A2','网元甲','001','M1')])
        groups=self.groups()
        self.assertEqual(len(groups),1)
        g=groups[0]
        self.assertEqual(g['sources']['综资'][0]['barcode'],'A1')
        self.assertEqual(g['sources']['现场'][0]['barcode'],'A3')
        self.assertEqual(g['sources']['资产'][0]['barcode'],'A2')
        self.assertEqual(g['checks']['综资资产']['资产条码'],'不一致')
        self.assertEqual(g['checks']['现场资产']['资产条码'],'不一致')

    def test_duplicate_asset_name_needs_admin_association(self):
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M1')])
        g=self.group()
        self.assertEqual(len(g['sources']['资产']),2)
        self.assertIn('同名多条，待管理员关联',g['problems'])
        self.assertFalse(g['consistent'])

    def test_other_location_asset_does_not_match_even_when_name_and_barcode_equal(self):
        self.assets([('A1','网元甲','999','M1')])
        result=tw.read([self.task])
        g=result['groups'][0]
        self.assertEqual(g['sources']['现场'][0]['location_code'],'001')
        self.assertEqual(g['sources']['资产'],[])
        self.assertIn('资产表未找到对应资源名称',g['problems'])
        self.assertEqual(g['checks']['现场资产']['地点编码'],'待处理')
        self.assertEqual(result['scoped_assets'],[])
        self.assertEqual(result['asset_counts']['total'],0)

    def test_two_model_decisions_are_independent_and_asset_change_only_resets_one(self):
        g=self.group()
        tw.save_decision([self.task],g['group_key'],g['fingerprint'],'model',
                         dict(field_status='一致',asset_status='不一致',note='分别核对设备资料'),self.admin)
        g=self.group()
        self.assertEqual((g['field_model_status'],g['asset_model_status']),('一致','不一致'))
        self.assets([('A1','网元甲','001','M2')])
        g=self.group()
        self.assertEqual((g['field_model_status'],g['asset_model_status']),('一致','待判断'))

    def test_legacy_barcode_judgment_remains_history_but_needs_new_confirmation(self):
        import json
        self.sql("INSERT INTO comparison_decisions(group_key,source_fingerprint,kind,decision_json,actor_id,created_at) VALUES(?,?,?,?,?,?)",
                 ('batch:barcode:A1','old','model',json.dumps({'status':'一致','note':'旧规则判断'},ensure_ascii=False),self.admin,'2026-10-15'))
        g=self.group()
        self.assertEqual(g['model_status'],'待判断')
        self.assertEqual(g['history'][0]['decision']['note'],'旧规则判断')

    def test_net_element_stays_in_one_group_when_field_barcode_differs(self):
        self.sql("UPDATE scan_records SET asset_barcodes='A2' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元乙','002','M2'),('OUT','其他网元','002','M3')])
        groups={g['barcode']:g for g in self.groups()}
        self.assertEqual(set(groups),{'A1'})
        self.assertEqual(groups['A1']['sources']['现场'][0]['barcode'],'A2')
        self.assertEqual(groups['A1']['sources']['资产'][0]['name'],'网元甲')
        self.assertEqual(groups['A1']['checks']['综资现场']['资产条码'],'不一致')
        self.assertEqual(groups['A1']['checks']['现场资产']['资产条码'],'不一致')
        first=tw.read([self.task])['stage_one'][0]
        self.assertEqual(first['status'],'条码不一致')
        self.assertEqual(first['resource']['barcode'],'A1')
        self.assertEqual(first['field']['barcode'],'A2')
        self.assertEqual(groups['A1']['stage_one_status'],'条码不一致')

    def test_names_trim_only_and_empty_values_never_equal(self):
        self.assets([('A1',' 网元甲 ','001','M1')])
        self.assertEqual(self.group()['name_status'],'一致')
        self.assets([('A1','网 元甲','001','M1')])
        self.assertIn('资产表未找到对应资源名称',self.group()['problems'])
        self.assets([('A1','','001','M1')])
        self.assertIn('资产表未找到对应资源名称',self.group()['problems'])

    def test_asset_latest_full_fields_ignore_popup_display_settings(self):
        db.set_asset_info_display_fields(['规格型号'])
        self.assertEqual(self.group()['sources']['资产'][0]['name'],'网元甲')
        self.assets([('A1','更新后的名称','001','M1')])
        self.assertIn('资产表未找到对应资源名称',self.group()['problems'])

    def test_scope_includes_asset_only_items_and_excludes_other_rooms(self):
        self.assets([('A1','网元甲','001','M1'),('A2','遗漏资产','001','M2'),('A3','外部资产','999','M3')])
        groups={g['barcode']:g for g in self.groups()}
        self.assertEqual(set(groups),{'A1','A2'})
        self.assertIn('资产表资源名称在综资中未找到对应网元',groups['A2']['problems'])

    def test_multibarcode_assets_are_filtered_by_room_before_matching_and_export(self):
        self.sql("UPDATE scan_records SET expected_barcode='A1,A2,A3' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M2'),
                     ('A3','网元甲','999','外机房型号'),('EMPTY','网元甲','','地点缺失')])
        result=tw.read([self.task])
        group=result['groups'][0]
        self.assertTrue(group['manual'])
        self.assertEqual({r['barcode'] for r in group['sources']['资产']},{'A1','A2'})
        self.assertEqual(result['asset_counts']['total'],2)
        self.assertEqual({a['barcode'] for a in result['scoped_assets']},{'A1','A2'})
        ai_row=tw.agent_rows(result)[0]
        self.assertNotIn('A3',ai_row['资产信息条码'])
        self.assertNotIn('999',ai_row['资产地点编码'])
        response=main.api_export_comparison([self.task],{})
        rows=list(openpyxl.load_workbook(io.BytesIO(response.body)).active.values)
        asset=next(row for row in rows[1:] if row[1]=='资产')
        self.assertEqual(asset[2],'A1\nA2')
        self.assertEqual(asset[4],'001\n001')
        self.assertEqual(db.get_record_by_id(self.record)['expected_barcode'],'A1,A2,A3')

    def test_selected_rooms_match_identical_names_only_within_each_location(self):
        other,_=self.make_task(room='机房乙',code='002',expected='B1',actual='B1',name='网元甲')
        self.assets([('A1','网元甲','001','M1'),('B1','网元甲','002','M1'),
                     ('OUT','网元甲','999','M1'),('ZERO','网元甲','1','M1')])
        result=tw.read([self.task,other])
        self.assertEqual(len(result['groups']),2)
        for group in result['groups']:
            resource=group['sources']['综资'][0]
            self.assertEqual(len(group['sources']['资产']),1)
            self.assertEqual(group['sources']['资产'][0]['location_code'],resource['location_code'])
            self.assertEqual(group['sources']['资产'][0]['barcode'],resource['barcode'])
            self.assertNotIn('同名多条，待管理员关联',group['problems'])
        self.assertTrue(all(row['status']=='一致' for row in result['stage_one']))
        self.assertEqual(result['asset_counts']['total'],2)
        self.assertEqual(result['asset_counts']['resource_names'],2)

    def test_rack_never_falls_back_to_same_name_asset_in_another_selected_room(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?",(self.task,))
        db.create_cabinet(self.task,'odf','R1',False,'ODF-A01','',self.worker)
        self.sql("UPDATE tasks SET status='done' WHERE id=?",(self.task,))
        other,_=self.make_task(room='机房乙',code='002',expected='B1',actual='B1',name='网元乙')
        self.assets([('A1','网元甲','001','M1'),('B1','网元乙','002','M1'),('R2','ODF-A01','002','')])
        result=tw.read([self.task,other])
        current=next(r for r in result['rack_comparison'] if r['task_id']==self.task)
        self.assertEqual(current['asset_records'],[])
        self.assertEqual(current['barcode_status'],'资产表未找到对应资源名称')
        other_rack=next(r for r in result['rack_comparison'] if r['task_id']==other)
        self.assertEqual(other_rack['asset_barcodes'],'R2')
        self.assertEqual(other_rack['field_barcode'],'未反馈')
        single=tw.read([self.task])
        self.assertEqual(single['asset_counts']['rack'],0)
        self.assertEqual(len(single['rack_comparison']),1)

    def test_latest_resource_baseline_location_defines_asset_scope(self):
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','002','M1')])
        tw.replace_baseline(self.task,[dict(device_name='网元甲',expected_model='M1',expected_barcode='A2',room_code='002')],self.admin)
        result=tw.read([self.task])
        self.assertEqual(result['asset_counts']['total'],1)
        self.assertEqual([a['barcode'] for a in result['scoped_assets']],['A2'])
        self.assertEqual(result['groups'][0]['sources']['资产'][0]['barcode'],'A2')
        self.assertEqual(result['groups'][0]['sources']['现场'][0]['location_code'],'002')
        self.assertEqual(db.get_task_by_id(self.task)['room_code'],'001')

    def test_duplicate_sources_are_not_silently_selected(self):
        task,_=self.make_task(room='机房2',code='002',expected='A1',actual='A1',name='网元乙')
        groups=tw.read([self.task,task])['groups']
        self.assertEqual(len(groups),2)
        self.assertTrue(all(len(g['sources']['现场'])==1 for g in groups))
        self.assertTrue(all(g['sources']['现场'][0]['name']==g['sources']['综资'][0]['name'] for g in groups))

    def test_unrelated_upload_preserves_model_judgment_but_changed_model_resets(self):
        self.judge()
        self.assets([('A1','网元甲','001','M1'),('OUT','其他','999','新型号')])
        self.assertEqual(self.group()['model_status'],'一致')
        self.assets([('A1','网元甲','001','M2')])
        g=self.group()
        self.assertEqual(g['model_status'],'待判断')
        self.assertEqual(len(g['history']),1)
        with self.assertRaisesRegex(ValueError,'已更新'):
            tw.save_decision([self.task],g['group_key'],'0'*64,'model',dict(status='一致',note='旧页面'),self.admin)

    def test_multi_barcode_is_one_manual_entity_and_never_auto_compared(self):
        self.sql("UPDATE scan_records SET expected_barcode='A1,A2',asset_barcodes='A1,A2' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M1')])
        groups=self.groups()
        self.assertEqual(len(groups),1)
        g=groups[0]
        self.assertTrue(g['manual'])
        self.assertEqual(tw.read([self.task])['stage_one'][0]['status'],'多条码人工核对')
        self.assertEqual(g['name_status'],'待判断')
        self.assertFalse(g['can_create_adjustment'])
        values={s:dict(barcode='A1,A2',name='网元甲',location_code='001',model='M1') for s in tw.SYSTEMS}
        decision=dict(values=values,
                      pair_statuses={pair:{label:'一致' for label in checks} for pair,checks in g['checks'].items()},
                      note='人工查阅三方资料')
        tw.save_decision([self.task],g['group_key'],g['fingerprint'],'manual',decision,self.admin)
        self.assertTrue(self.groups()[0]['consistent'])
        self.assets([('A1','名称改变','001','M1'),('A2','网元甲','001','M1')])
        self.assertEqual(self.groups()[0]['name_status'],'待判断')

    def test_no_barcode_feedback_is_shown_once_without_inventing_an_asset_key(self):
        self.sql('UPDATE scan_records SET asset_no_barcode=1,asset_barcodes=? WHERE id=?',('',self.record))
        groups=self.groups()
        self.assertEqual(len(groups),1)
        g=groups[0]
        self.assertEqual(g['barcode'],'A1')
        self.assertFalse(g['sources']['现场'])
        pending=g['pending_sources']['现场']
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['record_id'],self.record)
        self.assertEqual(pending[0]['barcode'],'')
        self.assertEqual(pending[0]['barcode_state'],'现场无条码')
        first=tw.read([self.task])['stage_one'][0]
        self.assertEqual(first['status'],'现场条码待补录')
        self.assertEqual(first['field']['model'],'M1')
        self.assertEqual(tw.agent_rows(tw.read([self.task]))[0]['综资现场网元关联与条码核对'],'现场条码待补录')
        self.assertEqual(pending[0]['model'],'M1')
        self.assertEqual(g['name_status'],'一致')
        self.assertTrue(any('登记为无资产条码' in p for p in g['problems']))
        self.assertNotIn('资产条码现场缺失',g['problems'])
        self.assertNotIn('资产条码资产缺失',g['problems'])
        self.assertFalse(g['consistent'])
        with self.assertRaisesRegex(ValueError,'先补录'):
            self.judge()

    def test_missing_code_is_not_attached_by_name_to_another_device(self):
        other,record=self.make_task(room='另机房',code='002',expected='',actual='OTHER',name='网元甲')
        self.sql("UPDATE scan_records SET asset_barcodes='',asset_no_barcode=1 WHERE id=?",(record,))
        groups=tw.read([self.task,other])['groups']
        g=next(g for g in groups if any(r['task_id']==self.task for r in tw.source_records(g,'综资')))
        self.assertFalse(g['pending_sources']['现场'])
        other_group=next(g for g in groups if any(r['record_id']==record for r in tw.source_records(g,'综资')))
        self.assertEqual([r['record_id'] for r in other_group['pending_sources']['现场']],[record])
        self.assertFalse(other_group['consistent'])

    def test_resource_missing_code_uses_same_record_reference_without_false_missing(self):
        self.sql("UPDATE scan_records SET expected_barcode='' WHERE id=?",(self.record,))
        groups=self.groups()
        self.assertEqual(len(groups),1)
        g=groups[0]
        self.assertEqual(g['barcode'],'A1')
        self.assertFalse(g['sources']['综资'])
        self.assertEqual(g['pending_sources']['综资'][0]['name'],'网元甲')
        self.assertNotIn('资产条码综资缺失',g['problems'])
        self.assertFalse(g['consistent'])

    def test_uncoded_feedback_can_be_corrected_and_old_orphan_case_is_relinked(self):
        self.sql("UPDATE scan_records SET asset_barcodes='',asset_no_barcode=1 WHERE id=?",(self.record,))
        g=self.group()
        case=self.case()
        # 兼容升级前由无条码独立组建立的工单，不重复建单。
        conn=db.get_conn()
        try:
            row=conn.execute('SELECT comparison_json FROM ai_adjustment_tasks WHERE id=?',(case,)).fetchone()
            import json
            meta=json.loads(row['comparison_json'])
            meta['group_key']=f'batch:empty:现场:{self.task}:{self.record}'
            conn.execute('UPDATE ai_adjustment_tasks SET comparison_json=? WHERE id=?',(json.dumps(meta),case))
            conn.commit()
        finally:conn.close()
        self.assertEqual(self.group()['case']['id'],case)
        with self.assertRaisesRegex(ValueError,'已有整改单'):
            self.case()
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        item=tw.worker_case(db.get_my_adjustment_tasks(self.worker)[0])
        self.assertEqual(item['field_records'][0]['id'],self.record)
        self.action(case,'field_complete',self.worker,records=[dict(record_id=self.record,actual_model='M1',asset_barcodes='A1')])
        g=self.group()
        self.assertFalse(g['pending_association'])
        self.assertEqual(g['sources']['现场'][0]['barcode'],'A1')
        self.judge()
        self.assertEqual(self.action(case,'verify')['status'],'done')

    def test_export_and_ai_keep_uncoded_feedback_as_unmatched_reference(self):
        self.sql("UPDATE scan_records SET asset_barcodes='',asset_no_barcode=1 WHERE id=?",(self.record,))
        response=main.api_export_comparison([self.task],{})
        workbook=openpyxl.load_workbook(io.BytesIO(response.body))
        self.assertEqual(workbook.sheetnames,['三系统比对','传输综合柜核对'])
        values=list(workbook.active.values)
        self.assertEqual(values[1][0],'网元甲')
        self.assertEqual(values[1][2],'A1')
        field=next(r for r in values[1:] if r[1]=='现场')
        self.assertEqual(field[2],'现场无条码')
        self.assertEqual(field[4],'001')
        self.assertIn('待补录',field[-1])
        self.assertIn('现场已有记录',values[1][7])
        rows=tw.agent_rows(tw.read([self.task]))
        self.assertEqual(rows[0]['现场反馈数'],1)
        self.assertEqual(rows[0]['现场记录数'],0)
        self.assertEqual(rows[0]['现场网元名称'],'网元甲')
        self.assertEqual(rows[0]['现场资产条码'],'')
        self.assertIn('待补录',rows[0]['条码关联状态'])
        payload,_=apply_context_policy(dict(rows=rows,data_sources=list(tw.SYSTEMS)))
        self.assertIn('条码关联状态',payload['rows'][0])

    def test_resources_update_preserves_task_field_scans_and_historical_baseline(self):
        before=db.get_record_by_id(self.record)
        before_task=db.get_task_by_id(self.task)
        tw.replace_baseline(self.task,[dict(device_name='规范网元甲',expected_model='M2',expected_barcode='A1',room_code='001')],self.admin)
        g=self.group()
        self.assertEqual(g['sources']['综资'][0]['name'],'规范网元甲')
        self.assertEqual(g['sources']['现场'][0]['name'],'规范网元甲')
        self.assertEqual(db.get_record_by_id(self.record),before)
        self.assertEqual(db.get_task_by_id(self.task)['field_completed_at'],before_task['field_completed_at'])
        self.assertEqual(len(db.get_all_tasks()),1)
        self.assertEqual(g['sources']['现场'][0]['model'],'M1')
        first=tw.read([self.task])['stage_one'][0]
        self.assertEqual(first['status'],'一致')
        self.assertEqual(first['resource']['model'],'M2')
        self.assertEqual(first['field']['model'],'M1')

    def test_updated_resource_barcode_changes_first_step_without_new_task(self):
        raw=db.get_record_by_id(self.record)
        tw.replace_baseline(self.task,[dict(device_name='网元甲',expected_model='M1',expected_barcode='A2',room_code='001')],self.admin)
        result=tw.read([self.task])
        self.assertEqual(result['stage_one'][0]['status'],'条码不一致')
        self.assertEqual(result['stage_one'][0]['resource']['barcode'],'A2')
        self.assertEqual(result['stage_one'][0]['field']['barcode'],'A1')
        self.assertEqual({g['barcode'] for g in result['groups']},{'A2'})
        self.assertEqual(db.get_record_by_id(self.record),raw)
        self.assertEqual(len(db.get_all_tasks()),1)

    def test_only_admin_handling_does_not_dispatch_and_cannot_close_with_note_only(self):
        self.assets([('A1','错误名称','001','M1')])
        case=self.case()
        c=self.action(case,'approve',needs_admin=True)
        self.assertIsNone(c['assigned_to'])
        self.assertFalse(db.get_my_adjustment_tasks(self.worker))
        self.action(case,'admin_complete')
        with self.assertRaisesRegex(ValueError,'仍有问题'):
            self.action(case,'verify')
        self.assets()
        self.judge()
        c=self.action(case,'verify')
        self.assertEqual(c['status'],'done')
        self.assertFalse(c['comparison']['snapshot']['consistent'])
        self.assertTrue(c['comparison']['accepted_snapshot']['consistent'])
        self.assertEqual(len(c['history']),4)
        with self.assertRaises(ValueError):
            self.action(case,'verify')

    def test_active_cases_group_all_problems_and_prevent_duplicate_creation(self):
        self.assets([('A1','错误名称','002','M2')])
        case=self.case()
        with self.assertRaisesRegex(ValueError,str(case)):
            self.case()
        self.action(case,'reject_review')
        self.assertNotEqual(self.case(),case)

    def test_mixed_handling_waits_for_both_parties_and_return_is_case_only(self):
        self.assets([('A1','错误名称','001','M1')])
        case=self.case()
        completed_at=db.get_task_by_id(self.task)['field_completed_at']
        self.action(case,'approve',needs_admin=True,needs_field=True,assigned_to=self.worker)
        c=self.action(case,'field_complete',self.worker)
        self.assertEqual(c['status'],'dispatched')
        self.assertTrue(c['comparison']['field_done'])
        with self.assertRaises(ValueError):
            self.action(case,'verify')
        self.action(case,'admin_complete')
        c=self.action(case,'return',return_admin=True)
        self.assertEqual(c['status'],'dispatched')
        self.assertTrue(c['comparison']['field_done'])
        self.assertFalse(c['comparison']['admin_done'])
        self.assertEqual(db.get_task_by_id(self.task)['status'],'done')
        self.assertEqual(db.get_task_by_id(self.task)['field_completed_at'],completed_at)

    def test_field_updates_only_case_records_atomically_without_unlocking_task(self):
        self.judge('不一致')
        case=self.case()
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        with self.assertRaisesRegex(ValueError,'本整改单'):
            self.action(case,'field_complete',self.worker,records=[dict(record_id=self.record,actual_model='M2',asset_barcodes='A1'),dict(record_id=99999,actual_model='M2',asset_barcodes='A1')])
        self.assertEqual(db.get_report(self.task)[0]['actual_model'],'M1')
        self.action(case,'field_complete',self.worker,records=[dict(record_id=self.record,actual_model='M2',asset_barcodes='A1')])
        self.assertEqual(db.get_report(self.task)[0]['actual_model'],'M2')
        self.assertEqual(db.get_report(self.task)[0]['original_actual_model'],'M1')
        self.assertEqual(db.get_task_by_id(self.task)['status'],'done')
        self.assertEqual(self.group()['field_model_status'],'待判断')

    def test_wrong_worker_and_cross_area_assignment_rejected(self):
        self.judge('不一致')
        case=self.case()
        other=db.create_user('w2','其他人员','worker','北区')['id']
        with self.assertRaisesRegex(ValueError,'片区'):
            self.action(case,'approve',needs_field=True,assigned_to=other)
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        with self.assertRaisesRegex(ValueError,'自己的'):
            self.action(case,'field_complete',other)

    def test_missing_location_blocks_formal_comparison_and_can_fill_legacy_only(self):
        self.sql("UPDATE tasks SET room_code='' WHERE id=?",(self.task,))
        r=tw.read([self.task])
        self.assertTrue(r['warnings'])
        self.assertFalse(any(g['can_create_adjustment'] for g in r['groups']))
        tw.set_room_code(self.task,'001',self.admin)
        self.assertFalse(tw.read([self.task])['warnings'])
        with self.assertRaisesRegex(ValueError,'不能覆盖'):
            tw.set_room_code(self.task,'002',self.admin)

    def test_location_validation_and_template_leading_zero(self):
        wb=openpyxl.load_workbook(io.BytesIO(main.api_upload_template({}).body))
        self.assertEqual(list(next(wb.active.values)), list(main.TASK_TEMPLATE_HEADERS))
        ws=wb.active
        self.assertEqual(ws['C2'].value, 'PTN')
        ws.append(['另机房','南区','PTN','设备','M','B1','R','在网','0002'])
        devices=main._parse_excel_devices(ws)['另机房']
        self.assertEqual(devices[0]['room_code'],'0002')
        ws.append(['第三机房','南区','PTN','设备2','M','B2','R','在网','0002'])
        with self.assertRaisesRegex(HTTPException,'多个机房'):
            main._parse_excel_devices(ws)

    def test_three_way_ai_context_masks_all_identifiers_and_keeps_judgments(self):
        result=tw.read([self.task])
        rows=tw.agent_rows(result)
        self.assertEqual(rows[0]['资产资源名称'],'网元甲')
        masked,_=ai_agent.desensitize_rows(rows)
        row=masked[0]
        self.assertEqual(row['资产地点编码'],row['现场地点编码'])
        self.assertEqual(row['资产资源名称'],row['现场网元名称'])
        self.assertEqual(row['资产信息条码'],row['现场资产条码'])
        payload,meta=apply_context_policy(dict(rows=masked,data_sources=['资产','综资','现场']))
        self.assertEqual(payload['rows'][0]['型号人工判断'],'待判断')
        self.assertEqual(meta['missing_data_sources'],[])
        rules=run_rules(payload['rows'])
        self.assertTrue(all('规格型号不一致' not in i['问题类型'] for i in rules))
        with patch.object(ai_agent,'_external_agent_url',return_value=''),patch.object(ai_agent,'_call_deepseek',return_value=[]):
            ai=ai_agent.analyze_inventory([],comparison=result)
        self.assertIn('资产信息条码',ai['desensitized_payload'])
        self.assertNotIn('网元甲',ai['desensitized_payload'])

    def test_export_keeps_three_system_originals_and_manual_decisions(self):
        self.judge()
        response=main.api_export_comparison([self.task],{})
        wb=openpyxl.load_workbook(io.BytesIO(response.body))
        self.assertEqual(wb.sheetnames,['三系统比对','传输综合柜核对'])
        ws=wb.active
        values=list(ws.values)
        self.assertEqual(len(values),4)
        self.assertEqual([v[1] for v in values[1:]],['综资','现场','资产'])
        self.assertEqual([v[2] for v in values[1:]],['A1','A1','A1'])
        self.assertEqual(values[1][0],'网元甲')
        self.assertIsNone(values[2][0])
        self.assertIsNone(values[3][0])
        self.assertIn('A2:A4',{str(r) for r in ws.merged_cells.ranges})
        self.assertIn('设备型号一致',values[1][6])
        self.assertIn('规格型号一致',values[1][6])
        self.assertNotIn('网元或资源名称',values[0])

    def test_merged_export_keeps_multiple_asset_rows_in_one_net_group(self):
        self.sql("UPDATE scan_records SET expected_barcode='A1,A2' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M2')])
        response=main.api_export_comparison([self.task],{})
        ws=openpyxl.load_workbook(io.BytesIO(response.body)).active
        values=list(ws.values)
        self.assertEqual(len(values),4)
        self.assertEqual([row[1] for row in values[1:]],['综资','现场','资产'])
        self.assertEqual(values[1][2],'A1,A2')
        self.assertEqual(values[3][2],'A1\nA2')
        self.assertEqual(values[3][5],'M1\nM2')

    def test_export_uses_latest_manual_values_for_each_system(self):
        self.sql("UPDATE scan_records SET expected_barcode='A1,A2' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M2')])
        group=self.groups()[0]
        for revision in ('初次','最终'):
            manual={system:dict(barcode=f'{revision}-{system}-条码',name=f'{revision}核对网元',
                                location_code=f'{revision}-{system}-地点',model=f'{revision}-{system}-型号')
                    for system in tw.SYSTEMS}
            decision=dict(values=manual,
                          pair_statuses={pair:{label:'一致' for label in checks} for pair,checks in group['checks'].items()},
                          note=f'{revision}人工核对')
            tw.save_decision([self.task],group['group_key'],group['fingerprint'],'manual',decision,self.admin)

        response=main.api_export_comparison([self.task],{})
        ws=openpyxl.load_workbook(io.BytesIO(response.body)).active
        rows=list(ws.values)
        self.assertEqual(rows[1][0],'最终核对网元')
        for row in rows[1:]:
            system=row[1]
            self.assertEqual(row[2],manual[system]['barcode'])
            self.assertEqual(row[4],manual[system]['location_code'])
            self.assertEqual(row[5],manual[system]['model'])
            self.assertEqual(row[3],'不参与' if system=='资产' else 'R1')
            self.assertIn('管理员人工核对',row[11])
        self.assertIn('资产条码一致',rows[1][6])

    def test_export_does_not_reuse_manual_values_after_source_changes(self):
        self.sql("UPDATE scan_records SET expected_barcode='A1,A2' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('A2','网元甲','001','M1')])
        group=self.groups()[0]
        decision=dict(values={system:dict(barcode='旧人工条码',name='旧人工名称',location_code='旧人工地点',model='旧人工型号')
                              for system in tw.SYSTEMS},
                      pair_statuses={pair:{label:'一致' for label in checks} for pair,checks in group['checks'].items()},
                      note='数据更新前的核对')
        tw.save_decision([self.task],group['group_key'],group['fingerprint'],'manual',decision,self.admin)
        self.assets([('A1','网元甲','001','M2'),('A2','网元甲','001','M2')])

        response=main.api_export_comparison([self.task],{})
        rows=list(openpyxl.load_workbook(io.BytesIO(response.body)).active.values)
        self.assertEqual(rows[1][0],'网元甲')
        asset=next(row for row in rows[1:] if row[1]=='资产')
        self.assertEqual(asset[2],'A1\nA2')
        self.assertEqual(asset[5],'M2\nM2')
        self.assertNotIn('旧人工',str(rows))

    def test_export_uses_latest_admin_correction_for_field_data(self):
        record=db.get_report(self.task)[0]
        for revision in ('初次','最终'):
            db.create_record_correction(self.record,{**record,'actual_model':f'{revision}型号',
                                        'asset_barcodes':f'{revision}条码','rack_no':f'{revision}柜位'},
                                        '根据现场凭证修正','已核实',self.admin)
        response=main.api_export_comparison([self.task],{})
        rows=list(openpyxl.load_workbook(io.BytesIO(response.body)).active.values)
        field=next(row for row in rows[1:] if row[1]=='现场')
        self.assertEqual(field[2],'最终条码')
        self.assertEqual(field[3],'最终柜位')
        self.assertEqual(field[5],'最终型号')
        self.assertIn('管理员修正',field[11])

    def test_model_change_then_restore_does_not_resurrect_old_judgment(self):
        self.judge()
        self.assets([('A1','网元甲','001','M2')])
        self.assets([('A1','网元甲','001','M1')])
        self.assertEqual(self.group()['model_status'],'待判断')

    def test_baseline_and_field_model_restore_also_reset_judgment(self):
        self.judge()
        def baseline(model):
            tw.replace_baseline(self.task,[dict(device_name='网元甲',expected_model=model,expected_barcode='A1',room_code='001')],self.admin)
        baseline('M1')
        self.assertEqual(self.group()['model_status'],'一致')
        baseline('M2'); baseline('M1')
        self.assertEqual(self.group()['model_status'],'待判断')
        self.judge()
        values=db.get_report(self.task)[0]
        for model in ['M2','M1']:
            db.create_record_correction(self.record,{**values,'actual_model':model},'录入错误','凭证核实',self.admin)
        self.assertEqual(self.group()['model_status'],'待判断')

    def test_new_feedback_for_asset_only_case_and_no_duplicate_submission(self):
        self.assets([('A1','网元甲','001','M1'),('A2','新增网元','001','M2')])
        g=self.group('A2')
        case=tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)
        self.action(case,'approve',needs_admin=True,needs_field=True,assigned_to=self.worker)
        c=self.action(case,'field_complete',self.worker,new_record=dict(task_id=self.task,device_name='新增网元',actual_model='M2',asset_barcodes='A2',cabinet_no_barcode=True,rack_no='R2'))
        self.assertTrue(c['comparison']['field_record_ids'])
        self.assertEqual(len(self.group('A2')['sources']['现场']),1)
        worker_case=tw.worker_case(db.get_my_adjustment_tasks(self.worker)[0])
        self.assertEqual(len(worker_case['field_records']),1)
        with self.assertRaises(ValueError):
            self.action(case,'field_complete',self.worker,new_record=dict(task_id=self.task,device_name='重复',actual_model='M2',asset_barcodes='A2',rack_no='R2',cabinet_no_barcode=True))
        self.assertEqual(len(self.group('A2')['sources']['现场']),1)
        tw.replace_baseline(self.task,[dict(device_name='网元甲',expected_model='M1',expected_barcode='A1',room_code='001',device_extra={'设备机架':'R1'}),dict(device_name='新增网元',expected_model='M2',expected_barcode='A2',room_code='001',device_extra={'设备机架':'R2'})],self.admin)
        self.judge(barcode='A2')
        self.action(case,'admin_complete')
        self.assertEqual(self.action(case,'verify')['status'],'done')
        self.assertEqual(db.get_task_by_id(self.task)['status'],'done')

    def test_admin_renamed_surplus_is_not_overwritten_by_baseline_owner(self):
        self.assets([('A1','网元甲','001','M1'),('A2','新增网元','001','M2')])
        g=self.group('A2')
        case=tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)
        self.action(case,'approve',needs_admin=True,needs_field=True,assigned_to=self.worker)
        item=self.action(case,'field_complete',self.worker,new_record=dict(task_id=self.task,device_name='新增网元',actual_model='M2',asset_barcodes='A2',cabinet_no_barcode=True,rack_no='R2'))
        record_id=item['comparison']['field_record_ids'][0]
        tw.replace_baseline(self.task,[dict(device_name='网元甲',expected_barcode='A1',expected_model='M1',room_code='001'),
                                      dict(device_name='新增网元',expected_barcode='A2',expected_model='M2',room_code='001')],self.admin)
        record=next(r for r in db.get_report(self.task) if r['id']==record_id)
        db.create_record_correction(record_id,{**record,'device_name':'管理员核实后的名称'},'根据现场凭证修正','确认名称录入有误',self.admin)
        fields=[r for group in self.groups() for r in tw.source_records(group,'现场') if r.get('record_id')==record_id]
        self.assertEqual(len(fields),1)
        self.assertEqual(fields[0]['name'],'管理员核实后的名称')

    def test_historical_unscanned_can_only_be_filled_through_assigned_case(self):
        self.sql("UPDATE scan_records SET scanned_at=NULL,match_status='未扫',actual_model='',asset_barcodes='' WHERE id=?",(self.record,))
        case=self.case()
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        self.action(case,'field_complete',self.worker,records=[dict(record_id=self.record,actual_model='M1',asset_barcodes='A1',rack_no='R1',cabinet_no_barcode=True)])
        self.assertEqual(len(self.group()['sources']['现场']),1)
        self.assertTrue(db.get_report(self.task)[0]['scanned_at'])
        self.assertEqual(self.judge()['model_status'],'一致')
        self.assertEqual(self.action(case,'verify')['status'],'done')

    def test_correcting_wrong_barcode_can_follow_original_device_to_new_group(self):
        self.sql("UPDATE scan_records SET asset_barcodes='OUT' WHERE id=?",(self.record,))
        self.assets([('A1','网元甲','001','M1'),('OUT','外部设备','999','M2')])
        g=self.group('A1')
        case=tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        self.action(case,'field_complete',self.worker,records=[dict(record_id=self.record,actual_model='M1',asset_barcodes='A1')])
        self.judge()
        current=tw.case_detail(case)['current']
        self.assertEqual(current['barcode'],'A1')
        self.assertEqual(self.action(case,'verify')['status'],'done')

    def test_multi_entity_keeps_identity_when_baseline_model_changes(self):
        devices=[dict(device_name='新多资产设备',expected_model='M2',expected_barcode='A2,A3',room_code='001')]
        tw.replace_baseline(self.task,devices,self.admin)
        self.assets([('A2','新多资产设备','001','M2'),('A3','新多资产设备','001','M2')])
        g=next(g for g in self.groups() if g['manual'])
        decision=dict(values={s:dict(barcode='A2,A3',name='新多资产设备',location_code='001',model='M2') for s in tw.SYSTEMS},
                      pair_statuses={pair:{label:'不一致' if pair=='综资现场' and label=='资产条码' else '待判断' for label in checks} for pair,checks in g['checks'].items()},
                      note='需现场核实')
        tw.save_decision([self.task],g['group_key'],g['fingerprint'],'manual',decision,self.admin)
        g=next(g for g in self.groups() if g['manual'])
        case=tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)
        self.action(case,'approve',needs_field=True,assigned_to=self.worker)
        self.action(case,'field_complete',self.worker,new_record=dict(task_id=self.task,device_name='新多资产设备',actual_model='M2',asset_barcodes='A2,A3',rack_no='R2',cabinet_no_barcode=True))
        self.assertEqual(len([g for g in self.groups() if g['manual']]),1)
        devices[0]['expected_model']='M3'
        tw.replace_baseline(self.task,devices,self.admin)
        new=next(g for g in self.groups() if g['manual'])
        self.assertEqual(new['group_key'],g['group_key'])
        self.assertEqual(new['model_status'],'待判断')

    def test_task_summary_includes_location_and_filled_legacy_code(self):
        self.assertEqual(main.api_summary({})[0]['room_code'],'001')
        self.assertEqual(db.get_all_tasks()[0]['room_code'],'001')

    def test_concurrent_creation_and_verification_only_transition_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.judge('不一致')
        g=self.group()
        barrier=Barrier(2)
        def create():
            barrier.wait()
            try:
                return tw.create_case([self.task],g['group_key'],g['fingerprint'],self.admin)
            except ValueError:
                return None
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:create(),range(2)))
        case=next(i for i in results if i)
        self.assertEqual(sum(i is not None for i in results),1)
        self.action(case,'approve',needs_admin=True)
        self.action(case,'admin_complete')
        self.judge()
        barrier=Barrier(2)
        def verify():
            barrier.wait()
            try:
                return self.action(case,'verify')['status']
            except ValueError:
                return None
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:verify(),range(2)))
        self.assertEqual(results.count('done'),1)
        self.assertEqual(sum(h['action']=='case.verify' for h in tw.case_detail(case)['history']),1)

    def test_active_case_blocks_task_void_and_history_survives_later_void(self):
        self.judge('不一致')
        case=self.case()
        with self.assertRaisesRegex(ValueError,'未闭环'):
            db.delete_task(self.task,'不能绕过验收')
        self.action(case,'approve',needs_admin=True)
        self.action(case,'admin_complete')
        self.judge()
        self.action(case,'verify')
        db.delete_task(self.task,'完成后归档')
        archived=tw.case_detail(case)
        self.assertEqual(archived['status'],'done')
        self.assertTrue(archived['comparison']['accepted_snapshot']['consistent'])
        self.assertTrue(archived['warnings'])


class ThreeWayApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture=ThreeWayWorkflowTests()
        self.fixture.setUp()
        import os
        self.settings=patch.dict(os.environ,{'INVENTORY_SECRET_KEY':'unit-test-key-not-for-production-12345','INVENTORY_ADMIN_IP_RESTRICTION_ENABLED':'0'})
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.fixture.tearDown()

    async def request(self, method, url, user_id, payload=None):
        import asyncio,json
        from backend.auth import create_access_token
        token=create_access_token(db.get_user_for_auth(user_id))
        path,_,query=url.partition('?')
        data=json.dumps(payload or {}).encode()
        messages=[]
        first=True
        async def receive():
            nonlocal first
            if first:
                first=False
                return {'type':'http.request','body':data,'more_body':False}
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':method,'scheme':'http','path':path,'raw_path':path.encode(),'query_string':query.encode(),'root_path':'','headers':[(b'authorization',('Bearer '+token).encode()),(b'content-type',b'application/json')],'client':('127.0.0.1',12345),'server':('localhost',8000)}
        await asyncio.wait_for(main.app(scope,receive,send),5)
        status=next(m['status'] for m in messages if m['type']=='http.response.start')
        body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
        return status,json.loads(body) if body else None

    async def test_typed_decision_case_endpoints_and_worker_permissions(self):
        f=self.fixture
        status,result=await self.request('GET',f'/api/reconciliation?task_ids={f.task}',f.admin)
        self.assertEqual(status,200)
        g=result['groups'][0]
        base=dict(task_ids=[f.task],group_key=g['group_key'],fingerprint=g['fingerprint'])
        status,_=await self.request('POST','/api/reconciliation/decisions',f.worker,{**base,'kind':'model','decision':{'field_status':'一致','asset_status':'一致','note':'越权'}})
        self.assertEqual(status,403)
        status,_=await self.request('POST','/api/reconciliation/decisions',f.admin,{**base,'kind':'model','decision':{'field_status':'不一致','asset_status':'一致','note':'管理员核实'}})
        self.assertEqual(status,200)
        status,case=await self.request('POST','/api/reconciliation/adjustments',f.admin,base)
        self.assertEqual(status,200)
        case_id=case['item']['id']
        status,_=await self.request('POST',f'/api/reconciliation/cases/{case_id}/approve',f.admin,{'note':'现场处理','needs_field':True,'assigned_to':f.worker})
        self.assertEqual(status,200)
        status,rows=await self.request('GET','/api/adjustments/my',f.worker)
        self.assertEqual(status,200)
        self.assertEqual(len(rows[0]['field_records']),1)
        self.assertNotIn('snapshot',rows[0]['comparison'])
        status,result=await self.request('POST',f'/api/adjustments/{case_id}/complete',f.worker,{'completion_note':'现场整改结果','records':[{'record_id':f.record,'actual_model':'M2','asset_barcodes':'A1'}]})
        self.assertEqual(status,200)
        self.assertEqual(result['item']['status'],'pending_verify')
        self.assertNotIn('snapshot',result['item']['comparison'])
        self.assertNotIn('current_groups',result['item'])
        status,_=await self.request('POST',f'/api/ai/adjustments/{case_id}/verify',f.admin,{'verification_note':'旧入口也不能越过三方校验'})
        self.assertEqual(status,400)

    async def test_invalid_payload_is_validation_error_and_completed_rooms_can_cross_batches(self):
        f=self.fixture
        status,_=await self.request('POST','/api/reconciliation/decisions',f.admin,{'task_ids':[],'group_key':'bad','fingerprint':''})
        self.assertEqual(status,422)
        g=f.group()
        status,_=await self.request('POST','/api/reconciliation/decisions',f.admin,{'task_ids':[f.task],'group_key':g['group_key'],'fingerprint':g['fingerprint'],'kind':'manual','decision':{'values':None,'note':'无效参数'}})
        self.assertEqual(status,422)
        other,_=f.make_task(room='另批机房',code='002',batch='another-batch')
        status,result=await self.request('GET',f'/api/reconciliation?task_ids={f.task}&task_ids={other}',f.admin)
        self.assertEqual(status,200)
        self.assertEqual({task['id'] for task in result['tasks']},{f.task,other})


if __name__ == '__main__':
    unittest.main()
