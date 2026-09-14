"""Synthetic Word export tests; example.org jobs are not real vacancies."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_evidence_basis import build
from jobmatch_runtime.report import render_report, MODE_FIELDS
from test_integrations import corpus_fixture
from test_validate_jobs import fixture, NOW


def report_fixture(count=20):
    data = fixture()
    data['synthetic'] = True
    data['target_companies'] = count
    data['corpus'] = corpus_fixture(count=count, employers=count)
    data['evidence_basis'] = build(data['corpus'], NOW)['evidence_basis']
    base = data['jobs'][0]
    data['jobs'] = []
    data['report_summary'] = '模拟测试：按证据对照筛选岗位；不是真实求职建议。'
    data['resume_variants'], data['action_plan'] = [], []
    for index, row in enumerate(data['corpus']['records']):
        job = copy.deepcopy(base)
        for field in ('corpus_id', 'company_key', 'company', 'title', 'jd_url'):
            job[field] = row[field]
        job['id'] = f'synthetic-{index}'
        job['apply_url'] = f'https://example{index}.org/apply/{index}'
        job['details'] = {'salary': '模拟薪酬未披露', 'deadline': '模拟截止未披露',
                          'mode_fields': {'graduation_cohort': '2027 届（模拟）',
                                          'recruitment_batch': '秋招（模拟）',
                                          'graduate_eligibility': '模拟资格已核查'}}
        for rewrite_index, rewrite in enumerate(job['rewrites']):
            rewrite['text'] = f'企业{index}独有改写{rewrite_index}（模拟）'
        for mapping_index, mapping in enumerate(job['mappings']):
            mapping['requirement'] = f'企业{index}独有要求{mapping_index}（模拟）'
        data['jobs'].append(job)
        variant_id = f'variant-{index % 3}'
        variant = next((v for v in data['resume_variants'] if v['id'] == variant_id), None)
        if variant is None:
            variant = {'id': variant_id, 'name': f'模拟结构{index % 3}', 'job_ids': [], 'changes': []}
            data['resume_variants'].append(variant)
        variant['job_ids'].append(job['id'])
        variant['changes'].append(f'突出模拟项目{index}的分析方法')
        data['action_plan'].append({'job_id': job['id'], 'action': f'核查模拟企业{index}材料后投递',
                                    'materials': ['按该模拟职位要求提供成绩单'],
                                    'resume_variant_id': variant_id})
    return data


class WordReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'synthetic-report.docx'

    def test_twenty_companies_full_content_links_and_no_fill(self):
        data = report_fixture()
        result = render_report(data, self.output, now=NOW)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'render_pending')
        self.assertEqual(result['companies'], 20)
        self.assertEqual(result['visual_review'], 'pending')
        with ZipFile(self.output) as archive:
            xml = archive.read('word/document.xml')
            root = ET.fromstring(xml)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = ''.join(root.itertext())
            self.assertIn('模拟测试数据', text)
            self.assertIn('page 1, project 1', text)
            self.assertIn('要求与经历对照', text)
            self.assertIn('招聘原文', text)
            self.assertIn('可直接使用的事实性改写', text)
            self.assertIn('材料与申请动作', text)
            for index in range(20):
                for mapping in data['jobs'][index]['mappings']:
                    self.assertIn(mapping['requirement'], text)
                for rewrite in data['jobs'][index]['rewrites']:
                    self.assertIn(rewrite['text'], text)
                self.assertIn(data['corpus']['records'][index]['source_text'], text)
                self.assertIn(data['action_plan'][index]['action'], text)
            self.assertFalse(root.findall('.//w:shd', ns))
            tables = root.findall('.//w:tbl', ns)
            self.assertEqual(len(tables), 60)
            for tab in tables:
                for size in tab.findall('.//w:sz', ns):
                    self.assertEqual(size.get('{%s}val' % ns['w']), '21')
                self.assertEqual(tab.find('./w:tblPr/w:tblLayout', ns).get('{%s}type' % ns['w']), 'fixed')
            sect = root.find('.//w:sectPr/w:pgSz', ns)
            self.assertAlmostEqual(int(sect.get('{%s}w' % ns['w'])), 11906, delta=2)
            styles = ET.fromstring(archive.read('word/styles.xml'))
            normal = next(s for s in styles.findall('w:style', ns) if s.get('{%s}styleId' % ns['w']) == 'Normal')
            self.assertEqual(normal.find('./w:rPr/w:sz', ns).get('{%s}val' % ns['w']), '24')
            relations = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
            targets = {r.get('Target') for r in relations}
            for job in data['jobs']:
                self.assertIn(job['jd_url'], targets)
                self.assertIn(job['apply_url'], targets)
            bookmarks = {x.get('{%s}name' % ns['w']) for x in root.findall('.//w:bookmarkStart', ns)}
            for anchor in root.findall('.//w:hyperlink', ns):
                if anchor.get('{%s}anchor' % ns['w']):
                    self.assertIn(anchor.get('{%s}anchor' % ns['w']), bookmarks)
            footer = archive.read('word/footer1.xml').decode()
            self.assertIn('PAGE', footer)

    def test_never_overwrite(self):
        self.output.write_bytes(b'original resume bytes')
        result = render_report(report_fixture(1), self.output, NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(self.output.read_bytes(), b'original resume bytes')

    def test_formal_missing_extension_blocks_without_file(self):
        for field in ('report_summary', 'resume_variants', 'action_plan'):
            with self.subTest(field=field):
                data = report_fixture(1); del data[field]
                result = render_report(data, self.output, NOW)
                self.assertFalse(result['ok'])
                self.assertFalse(self.output.exists())

    def test_variant_action_linkage_and_mode_required(self):
        for mutation in ('mode', 'variant', 'materials', 'coverage'):
            data = report_fixture(1)
            if mutation == 'mode':
                del data['jobs'][0]['details']['mode_fields']['graduation_cohort']
            elif mutation == 'variant':
                data['action_plan'][0]['resume_variant_id'] = 'missing'
            elif mutation == 'materials':
                data['action_plan'][0]['materials'] = []
            else:
                data['resume_variants'][0]['job_ids'] = ['missing']
            self.assertFalse(render_report(data, self.output, NOW)['ok'], mutation)
            self.assertFalse(self.output.exists())

    def test_stage_missing_presentation_fields_explicitly_diagnosed(self):
        data = report_fixture(1); del data['report_summary']
        result = render_report(data, self.output, NOW, stage=True)
        self.assertTrue(result['ok'], result)
        self.assertTrue(any('report_summary' in e for e in result['diagnostics']))
        with ZipFile(self.output) as archive:
            self.assertIn('摘要尚未提供', archive.read('word/document.xml').decode())

    def test_stage_never_waives_evidence_error(self):
        data = report_fixture(1)
        data['jobs'][0]['rewrites'][0]['evidence_refs'] = ['not-real']
        result = render_report(data, self.output, NOW, stage=True)
        self.assertFalse(result['ok'])
        self.assertFalse(self.output.exists())

    def test_shortfall_requires_explicit_evidence_gate_flag(self):
        data = report_fixture(1); data['target_companies'] = 20
        gate = {'ok': False, 'evidence_ok': True, 'companies': 1, 'target': 20,
                'errors': ['company shortfall: 1/20; stage report only']}
        with patch('jobmatch_runtime.report.validate', return_value=gate):
            self.assertFalse(render_report(data, self.output, NOW)['ok'])
            result = render_report(data, self.output, NOW, stage=True)
            self.assertTrue(result['ok'], result)
        self.assertEqual(result['companies'], 1)

    def test_proposed_stage_prints_full_details_without_confirmed_count(self):
        data = report_fixture(1)
        data['runtime_version'] = '1.0'
        data['jobs'][0].update(selected=False, proposed_selection=True, reason='待人工复核')
        gate = {'ok': False, 'evidence_ok': True, 'companies': 0, 'target': 1,
                'errors': ['company shortfall: 0/1; stage report only']}
        with patch('jobmatch_runtime.report.validate', return_value=gate):
            result = render_report(data, self.output, NOW, stage=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['companies'], 0)
        self.assertEqual(result['proposed_jobs'], 1)
        with ZipFile(self.output) as archive:
            text = archive.read('word/document.xml').decode()
            self.assertIn('机器分析草稿／未确认可投', text)
            self.assertIn('企业0独有改写1', text)

    def test_all_mode_fields_rendered(self):
        for mode, fields in MODE_FIELDS.items():
            data = report_fixture(1)
            data['employment_mode'] = data['corpus']['employment_mode'] = mode
            for row in data['corpus']['records']:
                row['employment_mode'] = mode
            data['jobs'][0]['employment_mode'] = mode
            data['jobs'][0]['details']['mode_fields'] = {f: f + '-模拟值' for f in fields}
            data['evidence_basis'] = build(data['corpus'], NOW)['evidence_basis']
            path = self.output.with_name(mode + '.docx')
            result = render_report(data, path, NOW)
            self.assertTrue(result['ok'], result)
            with ZipFile(path) as archive:
                text = archive.read('word/document.xml').decode()
                for field in fields:
                    self.assertIn(field + '-模拟值', text)

    def test_validation_failure_leaves_no_partial_file(self):
        with patch('jobmatch_runtime.report._inspect', side_effect=ValueError('synthetic OOXML failure')):
            result = render_report(report_fixture(1), self.output, NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_malformed_presentation_payload_returns_diagnostics(self):
        for value in (None, {}, 'bad', 7, [None]):
            data = report_fixture(1)
            data['resume_variants'][0]['job_ids'] = value
            result = render_report(data, self.output, NOW)
            self.assertFalse(result['ok'], result)
            self.assertFalse(self.output.exists())

    def test_concurrent_destination_create_is_not_overwritten(self):
        def competing_create(source, destination):
            Path(destination).write_bytes(b'other writer')
            raise FileExistsError('concurrent writer')
        with patch('jobmatch_runtime.report.os.link', side_effect=competing_create):
            result = render_report(report_fixture(1), self.output, NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(self.output.read_bytes(), b'other writer')
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
