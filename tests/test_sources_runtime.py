"""Public-source contract tests. All HTTP is mocked; no live-site claims."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jobmatch_runtime import sources
from jobmatch_runtime.cache import Cache
from jobmatch_runtime.common import AdapterError


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.cache = Cache(Path(self.temp.name) / "cache", clock=lambda: self.now)
        self.url = "https://careers.example.test/jobs/accountant"
        self.spec = {"url": self.url}
        self.config = {"allowed_source_hosts": ["careers.example.test"],
                       "source_terms_accepted": True, "source_cache_seconds": 60}
        self.capture = "2026-09-13T00:00:00+00:00"
        self.body = ("A synthetic accounting job description with detailed duties and requirements. " * 3).encode()
        self.robots = b"User-agent: *\nAllow: /"
        self.final_url = self.url
        self.content_type = "text/plain; charset=utf-8"
        self.robots_error = None
        patcher = patch.object(sources, "http_bytes", side_effect=self.respond)
        self.http = patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, url, **kwargs):
        if url.endswith("/robots.txt"):
            if self.robots_error:
                raise self.robots_error
            return self.robots, {"url": url, "content_type": "text/plain", "status": 200, "captured_at": self.capture}
        self.assertEqual(url, self.url)
        return self.body, {"url": self.final_url, "content_type": self.content_type,
                           "status": 200, "captured_at": self.capture}

    def assert_error(self, code, config=None, spec=None, force=False):
        with self.assertRaises(AdapterError) as caught:
            sources.fetch(spec or self.spec, config or self.config, self.cache, force=force)
        self.assertEqual(caught.exception.code, code)

    def test_public_text_fetch_checks_robots_first(self):
        result = sources.fetch(self.spec, self.config, self.cache)
        self.assertEqual(result["text"], self.body.decode())
        self.assertEqual(result["captured_at"], self.capture)
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["execution"], "real_http_fetch")
        self.assertEqual([call.args[0] for call in self.http.call_args_list],
                         ["https://careers.example.test/robots.txt", self.url])
        self.assertEqual(self.http.call_args_list[-1].kwargs["max_bytes"], 2_000_000)

    def test_html_removes_executable_noise_and_extracts_safe_links(self):
        self.content_type = "text/html; charset=utf-8"
        self.body = ("<html><h1>Accounting vacancy</h1><script>SECRET_SCRIPT</script>"
                     "<style>SECRET_STYLE</style><noscript>SECRET_NOSCRIPT</noscript>"
                     "<svg><text>SECRET_SVG</text></svg>"
                     "<p>Responsibilities include invoice review, reporting and reconciliation.</p>"
                     "<p>Requirements include a relevant degree and confirmed graduation date.</p>"
                     "<a href='/apply'>Apply &amp; review</a><a href='/apply'>Duplicate</a>"
                     "<a href='javascript:alert(1)'>Ignore</a><a href='mailto:private@example.test'>Mail</a>"
                     "<a href='https://user:password@example.test'>Credentials</a></html>").encode()
        result = sources.fetch(self.spec, self.config, self.cache)
        for forbidden in ("SECRET_SCRIPT", "SECRET_STYLE", "SECRET_NOSCRIPT", "SECRET_SVG", "<p>"):
            self.assertNotIn(forbidden, result["text"])
        self.assertIn("Apply & review", result["text"])
        self.assertEqual(result["links"], ["https://careers.example.test/apply"])

    def test_robots_denies_without_fetching_jd(self):
        self.robots = b"User-agent: *\nDisallow: /jobs/"
        self.assert_error("robots_denied")
        self.assertEqual(self.http.call_count, 1)

    def test_robots_unavailable_is_fail_closed(self):
        for code in ("http_403", "http_503", "network_error"):
            with self.subTest(code=code):
                self.http.reset_mock()
                self.robots_error = AdapterError(code, "fixture error")
                self.assert_error("robots_unavailable")
                self.assertEqual(self.http.call_count, 1)

    def test_robots_404_allows_collection(self):
        self.robots_error = AdapterError("http_404", "fixture missing robots")
        result = sources.fetch(self.spec, self.config, self.cache)
        self.assertFalse(result["cache_hit"])
        self.assertEqual(self.http.call_count, 2)

    def test_cache_hit_preserves_original_capture_timestamp(self):
        first = sources.fetch(self.spec, self.config, self.cache)
        self.now += 20
        self.capture = "2026-09-13T01:00:00+00:00"
        self.http.reset_mock()
        second = sources.fetch(self.spec, self.config, self.cache)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["captured_at"], first["captured_at"])
        self.http.assert_not_called()

    def test_expired_cache_fetches_new_text_and_capture(self):
        sources.fetch(self.spec, self.config, self.cache)
        self.now += 61
        self.capture = "2026-09-13T02:00:00+00:00"
        self.body = b"Newly captured synthetic JD. " * 6
        self.http.reset_mock()
        result = sources.fetch(self.spec, self.config, self.cache)
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["captured_at"], self.capture)
        self.assertEqual(result["text"], self.body.decode())
        self.assertEqual(self.http.call_count, 1)  # robots cache is still fresh

    def test_force_bypasses_page_cache_not_capture_time(self):
        sources.fetch(self.spec, self.config, self.cache)
        self.capture = "2026-09-13T03:00:00+00:00"
        self.http.reset_mock()
        result = sources.fetch(self.spec, self.config, self.cache, force=True)
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["captured_at"], self.capture)
        self.assertEqual(self.http.call_count, 1)

    def test_short_text_is_not_complete_jd(self):
        self.body = b"a" * 79
        self.assert_error("incomplete_source")

    def test_minimum_text_boundary_is_inclusive(self):
        self.body = b"a" * 80
        result = sources.fetch(self.spec, self.config, self.cache)
        self.assertEqual(len(result["text"]), 80)

    def test_maximum_text_boundary_is_inclusive(self):
        self.body = b"a" * 100
        result = sources.fetch(self.spec, dict(self.config, max_source_chars=100), self.cache)
        self.assertEqual(len(result["text"]), 100)

    def test_oversized_text_is_rejected(self):
        self.body = b"a" * 101
        self.assert_error("source_too_large", config=dict(self.config, max_source_chars=100))

    def test_cached_page_cannot_bypass_tightened_text_limit(self):
        self.body = b"a" * 200
        sources.fetch(self.spec, dict(self.config, max_source_chars=300), self.cache)
        self.assert_error("source_too_large", config=dict(self.config, max_source_chars=100))

    def test_binary_pdf_requires_document_adapter(self):
        self.content_type = "application/pdf"
        self.body = b"%PDF" + b"binary" * 20
        self.assert_error("unsupported_source")

    def test_missing_allowlist_rejects_before_http(self):
        self.assert_error("source_not_allowed", config={"source_terms_accepted": True})
        self.http.assert_not_called()

    def test_lookalike_subdomain_not_in_exact_allowlist(self):
        self.assert_error("source_not_allowed", spec={"url": "https://careers.example.test.attacker.test/jobs"})
        self.http.assert_not_called()

    def test_terms_confirmation_required_even_for_cached_content(self):
        sources.fetch(self.spec, self.config, self.cache)
        self.http.reset_mock()
        self.assert_error("terms_confirmation_required", config=dict(self.config, source_terms_accepted=False))
        self.http.assert_not_called()

    def test_final_redirect_outside_allowlist_is_rejected(self):
        self.final_url = "https://other.example.test/jobs/redirected"
        self.assert_error("redirect_scope")

    def test_redirect_to_explicitly_allowed_host_is_accepted(self):
        self.final_url = "https://other.example.test/jobs/redirected"
        config = dict(self.config, allowed_source_hosts=["careers.example.test", "other.example.test"])
        result = sources.fetch(self.spec, config, self.cache)
        self.assertEqual(result["url"], self.final_url)

    def test_changing_allowlist_invalidates_cached_page(self):
        sources.fetch(self.spec, self.config, self.cache)
        self.http.reset_mock()
        config = dict(self.config, allowed_source_hosts=["careers.example.test", "other.example.test"])
        result = sources.fetch(self.spec, config, self.cache)
        self.assertFalse(result["cache_hit"])
        self.assertEqual(self.http.call_count, 1)


if __name__ == "__main__":
    unittest.main()
