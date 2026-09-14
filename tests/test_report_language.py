"""Reader labels stay clear without rewriting quotations or changing saved data."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime.report import render_report, _display
from evidence_core import digest
from build_evidence_basis import build
from test_report_runtime import report_fixture, NOW


class ReportLanguageTests(unittest.TestCase):
    def export(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-wording.docx'
            result = render_report(data, path, NOW)
            self.assertTrue(result['ok'], result)
            with ZipFile(path) as archive:
                tree = ET.fromstring(archive.read('word/document.xml'))
                return '\n'.join(tree.itertext())

    def test_reader_labels_do_not_expose_schema(self):
        data = report_fixture(1)
        original = copy.deepcopy(data)
        text = self.export(data)
        for label in ('经历记录与材料出处', '相关经历', '材料出处', '在招依据',
                      '要求与经历对照', '可直接使用的事实性改写', '材料与申请动作',
                      '经历记录数量：1', '符合已知必需条件', '已完成；已确认：是'):
            self.assertIn(label, text)
        for label in ('证据账本', '证据定位', '候选人证据：', '开放证据：',
                      'candidate_evidence_count:', 'confirmed=True', '资格：pass'):
            self.assertNotIn(label, text)
        self.assertEqual(data, original)
        self.assertEqual(data['jobs'][0]['eligibility'], 'pass')

    def test_source_quotes_and_professional_words_are_unchanged(self):
        data = report_fixture(1)
        row = data['corpus']['records'][0]
        quote = '模拟招聘原文：财务审计工作包括整理原始证据和审计底稿。'
        row['source_text'] += '\n' + quote
        row['source_sha256'] = digest(row['source_text'])
        data['evidence_basis'] = build(data['corpus'], NOW)['evidence_basis']
        ledger_quote = '模拟简历原文：在财务审计课程中整理原始证据。'
        data['corpus']['candidate_evidence'][0]['text'] = ledger_quote
        data['evidence_basis'] = build(data['corpus'], NOW)['evidence_basis']
        original = copy.deepcopy(data)
        text = self.export(data)
        self.assertIn(quote, text)
        self.assertIn(ledger_quote, text)
        self.assertEqual(data, original)

    def test_statuses_and_counts_remain_distinct(self):
        self.assertEqual(_display(1), '1')
        self.assertEqual(_display(0), '0')
        self.assertEqual(_display(True), '是')
        self.assertEqual(_display(False), '否')
        self.assertEqual(_display('planned'), '计划中')
        self.assertEqual(_display('unconfirmed'), '待确认')
        self.assertNotEqual(_display('pass'), _display('unknown'))
        self.assertEqual(_display('未经改写的财务审计原文'), '未经改写的财务审计原文')


if __name__ == '__main__':
    unittest.main()
