"""Protocol/error tests; mocked calls are deliberately not labelled live checks."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jobmatch_runtime import adapters_data as adapter
from jobmatch_runtime.common import AdapterError


def csv_bytes(columns, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


class DataAdaptersTest(unittest.TestCase):
    def assert_error(self, code, function, *args):
        with self.assertRaises(AdapterError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def test_dispatch_and_objects(self):
        self.assert_error("unsupported_component", adapter.run, "unknown", {}, {})
        self.assert_error("invalid_input", adapter.run, "esco", [], {})

    def test_query_limit_and_tables(self):
        for component in ("onet_database", "esco", "tabiya_open_dataset"):
            for payload in ({}, {"query": " "}, {"query": []}, {"query": "a", "limit": True},
                            {"query": "a", "limit": 0}, {"query": "a", "limit": 101}):
                with self.subTest(component=component, payload=payload):
                    self.assert_error("invalid_input", adapter.run, component, payload, {})
        for component in ("onet_database", "tabiya_open_dataset"):
            self.assert_error("invalid_input", adapter.run, component, {"query": "a", "table": "../../x"}, {})

    def test_onet_reads_official_versioned_csv(self):
        raw = b'O*NET-SOC Code,Title,Description\n13-2011.00,Accountants and Auditors,Inspect financial accounts\n'
        with patch.object(adapter, "_official_bytes", return_value=raw) as download:
            result = adapter.run("onet_database", {"query": "accountants", "limit": 1}, {})
        self.assertEqual(result["upstream_version"], "31.0")
        self.assertEqual(result["data"]["matched_count"], 1)
        self.assertEqual(len(result["data"]["source_sha256"]), 64)
        self.assertEqual(download.call_args.args[0], adapter.ONET_BASE + "occupation_data.csv")

    def test_onet_bad_soc_code(self):
        raw = b'O*NET-SOC Code,Title,Description\nnot-a-code,Accountants,Description\n'
        with patch.object(adapter, "_official_bytes", return_value=raw):
            self.assert_error("upstream_schema_error", adapter.run, "onet_database", {"query": "a"}, {})

    def test_csv_schema_utf8_rows_empty(self):
        for raw in (b'a,a\nx,x\n', b'b\nx\n', b'a\n', b'a,b\nx\n', b'a\nx,y\n', b'\xff'):
            self.assert_error("upstream_schema_error", adapter._csv_rows, raw, {"a"})

    def test_official_download_errors_are_sanitized(self):
        for failure, code in ((HTTPError("https://official.invalid", 503, "private details", {}, None), "upstream_http_error"),
                              (URLError("private details"), "upstream_unavailable")):
            with patch.object(adapter, "build_opener") as opener:
                opener.return_value.open.side_effect = failure
                self.assert_error(code, adapter._official_bytes, adapter.ONET_BASE + "occupation_data.csv")

    def test_official_download_size_and_redirect_limits(self):
        with patch.object(adapter, "MAX_DOWNLOAD_BYTES", 2), patch.object(adapter, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b"abc"
            self.assert_error("response_too_large", adapter._official_bytes, adapter.ONET_BASE + "occupation_data.csv")
        request = Request(adapter.ONET_BASE + "occupation_data.csv")
        self.assert_error("unsafe_redirect", adapter._OfficialRedirects().redirect_request,
                          request, None, 302, "", {}, "https://untrusted.invalid/data.csv")

    def test_esco_calls_pinned_search_and_validates_uris(self):
        response = {"_embedded": {"results": [{"uri": "http://data.europa.eu/esco/occupation/example", "title": "Accountant"}]}}
        with patch.object(adapter, "http_json", return_value=response) as request:
            result = adapter.run("esco", {"query": "accountant & auditor"}, {})
        self.assertEqual(result["upstream_version"], "1.2.1")
        self.assertIn("selectedVersion=1.2.1", request.call_args.args[0])
        self.assertIn("accountant+%26+auditor", request.call_args.args[0])
        self.assertEqual(len(result["data"]["results"]), 1)

    def test_esco_does_not_downgrade_or_accept_bad_protocol(self):
        with patch.object(adapter, "http_json", side_effect=AdapterError("upstream_http_error", "HTTP 500")) as request:
            self.assert_error("upstream_http_error", adapter.run, "esco", {"query": "a"}, {})
            self.assertEqual(request.call_count, 1)
        for response in ([], {}, {"_embedded": {"results": [{}]}}, {"_embedded": {"results": [{"uri": "https://attacker.invalid"}]}}):
            with patch.object(adapter, "http_json", return_value=response):
                self.assert_error("upstream_schema_error", adapter.run, "esco", {"query": "a"}, {})
        self.assert_error("unsupported_language", adapter.run, "esco", {"query": "a", "language": "zh"}, {})
        self.assert_error("invalid_input", adapter.run, "esco", {"query": "a", "type": "x"}, {})

    def tabiya_csv(self, table="skills", duplicate=False):
        fields = sorted(adapter.TABIYA_COLUMNS[table])
        if table == "occupation_skill_relations":
            row = {"OCCUPATIONTYPE": "escooccupation", "OCCUPATIONID": "key_1", "SKILLID": "key_2", "RELATIONTYPE": "essential"}
        else:
            row = {field: "" for field in fields}
            row.update({"ID": "key_1", "ORIGINURI": "http://data.europa.eu/esco/skill/example", "PREFERREDLABEL": "Financial accounting"})
        return csv_bytes(fields, [row, row] if duplicate else [row])

    def test_tabiya_uses_official_blob_and_schema(self):
        with patch.object(adapter, "_official_bytes", return_value=self.tabiya_csv()) as request:
            result = adapter.run("tabiya_open_dataset", {"query": "accounting"}, {})
        self.assertIn(adapter.PINS["tabiya_open_dataset"], request.call_args.args[0])
        self.assertEqual(result["data"]["matched_count"], 1)
        self.assertIn("1.1.1", result["warnings"][-1])

    def test_tabiya_rejects_duplicate_concepts_and_foreign_schema(self):
        for raw in (self.tabiya_csv(duplicate=True), b'ID,PREFERREDLABEL\nkey_1,Accounting\n'):
            with patch.object(adapter, "_official_bytes", return_value=raw):
                self.assert_error("upstream_schema_error", adapter.run, "tabiya_open_dataset", {"query": "a"}, {})

    def test_tabiya_relations(self):
        with patch.object(adapter, "_official_bytes", return_value=self.tabiya_csv("occupation_skill_relations")):
            result = adapter.run("tabiya_open_dataset", {"query": "key_1", "table": "occupation_skill_relations"}, {})
        self.assertEqual(result["data"]["matched_count"], 1)

    def test_checkout_pin_and_dirty_detection(self):
        with tempfile.TemporaryDirectory() as root:
            good = subprocess.CompletedProcess([], 0, adapter.PINS["melo_benchmark"] + "\n", "")
            clean = subprocess.CompletedProcess([], 0, "", "")
            bad = subprocess.CompletedProcess([], 0, "wrong\n", "")
            dirty = subprocess.CompletedProcess([], 0, "?? injected.py\n", "")
            with patch.object(adapter.subprocess, "run", side_effect=[good, clean]):
                self.assertEqual(adapter._checkout({"checkout": root}, "melo_benchmark"), Path(root).resolve())
            with patch.object(adapter.subprocess, "run", side_effect=[bad, clean]):
                self.assert_error("version_mismatch", adapter._checkout, {"checkout": root}, "melo_benchmark")
            with patch.object(adapter.subprocess, "run", side_effect=[good, dirty]):
                self.assert_error("dirty_checkout", adapter._checkout, {"checkout": root}, "melo_benchmark")

    def test_missing_checkout_is_explicit(self):
        payload = {"task": "occupation", "instances": [{"gold": ["accountant"], "predicted": ["accountant"]}]}
        self.assert_error("missing_dependency", adapter.run, "tgre_classification", payload, {})

    def melo_payload(self):
        return {"query_ids": ["q1"], "corpus_ids": ["c1", "c2"], "scores": [[0.8, 0.1]], "relevance": {"q1": {"c1": 1}}}

    def test_melo_validates_matrix_qrels_ids(self):
        adapter._validate_melo(self.melo_payload())
        changes = [{"scores": [[float("nan"), 1]]}, {"scores": [[True, 1]]}, {"scores": [[]]},
                   {"query_ids": ["bad\nid"]}, {"corpus_ids": ["c1", "c1"]}, {"relevance": {}},
                   {"relevance": {"q1": {"c1": 0}}}, {"relevance": {"q1": {"other": 1}}}]
        for change in changes:
            self.assert_error("invalid_input", adapter._validate_melo, {**self.melo_payload(), **change})

    def test_tgre_validates_instance_lists(self):
        for payload in ({}, {"task": "skill", "instances": []}, {"task": "skill", "instances": [1]},
                        {"task": "skill", "instances": [{"gold": [], "predicted": ["a"]}]}):
            self.assert_error("invalid_input", adapter._validate_tgre, payload)

    def test_evaluation_calls_real_entrypoint_program_with_offline_environment(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "data": {"metrics": {"map": 1.0}}}), "")
        with patch.object(adapter, "_checkout", return_value=Path("/tmp")), patch.object(adapter.subprocess, "run", return_value=result) as process:
            output = adapter.run("melo_benchmark", self.melo_payload(), {})
        self.assertEqual(output["transport"], "pinned_python_subprocess")
        self.assertIn("evaluator.evaluate(SuppliedScores())", process.call_args.args[0][-1])
        self.assertEqual(process.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertNotIn("OPENAI_API_KEY", process.call_args.kwargs["env"])

    def test_upstream_failure_not_success(self):
        responses = [("not json", "upstream_protocol_error"),
                     (json.dumps({"ok": False, "code": "missing_dependency", "message": "Missing upstream library"}), "missing_dependency"),
                     (json.dumps({"ok": True, "data": []}), "upstream_protocol_error")]
        for raw, code in responses:
            with patch.object(adapter, "_checkout", return_value=Path("/tmp")), patch.object(adapter.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, raw, "")):
                self.assert_error(code, adapter.run, "melo_benchmark", self.melo_payload(), {})

    def test_evaluation_timeout_and_input_timeout(self):
        with patch.object(adapter, "_checkout", return_value=Path("/tmp")):
            self.assert_error("invalid_input", adapter.run, "melo_benchmark", self.melo_payload(), {"timeout": 500})
            with patch.object(adapter.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 1)):
                self.assert_error("upstream_timeout", adapter.run, "melo_benchmark", self.melo_payload(), {})


if __name__ == "__main__":
    unittest.main()
