"""Synthetic rule tests only; no live vacancies or real candidate data."""
import copy
import unittest
from validate_jobs import validate, timestamp
from test_integrations import corpus_fixture
from build_evidence_basis import build

NOW = timestamp('2026-09-13T12:00:00+08:00')


def fixture():
    job = {
        'id': 'synthetic-1', 'corpus_id': 'job-0', 'company_key': 'company-0',
        'company': '模拟企业', 'title': '模拟岗位0', 'entity_type': 'company',
        'employment_mode': 'campus_full_time',
        'source_tier': 'employer_official',
        'selected': True, 'status': 'open', 'eligibility': 'pass', 'priority': 'A',
        'jd_url': 'https://example0.org/jobs/0', 'apply_url': 'https://example.org/apply/1',
        'checked_at': '2026-09-13T11:00:00+08:00', 'open_evidence': '模拟开放状态',
        'hard_requirements_reviewed': True,
        'requirements': [{'text': '模拟必需项', 'required': True, 'result': 'met',
                          'jd_evidence': '数据分析', 'candidate_evidence': 'E1模拟证据',
                          'evidence_refs': ['E1']}],
        'mappings': [{'requirement': str(i), 'jd_evidence': '数据分析', 'resume_evidence': 'E1',
                      'gap': '无', 'action': '模拟动作', 'evidence_refs': ['E1']} for i in range(3)],
        'rewrites': [{'text': f'模拟改写{i}', 'placement': '项目经历',
                      'evidence_refs': ['E1'], 'use_status': 'ready',
                      'fidelity_reviewed': True} for i in range(2)],
    }
    corpus = corpus_fixture()
    evidence_basis = build(corpus, NOW)['evidence_basis']
    return {'target_companies': 1, 'employment_mode': 'campus_full_time',
            'corpus': corpus, 'evidence_basis': evidence_basis, 'jobs': [job]}


class GateTests(unittest.TestCase):
    def result(self, data):
        return validate(data, NOW)

    def test_valid(self):
        self.assertTrue(self.result(fixture())['ok'])

    def test_report_mode_required(self):
        data = fixture(); del data['employment_mode']
        self.assertFalse(self.result(data)['ok'])

    def test_evidence_basis_required(self):
        data = fixture(); del data['evidence_basis']
        result = self.result(data)
        self.assertFalse(result['ok'])
        self.assertTrue(any('evidence_basis' in error for error in result['errors']))

    def test_market_pattern_not_inferred_from_single_batch(self):
        data = fixture(); data['evidence_basis']['trend_claim_level'] = 'market_pattern'
        result = self.result(data)
        self.assertFalse(result['ok'])
        self.assertTrue(any('not market_pattern' in error for error in result['errors']))

    def test_market_source_counts_must_reconcile(self):
        data = fixture(); data['evidence_basis']['market_sources'][0]['records'] = 9
        result = self.result(data)
        self.assertFalse(result['ok'])
        self.assertTrue(any('must equal market_corpus_count' in error for error in result['errors']))

    def test_summary_cannot_be_separate_from_corpus(self):
        data = fixture()
        data['evidence_basis']['market_sources'] = [
            {'name': '模拟聚合源', 'source_tier': 'aggregator',
             'url': 'https://example.org/jobs', 'records': 10}
        ]
        result = self.result(data)
        self.assertFalse(result['ok'])
        self.assertTrue(any('rebuilt from corpus' in error for error in result['errors']))

    def test_nonexistent_or_unconfirmed_resume_evidence_blocked(self):
        for field in ('rewrites', 'requirements', 'mappings'):
            data = fixture(); data['jobs'][0][field][0]['evidence_refs'] = ['MISSING']
            self.assertFalse(self.result(data)['ok'])
        for state in ('planned', 'unconfirmed'):
            data = fixture(); data['corpus']['candidate_evidence'][0]['state'] = state
            self.assertFalse(self.result(data)['ok'])

    def test_malformed_counts_return_errors(self):
        for value in ('ten', None, [], {}, True):
            data = fixture(); data['evidence_basis']['market_corpus_count'] = value
            self.assertFalse(self.result(data)['ok'])

    def test_report_job_must_reference_real_corpus_source(self):
        for field, value in (('corpus_id', 'missing'), ('jd_url', 'https://wrong.example/job')):
            data = fixture(); data['jobs'][0][field] = value
            self.assertFalse(self.result(data)['ok'])

    def test_cannot_downgrade_required_condition(self):
        data = fixture()
        data['jobs'][0]['requirements'][0]['required'] = False
        data['jobs'][0]['requirements'].append({
            'text': '英语', 'required': True, 'result': 'met',
            'jd_evidence': '英语', 'candidate_evidence': '模拟证据', 'evidence_refs': ['E1']})
        result = self.result(data)
        self.assertTrue(any('omitted or downgraded' in e for e in result['errors']))

    def test_missing_fidelity_review_blocks_ready_rewrite(self):
        data = fixture(); del data['jobs'][0]['rewrites'][0]['fidelity_reviewed']
        self.assertFalse(self.result(data)['ok'])

    def test_mapping_requires_traceable_jd_excerpt(self):
        data = fixture(); del data['jobs'][0]['mappings'][0]['jd_evidence']
        self.assertFalse(self.result(data)['ok'])

    def test_all_three_employment_modes(self):
        for mode in ('internship', 'campus_full_time', 'experienced_full_time'):
            with self.subTest(mode=mode):
                data = fixture(); data['employment_mode'] = mode
                data['corpus']['employment_mode'] = mode
                for row in data['corpus']['records']: row['employment_mode'] = mode
                data['jobs'][0]['employment_mode'] = mode
                self.assertTrue(self.result(data)['ok'])

    def test_taxonomy_requires_version(self):
        data = fixture(); data['evidence_basis']['taxonomies'][0]['version'] = ''
        self.assertFalse(self.result(data)['ok'])

    def test_discovery_source_cannot_enter_main_list(self):
        for tier in ('official_repost', 'aggregator'):
            with self.subTest(tier=tier):
                data = fixture(); data['jobs'][0]['source_tier'] = tier
                self.assertFalse(self.result(data)['ok'])

    def test_job_mode_must_match_report(self):
        data = fixture(); data['jobs'][0]['employment_mode'] = 'internship'
        result = self.result(data)
        self.assertFalse(result['ok'])
        self.assertTrue(any('does not match' in error for error in result['errors']))

    def test_required_unknown_and_unmet_block_main_list(self):
        for state, eligibility in [('unknown', 'unknown'), ('unmet', 'fail')]:
            with self.subTest(state=state):
                data = fixture(); job = data['jobs'][0]
                job['requirements'][0]['result'] = state
                job['eligibility'] = eligibility
                self.assertFalse(self.result(data)['ok'])

    def test_forged_pass_rejected(self):
        data = fixture(); data['jobs'][0]['requirements'][0]['result'] = 'unknown'
        self.assertTrue(any('contradicts' in e for e in self.result(data)['errors']))

    def test_optional_gap_allowed(self):
        data = fixture(); req = copy.deepcopy(data['jobs'][0]['requirements'][0])
        req.update(required=False, result='unmet')
        data['jobs'][0]['requirements'].append(req)
        self.assertTrue(self.result(data)['ok'])

    def test_duplicate_company_not_counted_twice(self):
        data = fixture(); data['target_companies'] = 2
        other = copy.deepcopy(data['jobs'][0]); other['id'] = 'synthetic-2'
        data['jobs'].append(other)
        self.assertEqual(self.result(data)['companies'], 1)
        self.assertFalse(self.result(data)['ok'])

    def test_duplicate_job_rejected(self):
        data = fixture(); data['jobs'].append(copy.deepcopy(data['jobs'][0]))
        self.assertFalse(self.result(data)['ok'])

    def test_institution_not_company(self):
        data = fixture(); data['jobs'][0]['entity_type'] = 'institution'
        self.assertEqual(self.result(data)['companies'], 0)

    def test_closed_expired_stale_future_and_naive_block(self):
        for field, value in [('status', 'closed'),
                             ('closes_at', '2026-09-13T11:00:00+08:00'),
                             ('checked_at', '2026-09-11T12:00:00+08:00'),
                             ('checked_at', '2026-09-14T12:00:00+08:00'),
                             ('checked_at', '2026-09-13T11:00:00')]:
            with self.subTest(field=field, value=value):
                data = fixture(); data['jobs'][0][field] = value
                self.assertFalse(self.result(data)['ok'])

    def test_midnight_boundary_allows_fresh_evidence(self):
        data = fixture(); data['jobs'][0]['checked_at'] = '2026-09-12T23:30:00+08:00'
        self.assertTrue(self.result(data)['ok'])

    def test_missing_evidence_route_and_rewrite_refs(self):
        for field in ('open_evidence', 'apply_url', 'hard_requirements_reviewed'):
            data = fixture(); del data['jobs'][0][field]
            self.assertFalse(self.result(data)['ok'])
        data = fixture(); data['jobs'][0]['rewrites'][0]['evidence_refs'] = []
        self.assertFalse(self.result(data)['ok'])

    def test_pending_appendix_not_counted(self):
        data = fixture(); pending = copy.deepcopy(data['jobs'][0])
        pending.update(id='pending', company_key='other', selected=False,
                       eligibility='unknown', reason='等待资格确认')
        pending['requirements'][0]['result'] = 'unknown'
        data['jobs'].append(pending)
        self.assertTrue(self.result(data)['ok'])
        self.assertEqual(self.result(data)['companies'], 1)

    def test_malformed_input(self):
        for data in ([], {}, {'jobs': [None]}, {'jobs': 'invalid'},
                     {'jobs': [], 'target_companies': False}):
            self.assertFalse(self.result(data)['ok'])


if __name__ == '__main__':
    unittest.main()
