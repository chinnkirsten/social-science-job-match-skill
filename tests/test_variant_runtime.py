"""Synthetic resume-structure grouping tests; no real model or candidate data."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime import pipeline
from jobmatch_runtime.common import AdapterError
from jobmatch_runtime.report import render_report
import test_pipeline_runtime as pipeline_fixtures
from test_pipeline_runtime import FakeModel, runtime_fixture
from test_report_runtime import report_fixture, NOW


def variant_fixture(names=None):
    names = names or ['模拟结构' + str(i) for i in range(4)]
    return {
        'resume_variants': [{'id': 'old-' + str(i), 'name': name, 'job_ids': ['job-' + str(i)],
                             'changes': ['模拟改动' + str(i), '共有模拟改动']}
                            for i, name in enumerate(names)],
        'action_plan': [{'job_id': 'job-' + str(i), 'resume_variant_id': 'old-' + str(i),
                         'action': '模拟行动' + str(i)} for i in range(len(names))],
        'jobs': [{'id': 'job-' + str(i), 'rewrites': [{'text': '原始模拟改写' + str(i)}]}
                 for i in range(len(names))],
    }


class GroupModel:
    def __init__(self, groups):
        self.groups, self.calls = groups, []

    def call(self, role, prompt, payload, private=False):
        self.calls.append({'role': role, 'private': private, 'payload': copy.deepcopy(payload)})
        return {'groups': copy.deepcopy(self.groups)}


class FourJobModel(FakeModel):
    def call(self, role, prompt, payload, private=False):
        if 'variants' in payload:
            self.calls.append({'role': role, 'private': private, 'grouping': True})
            ids = [job for variant in payload['variants'] for job in variant['job_ids']]
            return {'groups': [{'name': '模拟财务结构', 'job_ids': ids[:2]},
                               {'name': '模拟分析结构', 'job_ids': ids[2:]}]}
        result = super().call(role, prompt, payload, private=private)
        index = payload['source']['url'].rsplit('/', 1)[-1]
        if role == 'SourceScout':
            result['company'] = '模拟企业' + index
            result['title'] = '模拟财务岗位' + index
        elif role == 'EvidenceMapper':
            result['resume_variant']['name'] = '模拟原结构' + index
            result['resume_variant']['changes'] = ['模拟改动' + index]
        return result


class VariantRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_same_name_merges_without_llm_and_preserves_changes_and_actions(self):
        data = variant_fixture(['模拟财务', '模拟财务', '模拟分析', '模拟分析'])
        original_jobs = copy.deepcopy(data['jobs'])
        model = GroupModel(None)
        pipeline._consolidate_variants(data, model)
        self.assertEqual(model.calls, [])
        self.assertEqual(len(data['resume_variants']), 2)
        self.assertEqual(data['resume_variants'][0]['job_ids'], ['job-0', 'job-1'])
        self.assertEqual(data['resume_variants'][0]['changes'], ['模拟改动0', '共有模拟改动', '模拟改动1'])
        self.assertEqual(data['resume_variants'][1]['changes'], ['模拟改动2', '共有模拟改动', '模拟改动3'])
        self.assertEqual(data['jobs'], original_jobs)
        assignments = {j: v['id'] for v in data['resume_variants'] for j in v['job_ids']}
        for action in data['action_plan']:
            self.assertEqual(action['resume_variant_id'], assignments[action['job_id']])

    def test_more_than_three_names_calls_only_mapper_and_exactly_covers_jobs(self):
        data = variant_fixture()
        before = copy.deepcopy(data)
        groups = [{'name': '模拟结构甲', 'job_ids': ['job-0', 'job-2']},
                  {'name': '模拟结构乙', 'job_ids': ['job-1', 'job-3'],
                   'changes': ['模型不应替换的新增改动']}]
        model = GroupModel(groups)
        pipeline._consolidate_variants(data, model)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0]['role'], 'EvidenceMapper')
        self.assertTrue(model.calls[0]['private'])
        self.assertEqual(sorted(j for v in data['resume_variants'] for j in v['job_ids']),
                         ['job-0', 'job-1', 'job-2', 'job-3'])
        self.assertEqual(data['resume_variants'][0]['changes'], ['模拟改动0', '共有模拟改动', '模拟改动2'])
        self.assertEqual(data['resume_variants'][1]['changes'], ['模拟改动1', '共有模拟改动', '模拟改动3'])
        self.assertEqual(data['jobs'], before['jobs'])
        assignments = {j: v['id'] for v in data['resume_variants'] for j in v['job_ids']}
        self.assertTrue(all(a['resume_variant_id'] == assignments[a['job_id']] for a in data['action_plan']))

    def test_missing_duplicate_unknown_ids_and_excess_groups_rejected(self):
        malformed = [
            [{'name': '模拟缺失', 'job_ids': ['job-0', 'job-1', 'job-2']}],
            [{'name': '模拟重复', 'job_ids': ['job-0', 'job-1', 'job-2', 'job-3', 'job-0']}],
            [{'name': '模拟未知', 'job_ids': ['job-0', 'job-1', 'job-2', 'missing']}],
            [{'name': str(i), 'job_ids': ['job-' + str(i)]} for i in range(4)],
            [{'name': ' ', 'job_ids': ['job-0', 'job-1', 'job-2', 'job-3']}],
            [{'name': '模拟', 'job_ids': None}],
            None,
        ]
        for groups in malformed:
            with self.subTest(groups=groups):
                data = variant_fixture()
                before = copy.deepcopy(data)
                with self.assertRaises(AdapterError) as raised:
                    pipeline._consolidate_variants(data, GroupModel(groups))
                self.assertEqual(raised.exception.code, 'invalid_variant_groups')
                self.assertEqual(data, before)

    def _four_job_run(self, directory, model):
        config, ledger, _, source = runtime_fixture(target=1)
        specs = [{'url': f'https://example.org/jobs/{i}', 'source_tier': 'employer_official',
                  'company_key': 'synthetic-company-' + str(i)} for i in range(4)]
        def loader(spec, *args, **kwargs):
            return {**source, 'url': spec['url'], 'links': [spec['url']],
                    'text': source['text'] + ' 模拟唯一岗位标记 ' + spec['url'].rsplit('/', 1)[-1]}
        return pipeline.run(config, ledger, specs, directory, model=model, loader=loader)

    def test_resume_retains_job_checkpoints_only_regroups_once(self):
        directory = self.root / 'synthetic-four-job-run'
        first_model = FourJobModel()
        first = self._four_job_run(directory, first_model)
        self.assertEqual(first['status'], 'awaiting_review', first)
        self.assertEqual(len(first_model.calls), 13)
        self.assertEqual(sum(c.get('grouping', False) for c in first_model.calls), 1)
        second_model = FourJobModel()
        second = self._four_job_run(directory, second_model)
        self.assertEqual(second['execution']['resumed_jobs'], 4)
        self.assertEqual(second['execution']['fresh_jobs'], 0)
        self.assertEqual(second_model.calls, [{'role': 'EvidenceMapper', 'private': True, 'grouping': True}])
        report = json.loads((directory / second['report_file']).read_text())
        self.assertEqual(len(report['jobs']), 4)
        self.assertEqual(len(report['resume_variants']), 2)

    def test_finalization_keeps_approved_intersection_of_shared_group(self):
        directory = self.root / 'synthetic-finalize-run'
        result = self._four_job_run(directory, FourJobModel())
        path = directory / result['report_file']
        report = json.loads(path.read_text())
        review = json.loads(path.with_name(path.stem + '-review.json').read_text())
        review['approvals'] = review['approvals'][:1]
        pipeline_fixtures.PipelineRuntimeTests.approve(review)
        expected_id = review['approvals'][0]['job_id']
        preserved_changes = report['resume_variants'][0]['changes']
        with patch('jobmatch_runtime.report.render_report', wraps=render_report) as render:
            rendered = pipeline.finalize(report, review, self.root / 'synthetic-approved.docx')
        self.assertTrue(rendered['ok'], rendered)
        final = render.call_args.args[0]
        self.assertEqual(len(final['resume_variants']), 1)
        self.assertEqual(final['resume_variants'][0]['job_ids'], [expected_id])
        self.assertEqual(final['resume_variants'][0]['changes'], preserved_changes)
        self.assertEqual([a['job_id'] for a in final['action_plan']], [expected_id])
        self.assertEqual(final['action_plan'][0]['resume_variant_id'], final['resume_variants'][0]['id'])
        self.assertEqual(len(report['resume_variants']), 2)

    def test_four_groups_cannot_be_exported_as_formal_word(self):
        data = report_fixture(4)
        data['resume_variants'] = [{'id': f'track-{i}', 'name': f'模拟结构{i}',
                                   'job_ids': [job['id']], 'changes': ['模拟改动']}
                                  for i, job in enumerate(data['jobs'])]
        for index, action in enumerate(data['action_plan']):
            action['resume_variant_id'] = f'track-{index}'
        path = self.root / 'forbidden-four-groups.docx'
        result = render_report(data, path, NOW)
        self.assertFalse(result['ok'])
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
