"""Versioned taxonomy reads and calls to pinned upstream evaluation code.

Payloads:
  O*NET: query, table (occupation_data/essential_skills/task_statements), limit.
  ESCO: query, type (occupation/skill), language, limit.
  Tabiya: query, table (skills/occupations/occupation_skill_relations), limit.
  MELO: query_ids, corpus_ids, scores (query x corpus), relevance (q -> c -> int).
  TGRE: task (occupation/skill), instances [{gold: [...], predicted: [...]}].
Config: checkout for local pinned Tabiya/MELO/TGRE; python and timeout for
evaluation. ESCO uses the public API and never silently substitutes versions.
No package installation, model download, or model inference occurs here.
"""

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

from .common import AdapterError, http_json


PINS = {
    "onet_database": "31.0",
    "esco": "1.2.1",
    "tabiya_open_dataset": "815c85d4be9b059c927156d2819e41e904d1d442",
    "melo_benchmark": "014982fef0abf5149b16e75b1043bd286ae4c98f",
    "tgre_classification": "91fccb7a513edc8e069491052bf5979f7cac4525",
}
ONET_BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/"
ESCO_BASE = "https://ec.europa.eu/esco/api/search"
TABIYA_BASE = ("https://raw.githubusercontent.com/tabiya-tech/tabiya-open-dataset/"
               + PINS["tabiya_open_dataset"] + "/tabiya-esco-v1.1.1/csv/")
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
ONET_COLUMNS = {
    "occupation_data": {"O*NET-SOC Code", "Title", "Description"},
    "essential_skills": {"O*NET-SOC Code", "Element ID", "Element Name", "Data Value"},
    "task_statements": {"O*NET-SOC Code", "Task ID", "Task"},
}
TABIYA_COLUMNS = {
    "skills": {"ORIGINURI", "ID", "UUIDHISTORY", "SKILLTYPE", "REUSELEVEL",
               "PREFERREDLABEL", "ALTLABELS", "DESCRIPTION", "DEFINITION", "SCOPENOTE"},
    "occupations": {"ORIGINURI", "ID", "UUIDHISTORY", "ISCOGROUPCODE", "CODE",
                    "PREFERREDLABEL", "ALTLABELS", "DESCRIPTION", "DEFINITION",
                    "SCOPENOTE", "REGULATEDPROFESSIONNOTE", "OCCUPATIONTYPE", "ISLOCALIZED"},
    "occupation_skill_relations": {"OCCUPATIONTYPE", "OCCUPATIONID", "RELATIONTYPE", "SKILLID"},
}


def _text(value, name, max_length=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise AdapterError("invalid_input", name + " must be a nonempty bounded string")
    return value.strip()


def _limit(payload):
    value = payload.get("limit", 20)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise AdapterError("invalid_input", "limit must be an integer from 1 to 100")
    return value


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlparse(req.full_url), urlparse(newurl)
        if new.scheme != "https" or new.netloc != old.netloc:
            raise AdapterError("unsafe_redirect", "Official download changed origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _official_bytes(url):
    """Only callers' fixed official URLs enter this function, never a payload URL."""
    request = Request(url, headers={"User-Agent": "jobmatch-skill/1.0", "Accept": "text/csv"})
    try:
        with build_opener(_OfficialRedirects()).open(request, timeout=30) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    except HTTPError as exc:
        raise AdapterError("upstream_http_error", "Official download returned HTTP " + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise AdapterError("upstream_unavailable", "Official download could not be verified") from None
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise AdapterError("response_too_large", "Official dataset exceeds the bounded download size")
    return raw


def _csv_rows(raw, required, exact=False):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required.issubset(fields):
            raise AdapterError("upstream_schema_error", "Official CSV required columns are missing or duplicated")
        if exact and set(fields) != required:
            raise AdapterError("upstream_schema_error", "Pinned CSV column schema changed")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise AdapterError("upstream_schema_error", "CSV row has an inconsistent number of fields")
            rows.append(row)
        if not rows:
            raise AdapterError("upstream_schema_error", "Official CSV contains no data rows")
        return rows
    except (UnicodeError, csv.Error):
        raise AdapterError("upstream_schema_error", "Official CSV is not valid UTF-8 CSV") from None


def _result(component, data, transport, warnings):
    return {"data": data, "transport": transport, "upstream_version": PINS[component], "warnings": warnings}


def _taxonomy_result(component, query, rows, raw, source, transport, limit):
    selected = [row for row in rows if query.casefold() in " ".join(row.values()).casefold()]
    return _result(component, {
        "rows": selected[:limit], "matched_count": len(selected), "dataset_rows": len(rows),
        "source_url": source, "source_sha256": hashlib.sha256(raw).hexdigest(),
    }, transport, ["Taxonomy references are not evidence of a vacancy or candidate eligibility."])


def _onet(payload, config):
    query, limit = _text(payload.get("query"), "query"), _limit(payload)
    table = payload.get("table", "occupation_data")
    if not isinstance(table, str) or table not in ONET_COLUMNS:
        raise AdapterError("invalid_input", "Unsupported O*NET table")
    url = ONET_BASE + table + ".csv"
    raw = _official_bytes(url)
    rows = _csv_rows(raw, ONET_COLUMNS[table])
    if any(not re.fullmatch(r"[0-9]{2}-[0-9]{4}\.[0-9]{2}", row["O*NET-SOC Code"]) for row in rows):
        raise AdapterError("upstream_schema_error", "O*NET-SOC code schema is invalid")
    return _taxonomy_result("onet_database", query, rows, raw, url, "official_https_csv", limit)


def _esco(payload, config):
    query, limit = _text(payload.get("query"), "query"), _limit(payload)
    kind = payload.get("type", "occupation")
    language = payload.get("language", "en")
    if kind not in ("occupation", "skill") or not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language):
        raise AdapterError("invalid_input", "ESCO type must be occupation/skill and language a two-letter code")
    if language == "zh":
        raise AdapterError("unsupported_language", "ESCO has no official Chinese labels; supply a reviewed translation")
    url = ESCO_BASE + "?" + urlencode({"text": query, "type": kind, "language": language,
        "limit": limit, "offset": 0, "selectedVersion": PINS["esco"]})
    response = http_json(url, timeout=30)
    if not isinstance(response, dict) or not isinstance(response.get("_embedded"), dict):
        raise AdapterError("upstream_schema_error", "ESCO response is not a HAL search collection")
    records = response["_embedded"].get("results")
    if not isinstance(records, list) or any(not isinstance(row, dict) or not isinstance(row.get("uri"), str)
            or not row["uri"].startswith("http://data.europa.eu/esco/" + kind + "/")
            for row in records):
        raise AdapterError("upstream_schema_error", "ESCO results lack valid concept URIs")
    return _result("esco", {"results": records, "source_url": url}, "official_https_api", [
        "selectedVersion=1.2.1 is explicitly requested; the adapter never falls back to another release.",
        "ESCO taxonomy matches do not prove Chinese JD requirements or application eligibility.",
    ])


def _checkout(config, component):
    if not config.get("checkout"):
        raise AdapterError("missing_dependency", "Configure a local checkout of the pinned upstream commit")
    root = Path(_text(config.get("checkout"), "config.checkout")).expanduser().resolve()
    if not root.is_dir():
        raise AdapterError("missing_dependency", "Pinned upstream checkout is not available")
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        raise AdapterError("missing_dependency", "Unable to verify the upstream Git checkout") from None
    if head != PINS[component]:
        raise AdapterError("version_mismatch", "Upstream checkout does not match the registered commit")
    if dirty:
        raise AdapterError("dirty_checkout", "Upstream checkout must contain only clean, pinned files")
    return root


def _tabiya(payload, config):
    query, limit = _text(payload.get("query"), "query"), _limit(payload)
    table = payload.get("table", "skills")
    if not isinstance(table, str) or table not in TABIYA_COLUMNS:
        raise AdapterError("invalid_input", "Unsupported Tabiya table")
    url = TABIYA_BASE + table + ".csv"
    if config.get("checkout"):
        root = _checkout(config, "tabiya_open_dataset")
        relative = "tabiya-esco-v1.1.1/csv/" + table + ".csv"
        # Read the verified commit's blob, not a mutable worktree file.
        try:
            raw = subprocess.run(["git", "-C", str(root), "show", "HEAD:" + relative],
                capture_output=True, check=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            raise AdapterError("missing_dependency", "Pinned Tabiya CSV blob is unavailable") from None
        transport = "pinned_git_csv"
    else:
        raw = _official_bytes(url)
        transport = "official_https_csv"
    rows = _csv_rows(raw, TABIYA_COLUMNS[table], exact=True)
    if table == "occupation_skill_relations":
        if any(row["RELATIONTYPE"] not in {"essential", "optional"} or not row["OCCUPATIONID"]
               or not row["SKILLID"] for row in rows):
            raise AdapterError("upstream_schema_error", "Invalid Tabiya occupation-skill relationship")
    else:
        uri_type = "skill" if table == "skills" else "occupation"
        ids = [row["ID"] for row in rows]
        if len(ids) != len(set(ids)) or any(not value for value in ids) or any(
            not row["PREFERREDLABEL"] or not row["ORIGINURI"].startswith("http://data.europa.eu/esco/" + uri_type + "/")
            for row in rows
        ):
            raise AdapterError("upstream_schema_error", "Invalid or duplicate Tabiya concept identifiers")
    result = _taxonomy_result("tabiya_open_dataset", query, rows, raw, url, transport, limit)
    result["warnings"].append("Tabiya's pinned dataset uses ESCO 1.1.1, not the ESCO 1.2.1 baseline.")
    return result


def _string_list(value, name, maximum=5000):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise AdapterError("invalid_input", name + " must be a nonempty bounded list")
    return [_text(item, name + " item") for item in value]


def _validate_melo(payload):
    queries = _string_list(payload.get("query_ids"), "query_ids", 1000)
    corpus = _string_list(payload.get("corpus_ids"), "corpus_ids")
    if len(set(queries)) != len(queries) or len(set(corpus)) != len(corpus):
        raise AdapterError("invalid_input", "MELO IDs must be unique")
    if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item) for item in queries + corpus):
        raise AdapterError("invalid_input", "MELO IDs must be TREC-safe ASCII identifiers")
    scores, relevance = payload.get("scores"), payload.get("relevance")
    if len(queries) * len(corpus) > 1000000 or not isinstance(scores, list) or len(scores) != len(queries):
        raise AdapterError("invalid_input", "MELO score matrix has invalid dimensions")
    for row in scores:
        if not isinstance(row, list) or len(row) != len(corpus) or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in row
        ):
            raise AdapterError("invalid_input", "MELO scores must be a finite numeric query-by-corpus matrix")
    if not isinstance(relevance, dict) or set(relevance) != set(queries):
        raise AdapterError("invalid_input", "MELO relevance must cover exactly the query IDs")
    for rels in relevance.values():
        if not isinstance(rels, dict) or not rels or not set(rels).issubset(corpus) or any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100 for value in rels.values()
        ) or not any(rels.values()):
            raise AdapterError("invalid_input", "MELO relevance needs in-corpus IDs and positive integer judgments")


def _validate_tgre(payload):
    if payload.get("task") not in ("occupation", "skill"):
        raise AdapterError("invalid_input", "TGRE task must be occupation or skill")
    instances = payload.get("instances")
    if not isinstance(instances, list) or not 1 <= len(instances) <= 1000:
        raise AdapterError("invalid_input", "TGRE instances must be a nonempty list of at most 1000 entries")
    for item in instances:
        if not isinstance(item, dict):
            raise AdapterError("invalid_input", "TGRE instance must be an object")
        _string_list(item.get("gold"), "gold", 100)
        _string_list(item.get("predicted"), "predicted", 100)


_EVALUATION_PROGRAM = r'''
import contextlib, importlib.util, io, json, os, pathlib, shutil, sys, tempfile
args = json.load(sys.stdin)
root, component, payload = pathlib.Path(args["checkout"]), args["component"], args["payload"]
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if component == "tgre_classification":
            spec = importlib.util.spec_from_file_location("jobmatch_upstream_tgre", root / "classification/compute_scores.py")
            upstream = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(upstream)
            instances = []
            for item in payload["instances"]:
                gold = [upstream.preprocess_label(s) for s in item["gold"]]
                pred = [upstream.preprocess_label(s) for s in item["predicted"]]
                if any(not s for s in gold + pred) or len(set(gold)) != len(gold) or len(set(pred)) != len(pred):
                    raise ValueError("TGRE normalized labels must be nonempty and unique")
                metrics = ({"precision@1": upstream.compute_instance_precision_at_k(gold, pred, k=1)}
                    if payload["task"] == "occupation" else upstream.compute_instance_rp_at_k(gold, pred, k_list=[3, 5, 10]))
                instances.append(metrics)
            data = {"instances": instances, "mean_metrics": {
                key: sum(item[key] for item in instances) / len(instances) for key in instances[0]},
                "upstream_entrypoint": "classification.compute_scores:compute_instance_precision_at_k/compute_instance_rp_at_k"}
        else:
            sys.path.insert(0, str(root / "src"))
            from melo_benchmark.evaluation.evaluator import Evaluator
            from melo_benchmark.evaluation.scorer import BaseScorer
            class SuppliedScores(BaseScorer):
                def compute_scores(self, q_surface_forms, c_surface_forms):
                    return payload["scores"]
            with tempfile.TemporaryDirectory(prefix="jobmatch-melo-", dir="/tmp") as directory:
                work = pathlib.Path(directory)
                qfile, cfile, afile = work / "queries.tsv", work / "corpus_elements.tsv", work / "annotations.tsv"
                qfile.write_text("".join(q + "\t" + q + "\n" for q in payload["query_ids"]), encoding="utf-8")
                cfile.write_text("".join(c + "\t" + c + "\n" for c in payload["corpus_ids"]), encoding="utf-8")
                afile.write_text("".join(q + "\t0\t" + c + "\t" + str(rel) + "\n"
                    for q, values in payload["relevance"].items() for c, rel in values.items()), encoding="utf-8")
                binary = work / "trec_eval"
                shutil.copyfile(root / "resources/trec_eval", binary)
                binary.chmod(0o700)
                evaluator = Evaluator(str(qfile), str(cfile), str(afile), str(work), max_relevant_to_consider=100)
                # Upstream shells out without quoting: all shell-visible paths are fixed safe temporary paths.
                evaluator.trec_eval_binary_path = str(binary)
                metrics = evaluator.evaluate(SuppliedScores())
                data = {"metrics": {str(key.value if hasattr(key, "value") else key): float(value)
                    for key, value in metrics}, "upstream_entrypoint": "melo_benchmark.evaluation.evaluator:Evaluator.evaluate"}
                if not data["metrics"] or any(value < 0 for value in data["metrics"].values()):
                    raise RuntimeError("MELO trec_eval failed; ensure the pinned binary supports this operating system")
                os.chdir("/tmp")
    print(json.dumps({"ok": True, "data": data}, allow_nan=False))
except (ImportError, ModuleNotFoundError) as exc:
    print(json.dumps({"ok": False, "code": "missing_dependency", "message": "Required upstream Python dependency is not installed"}))
except ValueError:
    print(json.dumps({"ok": False, "code": "invalid_input", "message": "Upstream rejected the supplied evaluation labels or scores"}))
except Exception:
    print(json.dumps({"ok": False, "code": "upstream_execution_failed", "message": "Pinned evaluation failed; check upstream dependencies and platform compatibility"}))
'''


def _evaluate(component, payload, config):
    (_validate_melo if component == "melo_benchmark" else _validate_tgre)(payload)
    root = _checkout(config, component)
    executable = config.get("python", sys.executable)
    if not isinstance(executable, str) or not Path(executable).is_file():
        raise AdapterError("missing_dependency", "config.python must identify an installed Python executable")
    timeout = config.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
        raise AdapterError("invalid_input", "timeout must be from 1 to 300 seconds")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1"})
    args = {"component": component, "payload": payload, "checkout": str(root)}
    try:
        process = subprocess.run([executable, "-B", "-c", _EVALUATION_PROGRAM],
            input=json.dumps(args, allow_nan=False), text=True, capture_output=True, timeout=timeout,
            cwd=tempfile.gettempdir(), env=env, check=False)
    except subprocess.TimeoutExpired:
        raise AdapterError("upstream_timeout", "Pinned upstream evaluation timed out") from None
    except OSError:
        raise AdapterError("missing_dependency", "Unable to start the selected Python executable") from None
    try:
        output = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise AdapterError("upstream_protocol_error", "Pinned evaluation returned no valid JSON result") from None
    if process.returncode or not isinstance(output, dict) or not isinstance(output.get("ok"), bool):
        raise AdapterError("upstream_execution_failed", "Pinned evaluation process failed")
    if output["ok"] is False:
        code = output.get("code")
        if code not in {"missing_dependency", "invalid_input", "upstream_execution_failed"}:
            code = "upstream_execution_failed"
        raise AdapterError(code, output.get("message", "Pinned upstream evaluation failed"))
    if not isinstance(output.get("data"), dict):
        raise AdapterError("upstream_protocol_error", "Pinned evaluation result lacks data")
    return _result(component, output["data"], "pinned_python_subprocess", [
        "Evaluation of supplied predictions only; no model training, inference, or download was performed.",
        "A successful test run is not a full benchmark result or evidence of Chinese-market accuracy.",
    ])


def run(component_id, payload, config):
    """Run one explicit component call; dispatch and cache policy live in the parent runtime."""
    if not isinstance(payload, dict) or not isinstance(config, dict):
        raise AdapterError("invalid_input", "payload and config must be JSON objects")
    if component_id == "onet_database":
        return _onet(payload, config)
    if component_id == "esco":
        return _esco(payload, config)
    if component_id == "tabiya_open_dataset":
        return _tabiya(payload, config)
    if component_id in ("melo_benchmark", "tgre_classification"):
        return _evaluate(component_id, payload, config)
    raise AdapterError("unsupported_component", "No data adapter exists for this component")
