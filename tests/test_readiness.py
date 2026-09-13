"""Read-only readiness checks; local HTTP fixtures are not upstream verification."""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime.common import AdapterError
from jobmatch_runtime.readiness import inspect_readiness


class Handler(BaseHTTPRequestHandler):
    code = 200
    requests = []

    def do_GET(self):
        self.requests.append(self.path)
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b'{"model_ready": true, "secret": "never-retain-this"}')

    def log_message(self, *args):
        pass


class ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.code = 200
        Handler.requests.clear()
        self.cfg = {'adapters': {'tabiya_livelihoods_classifier': {
            'service_url': f'http://127.0.0.1:{self.server.server_port}',
            'allow_local': True, 'health_path': '/health', 'service_ready': True,
        }}}

    def row(self, **kwargs):
        result = inspect_readiness(self.cfg, **kwargs)
        return next(row for row in result['components'] if row['id'] == 'tabiya_livelihoods_classifier')

    def test_passive_does_not_resolve_dns_or_network(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('passive DNS')), patch(
                'jobmatch_runtime.readiness.http_bytes', side_effect=AssertionError('passive HTTP')):
            row = self.row()
        self.assertTrue(row['configured'])
        self.assertIsNone(row['reachable'])
        self.assertEqual(Handler.requests, [])

    def test_200_does_not_certify_models_or_effectiveness(self):
        row = self.row(live=True)
        self.assertTrue(row['reachable'])
        self.assertIsNone(row['installed'])
        self.assertIsNone(row['live_operation_verified'])
        self.assertIsNone(row['effectiveness_evaluated'])
        self.assertFalse(row['enabled'])
        self.assertEqual(Handler.requests, ['/health'])
        self.assertNotIn('never-retain-this', json.dumps(row))

    def test_error_response_is_reachable_but_not_healthy(self):
        for code in (401, 403, 404, 503):
            with self.subTest(code=code):
                Handler.code = code
                row = self.row(live=True)
                self.assertTrue(row['reachable'])
                self.assertIn('http_' + str(code), row['observations'])
                self.assertIsNone(row['live_operation_verified'])
                self.assertTrue(any('未返回 HTTP 200' in item for item in row['missing']))

    def test_history_never_promotes_current_state(self):
        row = self.row(historical={'verified_at': '2026-01-01', 'components': [
            {'id': 'tabiya_livelihoods_classifier', 'live_status': 'passed'}]})
        self.assertEqual(row['historical_verification']['recorded_at'], '2026-01-01')
        self.assertFalse(row['historical_verification']['applies_to_current_environment'])
        self.assertIsNone(row['live_operation_verified'])

    def test_missing_sdks_have_specific_remediation(self):
        with patch('jobmatch_runtime.readiness.importlib.util.find_spec', return_value=None):
            report = inspect_readiness()
        for row in report['components']:
            if row['id'] in ('docling', 'crawl4ai', 'jobspy'):
                self.assertFalse(row['installed'])
                self.assertTrue(any('SDK' in item for item in row['missing']))
        self.assertEqual(set(report['profiles']), {'standard', 'enhanced', 'evaluation'})
        self.assertTrue(all(not row['enabled'] for row in report['components']))

    def test_health_path_is_explicit_and_bounded(self):
        cfg = self.cfg['adapters']['tabiya_livelihoods_classifier']
        for path in (None, '//evil.example/health', '/health?token=x', '/../admin', '/health\n'):
            with self.subTest(path=path):
                cfg['health_path'] = path
                row = self.row(live=True)
                self.assertIn('health_path_required', row['observations'])
        self.assertEqual(Handler.requests, [])

    def test_loopback_requires_explicit_permission(self):
        self.cfg['adapters']['tabiya_livelihoods_classifier']['allow_local'] = False
        row = self.row(live=True)
        self.assertFalse(row['reachable'])
        self.assertIn('unsafe_url', row['observations'])
        self.assertEqual(Handler.requests, [])

    def test_credential_values_never_returned(self):
        self.cfg['adapters']['tabiya_livelihoods_classifier']['api_key_env'] = 'READINESS_TEST_KEY'
        with patch.dict('os.environ', {'READINESS_TEST_KEY': 'private-test-value'}):
            row = self.row(live=True)
        self.assertNotIn('private-test-value', json.dumps(row))

    def test_inline_secrets_rejected_even_passively(self):
        self.cfg['adapters']['tabiya_livelihoods_classifier']['api_key'] = 'private'
        with self.assertRaises(AdapterError) as exc:
            self.row()
        self.assertEqual(exc.exception.code, 'inline_credential')

    def test_models_ready_declaration_is_not_an_operation_test(self):
        cfg = {'adapters': {'esco_skill_extractor': {'service_url': 'https://example.org',
                'service_ready': True, 'models_ready': True, 'llm_model': 'model', 'embedding_model': 'embed'}}}
        row = next(row for row in inspect_readiness(cfg)['components'] if row['id'] == 'esco_skill_extractor')
        self.assertTrue(row['configured'])
        self.assertIsNone(row['live_operation_verified'])

    def test_bad_configuration_types_rejected(self):
        for config in ([], [1], {'adapters': []}, {'adapters': {'docling': 'invalid'}}):
            with self.subTest(config=config), self.assertRaises(AdapterError):
                inspect_readiness(config)


if __name__ == '__main__':
    unittest.main()
