"""Synthetic tests for the integration registry and corpus evidence builder."""
import copy
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile

from build_evidence_basis import build
from validate_integrations import validate
from evidence_core import digest
from datetime import datetime

NOW = datetime.fromisoformat('2026-09-13T12:00:00+08:00')


ROOT = Path(__file__).resolve().parents[1]


def corpus_fixture(count=10, employers=5, tier='employer_official'):
    records = []
    for index in range(count):
        records.append({
            'corpus_id': f'job-{index}',
            'company_key': f'company-{index % employers}',
            'company': f'模拟企业{index % employers}',
            'title': f'模拟岗位{index}',
            'employment_mode': 'campus_full_time',
            'geography': '杭州/广州/深圳',
            'locations': ['杭州'],
            'source_tier': tier,
            'jd_url': f'https://example{index % employers}.org/jobs/{index}',
            'captured_at': '2026-09-13T10:00:00+08:00',
            'status': 'open',
            'full_jd': True,
            'comparable': True,
            'responsibilities': ['模拟职责'],
            'requirements': [
                {'text': '数据分析', 'required': True,
                 'source_excerpt': '数据分析',
                 'normalized_skills': ['data analysis'], 'taxonomy_refs': ['ESCO:test']},
                {'text': '英语', 'required': False,
                 'source_excerpt': '英语',
                 'normalized_skills': ['English'], 'taxonomy_refs': []},
            ],
            'excluded_reason': '',
            'source_text': f'模拟岗位{index}。数据分析。英语优先。职责：模拟职责。',
        })
        records[-1]['source_sha256'] = digest(records[-1]['source_text'])
    return {
        'method_version': '3.0',
        'employment_mode': 'campus_full_time',
        'geography': '杭州/广州/深圳',
        'locations': ['杭州', '广州', '深圳'],
        'collected_at': '2026-09-13T12:00:00+08:00',
        'candidate_evidence': [{'id': 'E1', 'source_id': 'resume', 'locator': 'page 1, project 1',
                                'text': '模拟数据分析经历', 'state': 'completed', 'confirmed': True}],
        'candidate_pool_count': count + 5,
        'taxonomies': [{'name': 'ESCO', 'version': '1.2.1', 'use': '技能归一化',
                        'url': 'https://esco.ec.europa.eu/en/use-esco'}],
        'records': records,
    }


class IntegrationRegistryTests(unittest.TestCase):
    def test_checked_in_registry_is_valid(self):
        with open(ROOT / 'integrations' / 'open_source_stack.json', encoding='utf-8') as handle:
            result = validate(json.load(handle))
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['components'], 12)

    def test_duplicate_registry_id_fails(self):
        data = {'schema_version': '1.0', 'verified_at': '2026-09-13',
                'components': []}
        with open(ROOT / 'integrations' / 'open_source_stack.json', encoding='utf-8') as handle:
            component = json.load(handle)['components'][0]
        data['components'] = [component, copy.deepcopy(component)]
        self.assertFalse(validate(data)['ok'])

    def test_unpinned_github_component_fails(self):
        with open(ROOT / 'integrations' / 'open_source_stack.json', encoding='utf-8') as handle:
            data = json.load(handle)
        data['components'][2]['pin'] = 'main'
        self.assertFalse(validate(data)['ok'])


class EvidenceBuilderTests(unittest.TestCase):
    def test_large_batch_remains_sample_observation(self):
        result = build(corpus_fixture(), NOW)
        self.assertTrue(result['ok'], result.get('errors'))
        self.assertEqual(result['evidence_basis']['trend_claim_level'], 'sample_observation')
        self.assertEqual(result['evidence_basis']['market_corpus_count'], 10)
        self.assertEqual(result['corpus_summary']['different_employer_count'], 5)
        self.assertEqual(result['corpus_summary']['top_required_skills'][0], ('data analysis', 10))

    def test_small_corpus_is_only_sample_observation(self):
        result = build(corpus_fixture(count=9, employers=5), NOW)
        self.assertEqual(result['evidence_basis']['trend_claim_level'], 'sample_observation')

    def test_weak_sources_cannot_claim_market_pattern(self):
        result = build(corpus_fixture(tier='aggregator'), NOW)
        self.assertEqual(result['evidence_basis']['trend_claim_level'], 'sample_observation')
        self.assertEqual(result['corpus_summary']['reliable_source_ratio'], 0.0)

    def test_duplicate_corpus_id_fails(self):
        data = corpus_fixture()
        data['records'][1]['corpus_id'] = data['records'][0]['corpus_id']
        self.assertFalse(build(data, NOW)['ok'])

    def test_mixed_employment_modes_fail(self):
        data = corpus_fixture()
        data['records'][0]['employment_mode'] = 'internship'
        self.assertFalse(build(data, NOW)['ok'])

    def test_corpus_regressions(self):
        mutations = {
            'city': lambda r: r.update(locations=['London']),
            'old': lambda r: r.update(captured_at='2020-01-01T10:00:00+08:00'),
            'future': lambda r: r.update(captured_at='2026-09-14T10:00:00+08:00'),
            'empty': lambda r: r.update(requirements=[], responsibilities=[]),
            'hash': lambda r: r.update(source_sha256='0' * 64),
            'closed': lambda r: r.update(status='closed'),
            'requirement_type': lambda r: r['requirements'][0].update(required='yes'),
            'excerpt': lambda r: r['requirements'][0].update(source_excerpt='not in source'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                data = corpus_fixture(); mutate(data['records'][0])
                self.assertFalse(build(data, NOW)['ok'])

    def test_duplicate_tracking_url_and_content(self):
        for duplicate in ('url', 'content'):
            data = corpus_fixture(); a, b = data['records'][:2]
            if duplicate == 'url': b['jd_url'] = a['jd_url'] + '?utm_source=mirror'
            else:
                b['source_text'] = a['source_text']; b['source_sha256'] = a['source_sha256']
            self.assertFalse(build(data, NOW)['ok'])

    def test_distinct_spa_jobs_not_merged(self):
        data = corpus_fixture()
        for i, row in enumerate(data['records']):
            row['jd_url'] = f'https://example.org/#/job/{i}'
        self.assertTrue(build(data, NOW)['ok'])

    def test_cli_refuses_overwrite_and_keeps_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'corpus.json'
            original = json.dumps(corpus_fixture(), ensure_ascii=False)
            source.write_text(original, encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_evidence_basis.py'),
                                     str(source), '--output', str(source), '--as-of', NOW.isoformat()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(source.read_text(encoding='utf-8'), original)

    def test_cli_end_to_end_corpus_and_report_gate(self):
        from test_validate_jobs import fixture
        with tempfile.TemporaryDirectory() as folder:
            source, output, report = [Path(folder) / name for name in ('corpus.json','evidence.json','report.json')]
            data = fixture()
            source.write_text(json.dumps(data['corpus']), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_evidence_basis.py'),
                                     str(source), '--output', str(output), '--as-of', NOW.isoformat()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data['evidence_basis'] = json.loads(output.read_text())['evidence_basis']
            report.write_text(json.dumps(data), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/validate_jobs.py'),
                                     str(report), '--as-of', NOW.isoformat()], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
