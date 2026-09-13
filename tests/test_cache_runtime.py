"""Offline cache tests with synthetic public/private payloads and a controlled clock."""
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jobmatch_runtime.cache import Cache
from jobmatch_runtime.common import AdapterError
from jobmatch_runtime import sources


class CacheRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cache'
        self.now = 1000.0
        self.cache = Cache(self.path, clock=lambda: self.now)
        self.key = Cache.key('synthetic', '1', {'query': '模拟岗位'})

    def test_key_uses_version_and_canonical_payload_not_mapping_order(self):
        a = Cache.key('component', '1', {'a': 1, 'b': 2})
        self.assertEqual(a, Cache.key('component', '1', {'b': 2, 'a': 1}))
        self.assertNotEqual(a, Cache.key('component', '2', {'a': 1, 'b': 2}))
        self.assertNotEqual(a, Cache.key('other', '1', {'a': 1, 'b': 2}))

    def test_hit_preserves_capture_timestamp_and_does_not_extend_ttl(self):
        value = {'captured_at': '2026-09-13T00:00:00+00:00', 'text': '模拟原文'}
        self.cache.put(self.key, value, ttl=60)
        self.now += 59
        self.assertEqual(self.cache.get(self.key), value)
        self.now += 1
        self.assertIsNone(self.cache.get(self.key))
        self.assertEqual(self.cache.stats(), {'hits': 1, 'misses': 1, 'writes': 1})

    def test_private_payload_not_cached_by_default(self):
        self.cache.put(self.key, {'text': '仅供测试的模拟候选事实'}, private=True)
        self.assertEqual(list(self.path.iterdir()), [])
        self.assertIsNone(self.cache.get(self.key, private=True))
        self.assertEqual(self.cache.writes, 0)

    def test_opt_in_private_cache_has_private_permissions(self):
        cache = Cache(self.path, private_enabled=True, clock=lambda: self.now)
        cache.put(self.key, {'text': '模拟私有数据'}, private=True)
        self.assertEqual(cache.get(self.key, private=True)['text'], '模拟私有数据')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.path / (self.key + '.json')).stat().st_mode), 0o600)

    def test_stored_private_entry_cannot_be_read_after_opt_out(self):
        enabled = Cache(self.path, private_enabled=True, clock=lambda: self.now)
        enabled.put(self.key, {'text': '模拟私有事实'}, private=True)
        disabled = Cache(self.path, private_enabled=False, clock=lambda: self.now)
        self.assertIsNone(disabled.get(self.key))
        self.assertIsNone(disabled.get(self.key, private=True))

    def test_corruption_hash_mismatch_and_future_created_are_misses(self):
        path = self.path / (self.key + '.json')
        for field in ('broken_json', 'value_sha256', 'created_at', 'key'):
            self.cache.put(self.key, {'x': 'synthetic'}, ttl=60)
            item = json.loads(path.read_text())
            if field == 'broken_json':
                path.write_text('{not-json')
            else:
                item[field] = self.now + 1 if field == 'created_at' else 'tampered'
                path.write_text(json.dumps(item))
            self.assertIsNone(self.cache.get(self.key), field)

    def test_symlink_cache_entry_is_not_read_or_pruned(self):
        external = Path(self.tmp.name) / 'retained.txt'
        external.write_text('synthetic original')
        entry = self.path / (self.key + '.json')
        entry.symlink_to(external)
        self.assertIsNone(self.cache.get(self.key))
        self.assertEqual(self.cache.prune()['removed_cache_entries'], 0)
        self.assertEqual(external.read_text(), 'synthetic original')

    def test_prune_removes_only_expired_cache_entries(self):
        second = Cache.key('synthetic', '1', {'query': 'other'})
        self.cache.put(self.key, {'x': 1}, ttl=10)
        self.cache.put(second, {'x': 2}, ttl=100)
        unrelated = self.path / 'notes.json'; unrelated.write_text('{}')
        self.now += 11
        self.assertEqual(self.cache.prune()['removed_cache_entries'], 1)
        self.assertIsNotNone(self.cache.get(second))
        self.assertTrue(unrelated.exists())

    def test_invalid_ttl_and_traversal_keys_rejected(self):
        for ttl in (0, -1, 367 * 86400, '60', None):
            with self.assertRaises(AdapterError):
                self.cache.put(self.key, {}, ttl=ttl)
        for key in ('../outside', 'g' * 64, '1' * 63):
            with self.assertRaises(AdapterError):
                self.cache.get(key)

    def test_source_cache_hit_keeps_real_capture_metadata_without_network(self):
        spec = {'url': 'https://example.org/jobs/synthetic'}
        config = {'allowed_source_hosts': ['example.org'], 'source_terms_accepted': True}
        captured_at = '2026-09-13T00:00:00+00:00'
        html = ('<html><body><p>模拟招聘页面，仅用于离线缓存测试。</p>'
                '<p>模拟工作地点杭州，要求2027届毕业生，有数据分析经历。'
                '</p><p>模拟岗位开放，职责为整理数据及报告，官网未披露具体薪酬与截止日期。'
                '</p><a href="/apply/synthetic">模拟申请入口</a></body></html>')
        def fetch(url, **kwargs):
            if url.endswith('/robots.txt'):
                return b'User-agent: *\nAllow: /', {}
            return html.encode(), {'url': spec['url'], 'content_type': 'text/html',
                                   'captured_at': captured_at, 'status': 200}
        with patch('jobmatch_runtime.sources.http_bytes', side_effect=fetch) as net:
            first = sources.fetch(spec, config, self.cache)
            self.assertEqual(net.call_count, 2)
        self.now += 30
        with patch('jobmatch_runtime.sources.http_bytes', side_effect=AssertionError('Unexpected network')):
            second = sources.fetch(spec, config, self.cache)
        self.assertFalse(first['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['captured_at'], captured_at)
        self.assertEqual(second['text'], first['text'])


if __name__ == '__main__':
    unittest.main()
