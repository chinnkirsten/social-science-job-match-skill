"""Offline synthetic orchestration tests; FakeModel is NOT a live LLM evaluation."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime import pipeline
from jobmatch_runtime.common import AdapterError, fingerprint
from jobmatch_runtime.llm import ModelClient


def runtime_fixture(target=1):
    config = {'employment_mode': 'campus_full_time', 'locations': ['杭州'],
              'target_directions': ['财务分析'],
              'target_companies': target, 'limits': {'max_parallel_calls': 1},
              'llm': {'provider': 'synthetic_test_only', 'model': 'FakeModel'}}
    ledger = [{'id': 'E1', 'source_id': 'synthetic-resume', 'locator': '模拟第1页',
               'text': '模拟：2027 届毕业，已完成财务分析课程与数据分析项目。',
               'state': 'completed', 'confirmed': True}]
    specs = [{'url': 'https://example.org/jobs/synthetic-1', 'target_direction': '财务分析',
              'source_tier': 'employer_official', 'company_key': 'synthetic-company'}]
    captured = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    source = {'url': specs[0]['url'], 'links': [specs[0]['url']],
              'text': '模拟企业招聘公告，仅供离线测试。杭州财务分析校招岗位。'
                      '模拟岗位正在接受申请。要求2027届毕业生。要求有数据分析经验。'
                      '职责：整理模拟财务报表并分析数据。薪酬与截止日未披露。',
              'captured_at': captured, 'cache_hit': False, 'execution': 'synthetic_fixture'}
    return config, ledger, specs, source


class FakeModel:
    """Three independent role responses, deterministic test double, never a real model."""
    def __init__(self, mutation=None):
        self.calls = []
        self.mutation = mutation

    def call(self, role, prompt, payload, private=False):
        self.calls.append({'role': role, 'private': private})
        if role == 'SourceScout':
            value = {'company': '模拟企业', 'title': '模拟财务分析岗位',
                     'employment_mode': 'campus_full_time', 'locations': ['杭州'],
                     'full_jd': True, 'status': 'open', 'open_evidence': '模拟岗位正在接受申请。',
                     'responsibilities': ['整理模拟财务报表并分析数据'],
                     'requirements': [{'text': '2027届毕业生', 'required': True,
                                       'source_excerpt': '要求2027届毕业生。'},
                                      {'text': '数据分析经验', 'required': True,
                                       'source_excerpt': '要求有数据分析经验。'}],
                     'apply_url': payload['source']['url'],
                     'details': {'salary': '官网未披露（模拟）', 'deadline': '官网未披露（模拟）',
                                 'mode_fields': {'graduation_cohort': '2027届（模拟）',
                                                 'recruitment_batch': '秋招（模拟）',
                                                 'graduate_eligibility': '模拟资格需读源确认'}}}
        elif role == 'EvidenceMapper':
            value = {'requirements': [{'result': 'met', 'candidate_evidence': 'E1模拟事实',
                                       'source_excerpt': requirement['source_excerpt'],
                                       'evidence_refs': ['E1']} for requirement in payload['jd']['requirements']],
                     'priority': 'A', 'mappings': [{'requirement': f'模拟条件{i}',
                        'resume_evidence': 'E1模拟经历', 'evidence_refs': ['E1'], 'gap': '待复核',
                        'action': '核对模拟原文'} for i in range(3)],
                     'rewrites': [{'text': f'模拟项目改写{i}：完成数据分析项目。',
                        'placement': '项目经历', 'evidence_refs': ['E1'],
                        'use_status': 'verify_first'} for i in range(2)],
                     'resume_variant': {'name': '模拟财务版', 'changes': ['突出已完成的模拟数据分析项目']},
                     'action': {'action': '读源核对模拟申请材料', 'materials': ['模拟成绩单']},
                     'reason': '模拟分析，不代表真实就业机会'}
        elif role == 'Auditor':
            value = {'approved': True, 'issues': []}
        else:
            raise AssertionError('Unexpected role ' + role)
        if self.mutation:
            self.mutation(role, value, payload)
        return copy.deepcopy(value)


class PipelineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'synthetic-run'
        self.config, self.ledger, self.specs, self.source = runtime_fixture()

    def execute(self, model=None):
        self.model = model or FakeModel()
        return pipeline.run(self.config, self.ledger, self.specs, self.directory,
                            model=self.model, loader=lambda *a, **kw: copy.deepcopy(self.source))

    def read_run(self, result):
        path = self.directory / result['report_file']
        return (json.loads(path.read_text()),
                json.loads(path.with_name(path.stem + '-review.json').read_text()))

    def test_explainable_rank_is_not_input_order_or_probability(self):
        jobs = [
            {'id': 'b', 'proposed_selection': True, 'priority': 'B', 'source_tier': 'employer_official',
             'mappings': [{'evidence_refs': ['E1']}] * 5, 'requirements': []},
            {'id': 'a2', 'proposed_selection': True, 'priority': 'A', 'source_tier': 'official_ats',
             'mappings': [{'evidence_refs': ['E1']}] * 2,
             'requirements': [{'required': False, 'result': 'unknown'}]},
            {'id': 'a1', 'proposed_selection': True, 'priority': 'A', 'source_tier': 'employer_official',
             'mappings': [{'evidence_refs': ['E1']}] * 3, 'requirements': []},
        ]
        pipeline._assign_ranks(jobs)
        ordered = sorted(jobs, key=lambda job: job['rank_position'])
        self.assertEqual([job['id'] for job in ordered], ['a1', 'a2', 'b'])
        self.assertTrue(all('probability' not in job['rank_basis'] for job in jobs))

    @staticmethod
    def approve(review):
        for item in review['approvals']:
            for key in ('source_identity_verified', 'open_status_verified', 'full_jd_reviewed',
                        'qualification_reviewed', 'rewrite_fidelity_reviewed'):
                item[key] = True
            item['ready_rewrite_indices'] = [0, 1]
        return review

    def test_three_roles_draft_explicit_review_formal(self):
        result = self.execute()
        self.assertEqual(result['status'], 'awaiting_review', result)
        self.assertEqual([c['role'] for c in self.model.calls], ['SourceScout', 'EvidenceMapper', 'Auditor'])
        self.assertEqual([c['private'] for c in self.model.calls], [False, True, True])
        report, review = self.read_run(result)
        self.assertFalse(report['jobs'][0]['selected'])
        self.assertTrue(report['jobs'][0]['proposed_selection'])
        self.assertEqual(report['jobs'][0]['rank_position'], 1)
        self.assertEqual(report['jobs'][0]['target_direction'], '财务分析')
        self.assertIn('direct_resume_mappings', report['jobs'][0]['rank_basis'])
        self.assertEqual(result['validation']['companies'], 0)
        draft = self.directory / result['report_file'].replace('.json', '.docx')
        self.assertTrue(draft.exists())
        with ZipFile(draft) as archive:
            self.assertIn('机器分析草稿', archive.read('word/document.xml').decode())
        formal = self.root / 'synthetic-formal.docx'
        unreviewed = pipeline.finalize(report, review, formal)
        self.assertEqual(unreviewed['status'], 'blocked')
        self.assertFalse(formal.exists())
        result = pipeline.finalize(report, self.approve(review), formal)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['companies'], 1)
        self.assertTrue(formal.exists())
        self.assertEqual(result['visual_review'], 'pending')

    def test_resume_uses_checkpoint_without_model_or_loader_calls(self):
        first = self.execute()
        next_model = FakeModel()
        def forbidden(*args, **kwargs):
            self.fail('Resuming a fresh checkpoint must not fetch again')
        second = pipeline.run(self.config, self.ledger, self.specs, self.directory,
                              model=next_model, loader=forbidden)
        self.assertEqual(next_model.calls, [])
        self.assertEqual(second['execution']['resumed_jobs'], 1)
        self.assertNotEqual(first['report_file'], second['report_file'])
        self.assertTrue((self.directory / first['report_file']).exists())

    def test_changed_config_or_ledger_cannot_resume(self):
        self.execute()
        for change in ('config', 'ledger'):
            config, ledger = copy.deepcopy(self.config), copy.deepcopy(self.ledger)
            if change == 'config':
                config['target_companies'] = 20
            else:
                ledger[0]['text'] += ' 后补模拟事实'
            with self.assertRaises(AdapterError) as raised:
                pipeline.run(config, ledger, self.specs, self.directory, model=FakeModel())
            self.assertEqual(raised.exception.code, 'resume_mismatch')

    def test_capture_timestamp_cannot_be_rewritten_by_model_or_checkpoint(self):
        def forge(role, value, payload):
            value.update(captured_at='2099-01-01T00:00:00+00:00',
                         checked_at='2099-01-01T00:00:00+00:00', source_tier='aggregator')
        first = self.execute(FakeModel(forge))
        second = self.execute(FakeModel())
        for result in (first, second):
            report, _ = self.read_run(result)
            self.assertEqual(report['corpus']['records'][0]['captured_at'], self.source['captured_at'])
            self.assertEqual(report['jobs'][0]['checked_at'], self.source['captured_at'])
            self.assertEqual(report['jobs'][0]['source_tier'], 'employer_official')

    def test_model_cannot_upgrade_unconfirmed_ledger(self):
        self.ledger[0].update(confirmed=False, state='unconfirmed')
        original = copy.deepcopy(self.ledger)
        def forge(role, value, payload):
            if role == 'EvidenceMapper':
                value['candidate_evidence'] = [{**self.ledger[0], 'confirmed': True, 'state': 'completed'}]
        result = self.execute(FakeModel(forge))
        self.assertIn('unsupported_candidate_claim', result['failures'])
        self.assertEqual(self.ledger, original)
        report, _ = self.read_run(result)
        self.assertEqual(report['corpus']['candidate_evidence'], original)
        self.assertEqual(report['jobs'], [])
        self.assertFalse(list(self.directory.glob('*.docx')))

    def test_omitted_mapper_condition_is_blocked(self):
        def omit(role, value, payload):
            if role == 'EvidenceMapper':
                value['requirements'].pop()
        result = self.execute(FakeModel(omit))
        self.assertIn('condition_omission', result['failures'])
        self.assertFalse(list(self.directory.glob('*.docx')))

    def test_mapper_cannot_downgrade_original_required_flags(self):
        def downgrade(role, value, payload):
            if role == 'EvidenceMapper':
                for req in value['requirements']:
                    req['required'] = False
        result = self.execute(FakeModel(downgrade))
        report, _ = self.read_run(result)
        self.assertTrue(all(r['required'] for r in report['jobs'][0]['requirements']))

    def test_unknown_requirement_or_auditor_rejection_cannot_be_selected(self):
        for mode in ('unknown', 'audit_reject'):
            with self.subTest(mode=mode):
                self.directory = self.root / mode
                def reject(role, value, payload):
                    if mode == 'unknown' and role == 'EvidenceMapper':
                        value['requirements'][0].update(result='unknown', evidence_refs=[])
                    if mode == 'audit_reject' and role == 'Auditor':
                        value.update(approved=False, issues=['模拟：漏读硬条件'])
                result = self.execute(FakeModel(reject))
                report, review = self.read_run(result)
                self.assertFalse(report['jobs'][0]['selected'])
                self.assertFalse(report['jobs'][0]['proposed_selection'])
                self.assertEqual(review['approvals'], [])
                formal = self.root / (mode + '.docx')
                self.assertEqual(pipeline.finalize(report, review, formal)['status'], 'blocked')
                self.assertFalse(formal.exists())

    def test_unknown_or_closed_vacancy_never_gets_mapper_or_approval(self):
        for status in ('unknown', 'closed'):
            self.directory = self.root / status
            def close(role, value, payload):
                if role == 'SourceScout':
                    value.update(status=status, open_evidence='')
            result = self.execute(FakeModel(close))
            report, review = self.read_run(result)
            self.assertEqual([c['role'] for c in self.model.calls], ['SourceScout'])
            self.assertEqual(report['jobs'], [])
            self.assertEqual(review['approvals'], [])
            self.assertFalse(list(self.directory.glob('*.docx')))

    def test_partial_twenty_only_allows_explicit_stage(self):
        self.config['target_companies'] = 20
        result = self.execute()
        report, review = self.read_run(result)
        self.approve(review)
        formal = self.root / 'partial-formal.docx'
        blocked = pipeline.finalize(report, review, formal)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(blocked['validation']['companies'], 1)
        self.assertFalse(formal.exists())
        stage = self.root / 'partial-stage.docx'
        self.assertTrue(pipeline.finalize(report, review, stage, stage=True)['ok'])

    def test_review_hash_and_ready_rewrite_evidence_are_enforced(self):
        result = self.execute()
        report, review = self.read_run(result)
        self.approve(review)
        output = self.root / 'must-not-exist.docx'
        bad = copy.deepcopy(review); bad['report_sha256'] = '0' * 64
        with self.assertRaises(AdapterError) as raised:
            pipeline.finalize(report, bad, output)
        self.assertEqual(raised.exception.code, 'stale_review')
        report['jobs'][0]['rewrites'][0]['use_status'] = 'create_first'
        review['report_sha256'] = fingerprint(report)
        with self.assertRaises(AdapterError) as raised:
            pipeline.finalize(report, review, output)
        self.assertEqual(raised.exception.code, 'new_evidence_required')
        self.assertFalse(output.exists())

    def test_unanchored_open_evidence_cannot_generate_word(self):
        def forge(role, value, payload):
            if role == 'SourceScout':
                value['open_evidence'] = '虚构开放状态，不在原文中'
        result = self.execute(FakeModel(forge))
        self.assertIn('unsupported_openness', result['failures'])
        self.assertFalse(list(self.directory.glob('*.docx')))

    def test_in_process_model_hook_cannot_mutate_frozen_candidate_ledger(self):
        original = copy.deepcopy(self.ledger)
        def mutate(role, value, payload):
            if role == 'EvidenceMapper':
                payload['candidate_evidence'][0]['text'] = '模型注入的虚构管理经验'
        try:
            result = self.execute(FakeModel(mutate))
        except AdapterError as exc:
            self.assertEqual(exc.code, 'ledger_mutation')
            self.assertFalse(list(self.directory.glob('report-*.json')))
            self.assertFalse(list(self.directory.glob('*.docx')))
        else:
            report, _ = self.read_run(result)
            self.assertEqual(report['corpus']['candidate_evidence'], original)
        self.assertEqual(self.ledger, original)

    def test_expired_capture_checkpoint_is_not_reused_even_when_completed_recently(self):
        self.execute()
        checkpoint = self.directory / 'checkpoint.json'
        state = json.loads(checkpoint.read_text())
        for stored in state['jobs'].values():
            stored['result']['record']['captured_at'] = (
                datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        checkpoint.write_text(json.dumps(state))
        result = self.execute(FakeModel())
        self.assertEqual(len(self.model.calls), 3)
        self.assertEqual(result['execution']['resumed_jobs'], 0)

    def test_malformed_model_payload_is_diagnostic_not_formal_report(self):
        for field in ('requirements', 'rewrites', 'audit'):
            self.directory = self.root / ('malformed-' + field)
            def malformed(role, value, payload):
                if role == 'EvidenceMapper' and field == 'requirements':
                    value['requirements'] = [None, None]
                if role == 'EvidenceMapper' and field == 'rewrites':
                    value['rewrites'] = [None, None]
                if role == 'Auditor' and field == 'audit':
                    value['issues'] = 'not-an-array'
            result = self.execute(FakeModel(malformed))
            self.assertTrue(result['failures'], field)
            self.assertFalse(list(self.directory.glob('*.docx')), field)

    def test_cloud_cli_cannot_use_unused_loopback_setting_to_bypass_privacy(self):
        client = ModelClient({'provider': 'codex_cli', 'base_url': 'http://127.0.0.1:1234',
                              'service_egress': 'local_only'}, allow_remote_candidate_data=False)
        with patch.object(client, '_codex', return_value='{}') as invoke:
            with self.assertRaises(AdapterError) as raised:
                client.call('EvidenceMapper', 'synthetic instruction', {'text': '模拟事实'}, private=True)
        self.assertEqual(raised.exception.code, 'privacy_consent_required')
        invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
