"""Opt-in wrappers for seven real upstream SDK/service protocols.

No dependencies, browsers, models, sessions or remote accounts are provisioned
here. Service owners must configure their own model credentials and retention.
`upstream_version` reports the installed SDK version or ``unverified``; a source
pin records protocol review, not proof of the deployed service's identity.
"""

import asyncio
import importlib
import importlib.metadata
import ipaddress
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .common import AdapterError, http_json, validate_url


PROTOCOL_SOURCES = {
    "docling": ("docling-project/docling", "5ea6490ffdc57b2fd7de5cc436f2d0a22f2214d4", "docling/document_converter.py"),
    "crawl4ai": ("unclecode/crawl4ai", "862f6bccb9c063f49b9d42701baa0eea17a4993f", "crawl4ai/async_configs.py"),
    "jobspy": ("speedyapply/JobSpy", "fda080a373e8226f3fd60635323f5da9af9892b1", "jobspy/__init__.py"),
    "tabiya_livelihoods_classifier": ("tabiya-tech/tabiya-livelihoods-classifier", "b65781202d6e5c2d70a64aa7778538e3ea70c765", "backend/classify/classify/main.py"),
    "esco_skill_extractor": ("Datalab-AUTH/esco-skill-extractor", "68288f4b3382a07ee32837f33c8bba2ccc2e4d31", "src/esco_skill_extractor/web.py"),
    "tabiya_compass": ("tabiya-tech/compass", "8470bea7f68d0990898f441a12bbb6a261a0ef34", "backend/app/conversations/routes.py"),
    "resume_matcher": ("srbhr/Resume-Matcher", "ab2b370d1fb98d9272f71b8d6e54a9c51ac75110", "apps/backend/app/routers/resumes.py"),
}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise AdapterError("INVALID_INPUT", f"{field} must be non-empty text")
    return value


def _integer(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AdapterError("INVALID_INPUT", f"{field} must be an integer in {low}..{high}")
    return value


def _sdk(module):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise AdapterError("DEPENDENCY_MISSING", f"Install the reviewed {module} dependency explicitly") from exc


def _version(distribution):
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unverified"


def _result(component, data, transport, version="unverified", warnings=()):
    repo, pin, source = PROTOCOL_SOURCES[component]
    return {"data": data, "transport": transport, "upstream_version": version,
            "protocol_pin": pin, "protocol_source": f"https://github.com/{repo}/blob/{pin}/{source}",
            "warnings": list(warnings)}


def _is_loopback(url):
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _service(payload, config, always_private=False):
    base = _text(config.get("service_url"), "config.service_url").rstrip("/")
    validate_url(base, allow_local=config.get("allow_local") is True)
    parsed = urlsplit(base)
    if parsed.query or parsed.fragment:
        raise AdapterError("INVALID_CONFIG", "service_url must not contain a query or fragment")
    private = always_private or payload.get("data_classification") != "public"
    # A localhost proxy may still forward text to a cloud LLM. Do not infer
    # local-only processing from the first network hop.
    local_only = _is_loopback(base) and config.get("service_egress") == "local_only"
    if private and not local_only and config.get("allow_external_private") is not True:
        raise AdapterError("PRIVACY_CONSENT_REQUIRED", "Private input needs allow_external_private or a declared local-only loopback service")
    if config.get("service_ready") is not True:
        raise AdapterError("SERVICE_NOT_READY", "Provision the upstream service and models first; set service_ready only after verification")
    return base


def _headers(config, classifier=False):
    headers = {}
    env_name = config.get("api_key_env" if classifier else "token_env")
    if env_name:
        if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
            raise AdapterError("INVALID_CONFIG", "Credential configuration must name an environment variable")
        value = os.environ.get(env_name)
        if not value:
            raise AdapterError("CREDENTIAL_MISSING", "Configured credential environment variable is empty")
        headers["x-api-key" if classifier else "Authorization"] = value if classifier else "Bearer " + value
    return headers


def _request(base, path, payload, config, classifier=False):
    result = http_json(base + path, payload=payload, headers=_headers(config, classifier),
                       timeout=_integer(config.get("timeout", 30), "timeout", 1, 300),
                       allow_local=config.get("allow_local") is True)
    if not isinstance(result, dict):
        raise AdapterError("UPSTREAM_SCHEMA", "Expected an upstream JSON object")
    return result


def _docling(payload, config):
    source = Path(_text(payload.get("path"), "path")).expanduser()
    if not source.is_file():
        raise AdapterError("FILE_MISSING", "The local document does not exist")
    formats = {".pdf": "PDF", ".docx": "DOCX", ".md": "MD", ".html": "HTML", ".htm": "HTML"}
    kind = formats.get(source.suffix.lower())
    if not kind:
        raise AdapterError("UNSUPPORTED_FORMAT", "Local parser supports PDF, DOCX, Markdown and HTML")
    converter = _sdk("docling.document_converter")
    models = _sdk("docling.datamodel.base_models")
    fmt = getattr(models.InputFormat, kind)
    options = {}
    if kind == "PDF":
        native = getattr(converter, "NativePdfFormatOption", None)
        if native is None:
            raise AdapterError("VERSION_UNSUPPORTED", "This Docling version lacks the reviewed model-free NativePdfFormatOption")
        options[fmt] = native()
    result = converter.DocumentConverter(allowed_formats=[fmt], format_options=options).convert(source)
    status = getattr(result.status, "value", str(result.status))
    if status != "success":
        raise AdapterError("PARSE_FAILED", "Docling did not report complete conversion success")
    markdown = result.document.export_to_markdown()
    if not markdown.strip():
        raise AdapterError("EMPTY_DOCUMENT", "No text extracted; scanned files may require separately provisioned OCR")
    return _result("docling", {"markdown": markdown, "document": result.document.export_to_dict()},
                   "local_sdk", _version("docling-slim") if _version("docling-slim") != "unverified" else _version("docling"),
                   ["Model-free parsing; no OCR or model downloads. Check extraction against the source."])


def _crawl4ai(payload, config):
    url = _text(payload.get("url"), "url")
    validate_url(url, allow_local=False)
    if payload.get("data_classification") != "public" and config.get("allow_external_private") is not True:
        raise AdapterError("PRIVACY_CONSENT_REQUIRED", "The target URL must be public or explicitly approved for external access")
    if config.get("terms_accepted") is not True:
        raise AdapterError("TERMS_REQUIRED", "Review the target site's terms before crawling")
    sdk = _sdk("crawl4ai")

    async def crawl():
        browser = sdk.BrowserConfig(headless=True, java_script_enabled=False,
                                    enable_stealth=False, use_persistent_context=False)
        run_config = sdk.CrawlerRunConfig(check_robots_txt=True, cache_mode=sdk.CacheMode.BYPASS,
                                         page_timeout=_integer(config.get("timeout", 30), "timeout", 1, 300) * 1000,
                                         max_retries=0, remove_forms=True)
        async with sdk.AsyncWebCrawler(config=browser) as crawler:
            async def guard(page, context, **kwargs):
                async def route_handler(route):
                    try:
                        validate_url(route.request.url, allow_local=False)
                    except (AdapterError, ValueError):
                        await route.abort()
                    else:
                        await route.continue_()
                await context.route("**/*", route_handler)
                return page
            crawler.crawler_strategy.set_hook("on_page_context_created", guard)
            return await crawler.arun(url=url, config=run_config)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(crawl())
    else:
        raise AdapterError("ASYNC_CONTEXT", "Call this synchronous adapter in a worker thread outside the active event loop")
    if not result.success:
        raise AdapterError("EXTRACTION_FAILED", "Crawl4AI failed or the site's robots policy denied access")
    final_url = getattr(result, "redirected_url", None) or getattr(result, "url", None) or url
    validate_url(final_url, allow_local=False)
    if getattr(result, "status_code", None) in (401, 403, 429):
        raise AdapterError("ACCESS_BLOCKED", "The site refused access; do not retry with bypass settings")
    markdown = result.markdown
    text = markdown if isinstance(markdown, str) else getattr(markdown, "raw_markdown", "")
    if not text.strip():
        raise AdapterError("EMPTY_DOCUMENT", "No extractable text; JavaScript-dependent pages need separate approved collection")
    return _result("crawl4ai", {"url": url, "final_url": final_url, "markdown": text, "status_code": getattr(result, "status_code", None)},
                   "local_sdk", _version("crawl4ai"), ["Public pages only; JavaScript disabled. Extracted text does not establish eligibility."])


def _jobspy(payload, config):
    if config.get("terms_accepted") is not True:
        raise AdapterError("TERMS_REQUIRED", "Review the selected job platform's terms first")
    if payload.get("data_classification") != "public" and config.get("allow_external_private") is not True:
        raise AdapterError("PRIVACY_CONSENT_REQUIRED", "Search terms and location must be marked public or explicitly approved")
    sites = payload.get("site_name")
    if isinstance(sites, str):
        sites = [sites]
    allowed = {"indeed", "linkedin", "zip_recruiter", "glassdoor", "google", "bayt", "naukri", "bdjobs"}
    if not isinstance(sites, list) or not sites or any(site not in allowed for site in sites):
        raise AdapterError("INVALID_INPUT", "site_name must explicitly select supported upstream platforms")
    kwargs = {key: payload[key] for key in ("search_term", "google_search_term", "location", "is_remote", "job_type", "hours_old", "country_indeed") if key in payload}
    _text(kwargs.get("search_term") or kwargs.get("google_search_term"), "search_term")
    kwargs.update(site_name=sites, results_wanted=_integer(payload.get("results_wanted", 20), "results_wanted", 1, 100),
                  linkedin_fetch_description=False, description_format="markdown", verbose=0)
    frame = _sdk("jobspy").scrape_jobs(**kwargs)
    records = json.loads(frame.to_json(orient="records", date_format="iso"))
    # Never retain JobSpy's discovered contact columns.
    fields = {"id", "site", "job_url", "job_url_direct", "title", "company", "location", "date_posted", "job_type", "salary_source", "interval", "min_amount", "max_amount", "currency", "is_remote", "description"}
    clean = [{key: value for key, value in row.items() if key in fields} for row in records]
    return _result("jobspy", {"leads": clean, "verification_status": "unverified"}, "local_sdk",
                   _version("python-jobspy"), ["Discovery only. Verify the current official JD before inclusion in an application shortlist."])


def _classifier(payload, config):
    base = _service(payload, config)
    body = {key: payload[key] for key in ("text", "title", "description", "options") if key in payload}
    _text(body.get("text") or body.get("description"), "text or description")
    result = _request(base, "/v1/classify", body, config, classifier=True)
    if not isinstance(result.get("classification"), dict) or not isinstance(result.get("metadata"), dict):
        raise AdapterError("UPSTREAM_SCHEMA", "Classifier response requires classification and metadata")
    version = result["metadata"].get("classifier_version", "unverified")
    return _result("tabiya_livelihoods_classifier", result, "http_service", version,
                   ["ESCO-linked model suggestions require human review; they are not vacancy or candidate evidence."])


def _poll_esco(base, path, body, config):
    submitted = _request(base, path, body, config)
    job_id = submitted.get("job_id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise AdapterError("UPSTREAM_SCHEMA", "ESCO service did not return a safe job_id")
    polls = _integer(config.get("max_polls", 30), "max_polls", 1, 300)
    interval = _integer(config.get("poll_interval", 1), "poll_interval", 0, 30)
    for index in range(polls):
        if index:
            time.sleep(interval)
        status = _request(base, "/api/jobs/" + job_id, None, config)
        if status.get("status") == "done":
            if not isinstance(status.get("result"), dict):
                raise AdapterError("UPSTREAM_SCHEMA", "ESCO completed job has no result object")
            return status["result"]
        if status.get("status") == "error":
            raise AdapterError("UPSTREAM_FAILED", "ESCO model task failed; inspect private service logs")
        if status.get("status") not in ("queued", "running"):
            raise AdapterError("UPSTREAM_SCHEMA", "Unknown ESCO job status")
    raise AdapterError("UPSTREAM_TIMEOUT", "ESCO task remains pending; no completed result was fabricated")


def _esco_extractor(payload, config):
    base = _service(payload, config)
    # Provisioning and credentials remain on the server, except model names and
    # reviewed public configuration. Never send API keys in the persistent job body.
    if config.get("models_ready") is not True:
        raise AdapterError("MODEL_NOT_READY", "Provision the service's embeddings, taxonomy and LLM before calling it")
    title = _text(payload.get("title"), "title")
    provider = config.get("llm_provider", "ollama")
    if provider != "ollama":
        raise AdapterError("INVALID_CONFIG", "This persistent-job adapter supports a provisioned Ollama service only; cloud keys must not enter job bodies")
    body = {key: payload[key] for key in ("description", "qualifications") if key in payload}
    body.update(title=title, llm_provider=provider, llm_model=_text(config.get("llm_model"), "llm_model"),
                embedding_model=_text(config.get("embedding_model"), "embedding_model"), verbose=False)
    occupation = _poll_esco(base, "/api/occupation/match", body, config)
    matches = occupation.get("matches")
    if not isinstance(matches, list):
        raise AdapterError("UPSTREAM_SCHEMA", "Occupation result requires matches")
    if not matches:
        return _result("esco_skill_extractor", {"occupations": [], "skills": []}, "http_service",
                       warnings=["No occupation match; skill extraction was not inferred or fabricated."])
    top = _text(matches[0].get("occupation_name"), "matched occupation_name")
    skills = _poll_esco(base, "/api/skills/extract", dict(body, occupation=top), config)
    if not isinstance(skills.get("skills"), list):
        raise AdapterError("UPSTREAM_SCHEMA", "Skill result requires a skills list")
    return _result("esco_skill_extractor", {"occupations": matches, "skills": skills["skills"]}, "http_service",
                   warnings=["Occupation choice is the upstream first-ranked candidate, not a confirmed fact. Service persists submitted job text."])


def _compass(payload, config):
    base = _service(payload, config, always_private=True)
    session = _integer(payload.get("session_id"), "session_id", 0, 2147483647)
    result = _request(base, f"/conversations/{session}/messages?filter_pii=true",
                      {"user_input": _text(payload.get("user_input"), "user_input")}, config)
    if not isinstance(result.get("messages"), list) or "current_phase" not in result:
        raise AdapterError("UPSTREAM_SCHEMA", "Compass response requires messages and current_phase")
    return _result("tabiya_compass", result, "http_service",
                   warnings=["Requires an existing authorized Compass session. Conversation is stored upstream; discovered skills remain unconfirmed."])


def _resume_matcher(payload, config):
    base = _service(payload, config, always_private=True)
    body = {"resume_id": _text(payload.get("resume_id"), "resume_id"), "job_id": _text(payload.get("job_id"), "job_id")}
    if payload.get("prompt_id"):
        body["prompt_id"] = _text(payload["prompt_id"], "prompt_id")
    result = _request(base, "/api/v1/resumes/improve/preview", body, config)
    if not isinstance(result.get("data"), dict) or "resume_preview" not in result["data"]:
        raise AdapterError("UPSTREAM_SCHEMA", "Resume Matcher response requires data.resume_preview")
    return _result("resume_matcher", result, "http_service",
                   warnings=["Preview only; no resume is confirmed or saved by this adapter. Review every rewrite against candidate evidence; ATS scores are not hiring probabilities."])


_RUNNERS = {"docling": _docling, "crawl4ai": _crawl4ai, "jobspy": _jobspy,
            "tabiya_livelihoods_classifier": _classifier, "esco_skill_extractor": _esco_extractor,
            "tabiya_compass": _compass, "resume_matcher": _resume_matcher}


def run(component_id, payload, config):
    """Execute an explicit SDK/service call; errors contain no upstream private text."""
    if component_id not in _RUNNERS:
        raise AdapterError("UNKNOWN_COMPONENT", "Unknown service adapter")
    if not isinstance(payload, dict) or not isinstance(config, dict):
        raise AdapterError("INVALID_INPUT", "payload and config must be objects")
    try:
        return _RUNNERS[component_id](payload, config)
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError("UPSTREAM_FAILED", f"{component_id} failed ({type(exc).__name__}); inspect local logs without publishing private input") from None
