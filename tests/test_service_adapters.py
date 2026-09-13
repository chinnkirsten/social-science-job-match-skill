"""Contract tests: local HTTP fixtures and SDK doubles are not upstream live tests."""

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jobmatch_runtime import adapters_services as adapters
from jobmatch_runtime.common import AdapterError


class Handler(BaseHTTPRequestHandler):
    requests = []
    responses = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.requests.append(("POST", self.path, body, dict(self.headers)))
        self.reply()

    def do_GET(self):
        self.requests.append(("GET", self.path, None, dict(self.headers)))
        self.reply()

    def reply(self):
        status, body = self.responses.get(self.path, (404, {"detail": "unexpected route"}))
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class ServiceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.requests.clear()
        Handler.responses.clear()
        self.config = {"service_url": f"http://127.0.0.1:{self.server.server_port}",
                       "allow_local": True, "service_egress": "local_only", "service_ready": True}

    def assert_code(self, code, component, payload, config=None):
        with self.assertRaises(AdapterError) as caught:
            adapters.run(component, payload, self.config if config is None else config)
        self.assertEqual(caught.exception.code, code)

    def test_classifier_protocol_and_real_http(self):
        Handler.responses["/v1/classify"] = (200, {"classification": {"entities": [], "entity_counts": {}},
                                                       "metadata": {"classifier_version": "fixture-version"}})
        with patch.dict("os.environ", {"JOBMATCH_TEST_API_KEY": "fixture-secret"}):
            result = adapters.run("tabiya_livelihoods_classifier", {"text": "Public test JD", "options": {"language": "en"}},
                                  dict(self.config, api_key_env="JOBMATCH_TEST_API_KEY"))
        method, path, body, headers = Handler.requests[0]
        self.assertEqual((method, path), ("POST", "/v1/classify"))
        self.assertEqual(body, {"text": "Public test JD", "options": {"language": "en"}})
        self.assertEqual(headers.get("X-Api-Key"), "fixture-secret")
        self.assertEqual(result["upstream_version"], "fixture-version")
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_private_local_proxy_requires_egress_consent(self):
        config = dict(self.config)
        config.pop("service_egress")
        self.assert_code("PRIVACY_CONSENT_REQUIRED", "tabiya_livelihoods_classifier", {"text": "private"}, config)
        self.assertEqual(Handler.requests, [])

    def test_public_input_still_requires_ready_service(self):
        self.assert_code("SERVICE_NOT_READY", "tabiya_livelihoods_classifier", {"text": "JD", "data_classification": "public"},
                         dict(self.config, service_ready=False))

    def test_compass_existing_session_contract(self):
        path = "/conversations/12/messages?filter_pii=true"
        Handler.responses[path] = (200, {"messages": [], "current_phase": {"phase": "intro"}})
        result = adapters.run("tabiya_compass", {"session_id": 12, "user_input": "Synthetic experience"}, self.config)
        self.assertEqual(Handler.requests[0][1:3], (path, {"user_input": "Synthetic experience"}))
        self.assertEqual(result["upstream_version"], "unverified")

    def test_compass_never_trusts_public_label(self):
        config = dict(self.config)
        config.pop("service_egress")
        self.assert_code("PRIVACY_CONSENT_REQUIRED", "tabiya_compass", {"session_id": 1, "user_input": "text", "data_classification": "public"}, config)

    def test_resume_matcher_preview_does_not_confirm(self):
        path = "/api/v1/resumes/improve/preview"
        Handler.responses[path] = (200, {"request_id": "fixture", "data": {"resume_preview": {}, "resume_id": None}})
        adapters.run("resume_matcher", {"resume_id": "resume-fixture", "job_id": "jd-fixture"}, self.config)
        self.assertEqual([request[1] for request in Handler.requests], [path])
        self.assertEqual(Handler.requests[0][2], {"resume_id": "resume-fixture", "job_id": "jd-fixture"})

    def test_esco_two_stage_async_protocol(self):
        Handler.responses.update({
            "/api/occupation/match": (200, {"job_id": "occupation-task"}),
            "/api/jobs/occupation-task": (200, {"status": "done", "result": {"matches": [{"occupation_name": "accountant"}]}}),
            "/api/skills/extract": (200, {"job_id": "skills-task"}),
            "/api/jobs/skills-task": (200, {"status": "done", "result": {"skills": [{"label": "accounting"}]}}),
        })
        config = dict(self.config, models_ready=True, llm_model="fixture-model", embedding_model="fixture-embedding")
        result = adapters.run("esco_skill_extractor", {"title": "Accountant", "description": "Synthetic JD"}, config)
        self.assertEqual(len(Handler.requests), 4)
        self.assertEqual(Handler.requests[2][2]["occupation"], "accountant")
        self.assertEqual(result["data"]["skills"], [{"label": "accounting"}])
        self.assertNotIn("openai_api_key", Handler.requests[0][2])

    def test_esco_pending_is_not_fabricated_completion(self):
        Handler.responses["/api/occupation/match"] = (200, {"job_id": "pending"})
        Handler.responses["/api/jobs/pending"] = (200, {"status": "running"})
        self.assert_code("UPSTREAM_TIMEOUT", "esco_skill_extractor", {"title": "Accountant"},
                         dict(self.config, models_ready=True, llm_model="fixture", embedding_model="fixture", max_polls=1))

    def test_esco_rejects_job_id_path_injection(self):
        Handler.responses["/api/occupation/match"] = (200, {"job_id": "../../private"})
        self.assert_code("UPSTREAM_SCHEMA", "esco_skill_extractor", {"title": "Accountant"},
                         dict(self.config, models_ready=True, llm_model="fixture", embedding_model="fixture"))
        self.assertEqual(len(Handler.requests), 1)

    def test_wrong_schema_is_not_success(self):
        Handler.responses["/v1/classify"] = (200, {"hello": "world"})
        self.assert_code("UPSTREAM_SCHEMA", "tabiya_livelihoods_classifier", {"text": "JD"})

    def test_unknown_component(self):
        self.assert_code("UNKNOWN_COMPONENT", "unlisted", {})

    def test_missing_credential_not_sent(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assert_code("CREDENTIAL_MISSING", "tabiya_livelihoods_classifier", {"text": "JD"},
                             dict(self.config, api_key_env="JOBMATCH_MISSING_TEST_KEY"))
        self.assertEqual(Handler.requests, [])


class SDKContracts(unittest.TestCase):
    def test_crawler_redirect_and_subresource_guard_contract(self):
        seen = {}
        class Route:
            def __init__(self, url):
                self.request = SimpleNamespace(url=url)
            async def abort(self):
                seen[self.request.url] = "blocked"
            async def continue_(self):
                seen[self.request.url] = "allowed"
        class Context:
            async def route(self, pattern, handler):
                self.handler = handler
        class Crawler:
            def __init__(self, config):
                seen["browser"] = config
                self.crawler_strategy = SimpleNamespace(set_hook=lambda name, fn: setattr(self, "hook", fn))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def arun(self, url, config):
                seen["run"] = config
                context = Context()
                await self.hook(object(), context)
                await context.handler(Route("http://127.0.0.1/private"))
                await context.handler(Route("https://fixture.example/public"))
                return SimpleNamespace(success=True, markdown="Synthetic JD", status_code=200,
                                       redirected_url="https://fixture.example/final")
        def validate(url, allow_local=False):
            if "127.0.0.1" in url:
                raise AdapterError("private_target", "private target")
            return url
        sdk = SimpleNamespace(BrowserConfig=lambda **kw: kw, CrawlerRunConfig=lambda **kw: kw,
                              CacheMode=SimpleNamespace(BYPASS="bypass"), AsyncWebCrawler=Crawler)
        with patch.object(adapters, "_sdk", return_value=sdk), patch.object(adapters, "validate_url", side_effect=validate):
            result = adapters.run("crawl4ai", {"url": "https://fixture.example/jd", "data_classification": "public"},
                                  {"terms_accepted": True})
        self.assertEqual(seen["http://127.0.0.1/private"], "blocked")
        self.assertEqual(seen["https://fixture.example/public"], "allowed")
        self.assertFalse(seen["browser"]["java_script_enabled"])
        self.assertTrue(seen["run"]["check_robots_txt"])
        self.assertEqual(result["data"]["final_url"], "https://fixture.example/final")

    def test_docling_uses_model_free_pdf_and_local_path(self):
        seen = {}
        class Converter:
            def __init__(self, **kwargs):
                seen.update(kwargs)
            def convert(self, source):
                seen["source"] = source
                return SimpleNamespace(status=SimpleNamespace(value="success"), document=SimpleNamespace(
                    export_to_markdown=lambda: "Synthetic document", export_to_dict=lambda: {"name": "fixture"}))
        modules = {"docling.document_converter": SimpleNamespace(DocumentConverter=Converter, NativePdfFormatOption=lambda: "native"),
                   "docling.datamodel.base_models": SimpleNamespace(InputFormat=SimpleNamespace(PDF="PDF"))}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.pdf"
            path.write_bytes(b"synthetic SDK fixture, not a real PDF")
            with patch.object(adapters, "_sdk", side_effect=lambda name: modules[name]):
                result = adapters.run("docling", {"path": str(path)}, {})
        self.assertEqual(seen["format_options"], {"PDF": "native"})
        self.assertEqual(result["data"]["markdown"], "Synthetic document")

    def test_jobspy_explicit_search_and_contact_columns_removed(self):
        seen = {}
        def scrape(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(to_json=lambda **kwargs: json.dumps([{"title": "fixture", "emails": "private", "company": "example"}]))
        with patch.object(adapters, "_sdk", return_value=SimpleNamespace(scrape_jobs=scrape)):
            result = adapters.run("jobspy", {"site_name": "indeed", "search_term": "accountant", "data_classification": "public"},
                                  {"terms_accepted": True})
        self.assertFalse(seen["linkedin_fetch_description"])
        self.assertNotIn("proxies", seen)
        self.assertEqual(result["data"]["leads"], [{"title": "fixture", "company": "example"}])
        self.assertEqual(result["data"]["verification_status"], "unverified")

    def test_crawler_private_target_rejected_before_import(self):
        with patch.object(adapters, "_sdk") as sdk:
            with self.assertRaises(AdapterError):
                adapters.run("crawl4ai", {"url": "http://127.0.0.1/private"}, {"terms_accepted": True})
            sdk.assert_not_called()

    def test_missing_sdk_is_explicit(self):
        with patch.object(adapters.importlib, "import_module", side_effect=ModuleNotFoundError("synthetic")):
            with self.assertRaises(AdapterError) as caught:
                adapters.run("jobspy", {"site_name": "indeed", "search_term": "accountant", "data_classification": "public"}, {"terms_accepted": True})
        self.assertEqual(caught.exception.code, "DEPENDENCY_MISSING")


if __name__ == "__main__":
    unittest.main()
