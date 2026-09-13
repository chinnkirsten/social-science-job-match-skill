"""Availability degradation must not suppress privacy or source-safety failures."""
import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime import pipeline
from jobmatch_runtime.pipeline import _enrich
from jobmatch_runtime.common import AdapterError
import test_pipeline_runtime as pipeline_fixtures
from test_pipeline_runtime import FakeModel, runtime_fixture

class OptionalDegradationTests(unittest.TestCase):
    def execute(self, code, policy='continue'):
        with patch('jobmatch_runtime.pipeline.invoke', side_effect=AdapterError(code, 'Sensitive upstream detail')):
            return _enrich([{'component':'esco', 'input':{'query':'public term'}, 'on_failure':policy}],
                           {'source':{'url':'https://example.org/jd'}}, {}, None)

    def test_optional_network_failure_is_disclosed(self):
        results, warnings = self.execute('network_error')
        self.assertEqual(results, [])
        self.assertEqual(warnings[0]['error_code'], 'network_error')
        self.assertNotIn('Sensitive', str(warnings))
        self.assertTrue(warnings[0]['source_id'])

    def test_default_and_mandatory_steps_stop(self):
        for policy in ('stop', None):
            with self.assertRaises(AdapterError): self.execute('network_error', policy)

    def test_sdk_code_casing_is_supported(self):
        self.assertEqual(self.execute('DEPENDENCY_MISSING')[1][0]['error_code'], 'DEPENDENCY_MISSING')

    def test_safety_and_invalid_outputs_always_stop(self):
        for code in ('privacy_consent_required', 'unsafe_url', 'inline_credential',
                     'invalid_response', 'component_disabled', 'unsupported_excerpt', 'http_401',
                     'upstream_schema', 'adapter_contract', 'invalid_config', 'credential_missing',
                     'redirect_scope', 'redirect_rejected', 'terms_required', 'invalid_binding'):
            for variant in (code, code.upper()):
                with self.subTest(code=variant), self.assertRaises(AdapterError): self.execute(variant)

    def test_non_adapter_errors_are_never_suppressed(self):
        with patch('jobmatch_runtime.pipeline.invoke', side_effect=ValueError('malformed output')):
            with self.assertRaises(ValueError):
                _enrich([{'component': 'esco', 'input': {}, 'on_failure': 'continue'}],
                        {'source': {'url': 'https://example.org/jd'}}, {}, None)

    def test_warning_matches_job_and_survives_formal_word(self):
        config, ledger, specs, source = runtime_fixture()
        config['adapter_steps'] = [{'component': 'esco', 'input': {'query': 'public'}, 'on_failure': 'continue'}]
        # A captured redirect target must not silently change the warning's job ID.
        source['url'] = 'https://example.org/redirected-job'
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'run'
            with patch('jobmatch_runtime.pipeline.invoke', side_effect=AdapterError('NETWORK_ERROR', 'Sensitive upstream detail')):
                result = pipeline.run(config, ledger, specs, directory, model=FakeModel(),
                                      loader=lambda *a, **kw: copy.deepcopy(source))
            report_path = directory / result['report_file']
            report = json.loads(report_path.read_text())
            warning = report['execution']['warnings'][0]
            self.assertEqual(warning['source_id'], report['jobs'][0]['id'])
            self.assertEqual(report['execution']['adapters'], [])
            self.assertFalse(report['jobs'][0]['selected'])
            self.assertEqual(result['validation']['companies'], 0)
            review = json.loads(report_path.with_name(report_path.stem + '-review.json').read_text())
            formal = Path(temporary) / 'formal.docx'
            self.assertTrue(pipeline.finalize(report, pipeline_fixtures.PipelineRuntimeTests.approve(review), formal)['ok'])
            for path in (report_path.with_suffix('.docx'), formal):
                with ZipFile(path) as archive:
                    text = ''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
                self.assertIn('补充工具未运行成功：esco', text)
                self.assertIn('岗位编号：' + report['jobs'][0]['id'], text)
                self.assertIn(warning['impact'], text)
                self.assertNotIn('Sensitive upstream detail', text)

    def test_safety_failure_stops_mapping_and_produces_no_word(self):
        config, ledger, specs, source = runtime_fixture()
        config['adapter_steps'] = [{'component': 'esco', 'input': {'query': 'public'}, 'on_failure': 'continue'}]
        for code in ('PRIVACY_CONSENT_REQUIRED', 'UNSAFE_URL', 'UPSTREAM_SCHEMA'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                model = FakeModel()
                directory = Path(temporary) / 'run'
                with patch('jobmatch_runtime.pipeline.invoke', side_effect=AdapterError(code, 'Sensitive upstream detail')):
                    result = pipeline.run(config, ledger, specs, directory, model=model,
                                          loader=lambda *a, **kw: copy.deepcopy(source))
                self.assertEqual([item['role'] for item in model.calls], ['SourceScout'])
                self.assertIn(code, result['failures'])
                self.assertEqual(result['execution']['warnings'], [])
                self.assertFalse(list(directory.glob('*.docx')))

if __name__ == '__main__': unittest.main()
