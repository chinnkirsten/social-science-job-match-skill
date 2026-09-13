"""Dispatcher contract/privacy tests; all upstream calls are synthetic spies."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime import adapters
from jobmatch_runtime.cache import Cache
from jobmatch_runtime.common import AdapterError


def upstream_fixture():
    return {'data': {'items': ['synthetic taxonomy item']},
            'upstream_version': 'synthetic-fixture-only', 'execution': 'synthetic_spy'}


class DispatchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Cache(Path(self.tmp.name) / 'cache')

    def assert_blocked_without_upstream(self, component, payload, config):
        with patch.object(adapters.adapters_data, 'run') as data_call, \
                patch.object(adapters.adapters_services, 'run') as service_call:
            with self.assertRaises(AdapterError):
                adapters.invoke(component, payload, config, self.cache)
        data_call.assert_not_called()
        service_call.assert_not_called()
        self.assertEqual(self.cache.writes, 0)

    def test_disabled_unknown_and_malformed_requests_never_invoke(self):
        self.assert_blocked_without_upstream('esco', {'data_classification': 'public'}, {})
        self.assert_blocked_without_upstream('not-a-component', {}, {'enabled': True})
        self.assert_blocked_without_upstream('esco', 'not-an-object', {'enabled': True})

    def test_private_or_unclassified_text_cannot_enter_public_data_http(self):
        for component in ('onet_database', 'esco', 'tabiya_open_dataset'):
            for classification in (None, 'private'):
                with self.subTest(component=component, classification=classification):
                    payload = {'query': '模拟候选人的工作经历'}
                    if classification:
                        payload['data_classification'] = classification
                    self.assert_blocked_without_upstream(component, payload, {'enabled': True})

    def test_inline_credentials_are_rejected_before_dispatch_or_cache(self):
        for key in ('api_key', 'token', 'password', 'authorization', 'cookie'):
            for nested in (False, True):
                config = {'enabled': True}
                inline = {key: 'synthetic-value-not-a-real-credential'}
                config.update({'options': inline} if nested else inline)
                with self.subTest(key=key, nested=nested):
                    self.assert_blocked_without_upstream('esco', {'query': 'data analysis',
                        'data_classification': 'public'}, config)

    def test_env_name_reference_is_allowed_without_serializing_secret(self):
        cfg = {'enabled': True, 'api_key_env': 'SYNTHETIC_API_KEY_ENV'}
        payload = {'query': 'data analysis', 'data_classification': 'public'}
        with patch.object(adapters.adapters_data, 'run', return_value=upstream_fixture()) as call:
            result = adapters.invoke('esco', payload, cfg, self.cache)
        call.assert_called_once()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['component_id'], 'esco')

    def test_cache_hit_keeps_original_invocation_time_without_upstream(self):
        payload = {'query': 'data analysis', 'data_classification': 'public'}
        config = {'enabled': True}
        with patch.object(adapters.adapters_data, 'run', return_value=upstream_fixture()) as call:
            first = adapters.invoke('esco', payload, config, self.cache)
            second = adapters.invoke('esco', payload, config, self.cache)
        call.assert_called_once()
        self.assertFalse(first['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['invoked_at'], first['invoked_at'])
        self.assertEqual(second['upstream_version'], 'synthetic-fixture-only')

    def test_mutation_like_service_is_never_replayed_from_cache(self):
        cache = Cache(Path(self.tmp.name) / 'private-cache', private_enabled=True)
        for component in ('tabiya_compass', 'resume_matcher', 'esco_skill_extractor'):
            with patch.object(adapters.adapters_services, 'run', side_effect=lambda *a: upstream_fixture()) as call:
                for _ in range(2):
                    result = adapters.invoke(component, {'text': '模拟经历', 'data_classification': 'private'},
                                             {'enabled': True}, cache)
                    self.assertFalse(result['cache_hit'])
            self.assertEqual(call.call_count, 2)
        self.assertEqual(cache.writes, 0)

    def test_upstream_error_not_replaced_by_success_or_another_component(self):
        with patch.object(adapters.adapters_data, 'run', side_effect=AdapterError('synthetic_error', 'Fixture failure')), \
                patch.object(adapters.adapters_services, 'run') as service_call:
            with self.assertRaises(AdapterError) as raised:
                adapters.invoke('esco', {'query': 'analysis', 'data_classification': 'public'},
                                {'enabled': True}, self.cache)
        self.assertEqual(raised.exception.code, 'synthetic_error')
        service_call.assert_not_called()
        self.assertEqual(self.cache.writes, 0)

    def test_missing_result_contract_never_becomes_success_or_cache_entry(self):
        with patch.object(adapters.adapters_data, 'run', return_value={'unexpected': 'synthetic'}):
            with self.assertRaises(AdapterError) as raised:
                adapters.invoke('esco', {'query': 'analysis', 'data_classification': 'public'},
                                {'enabled': True}, self.cache)
        self.assertEqual(raised.exception.code, 'adapter_contract')
        self.assertEqual(self.cache.writes, 0)


if __name__ == '__main__':
    unittest.main()
