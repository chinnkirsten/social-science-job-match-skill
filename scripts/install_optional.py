"""Plan or explicitly install one reviewed optional SDK; never provision models/services."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


REGISTRY = Path(__file__).resolve().parents[1] / "integrations/open_source_stack.json"
INSTALLABLE = {
    "docling": {"source": "https://github.com/docling-project/docling", "distribution": "docling-slim", "python_max": (4, 0)},
    "crawl4ai": {"source": "https://github.com/unclecode/crawl4ai", "distribution": "Crawl4AI", "python_max": None},
    "jobspy": {"source": "https://github.com/speedyapply/JobSpy", "distribution": "python-jobspy", "python_max": (4, 0)},
}


def requirements(component):
    """Check installation prerequisites without importing or starting the optional SDK."""
    maximum = INSTALLABLE[component]["python_max"]
    version = tuple(sys.version_info[:2])
    return {
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_compatible": version >= (3, 10) and (maximum is None or version < maximum),
        "pip_available": importlib.util.find_spec("pip") is not None,
        "git_available": shutil.which("git") is not None,
        "isolated_environment": sys.prefix != sys.base_prefix,
    }


def _record(component, registry_path):
    try:
        registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        components = registry["components"]
        matches = [item for item in components if isinstance(item, dict) and item.get("id") == component]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Registry could not be read as a component registry") from None
    if len(matches) != 1:
        raise ValueError("Component must occur exactly once in the registry")
    record = matches[0]
    if component in INSTALLABLE and (record.get("source") != INSTALLABLE[component]["source"]
            or record.get("pin_type") != "commit"
            or not re.fullmatch(r"[0-9a-f]{40}", str(record.get("pin", "")))):
        raise ValueError("Installable source must be the reviewed HTTPS repository and a full commit SHA")
    return record


def _installed_provenance(component, record):
    """Read PEP 610 metadata; an installed package version alone does not verify its commit."""
    try:
        distribution = importlib.metadata.distribution(INSTALLABLE[component]["distribution"])
        version = distribution.version
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9.!+_-]{1,100}", version):
            return None
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        vcs = direct.get("vcs_info", {})
        source = direct.get("url", "")
        if not isinstance(source, str):
            return None
        if source.endswith(".git"):
            source = source[:-4]
        if source != record["source"] or vcs.get("vcs") != "git" or vcs.get("commit_id") != record["pin"]:
            return None
        return {"distribution": INSTALLABLE[component]["distribution"], "version": version,
                "source": record["source"], "commit_id": vcs["commit_id"], "commit_verified": True}
    except (importlib.metadata.PackageNotFoundError, ValueError, TypeError, AttributeError):
        return None


def install(component, *, execute=False, registry_path=REGISTRY, timeout=900):
    """Return a safe JSON-compatible plan/result. Installation is always opt-in."""
    base = {"component_id": component, "executed": False, "installed": False}
    try:
        record = _record(component, registry_path)
    except ValueError as exc:
        return {**base, "ok": False, "status": "invalid_registry", "message": str(exc)}
    if component not in INSTALLABLE:
        return {**base, "ok": False, "status": "manual_provisioning_required",
                "message": "This data/service/evaluation component is not installed with pip by this helper. Follow its upstream deployment or versioned-data instructions."}
    checks = requirements(component)
    target = "git+" + record["source"] + "@" + record["pin"]
    arguments = ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", target]
    result = {**base, "requirements": checks, "source": record["source"], "source_pin": record["pin"],
              "command": ["<current-python>"] + arguments,
              "warnings": [
                  "Installing reviewed source and verifying its commit does not establish model quality or report accuracy.",
                  "Transitive dependencies are resolved by pip; this helper does not provide a fully locked dependency environment.",
                  "No model-download, browser-install, server-start, or account-creation commands are executed.",
              ]}
    if component == "crawl4ai":
        result["manual_next_step"] = "After reviewing browser requirements, separately provision the browser using upstream Crawl4AI instructions; this helper does not run crawl4ai-setup or Playwright install."
    if not execute:
        return {**result, "ok": True, "status": "dry_run", "ready_to_install": all(value for key, value in checks.items() if key != "python_version")}
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        return {**result, "ok": False, "status": "invalid_timeout", "message": "timeout must be from 1 to 3600 seconds"}
    if not all(value for key, value in checks.items() if key != "python_version"):
        return {**result, "ok": False, "status": "requirements_not_met",
                "message": "Use a compatible isolated virtual environment with pip and git; the helper will not modify a global Python environment."}
    env = dict(os.environ)
    env.update({"PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"})
    try:
        process = subprocess.run([sys.executable] + arguments, capture_output=True, text=True,
                                 timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        return {**result, "executed": True, "ok": False, "status": "install_timeout",
                "message": "pip timed out; inspect the isolated environment before retrying. Partial installation is possible."}
    except OSError:
        return {**result, "ok": False, "status": "installer_unavailable", "message": "Could not start the current Python pip process."}
    if process.returncode != 0:
        return {**result, "executed": True, "ok": False, "status": "install_failed", "returncode": process.returncode,
                "message": "pip failed; no successful installation is claimed. Inspect the isolated environment locally."}
    provenance = _installed_provenance(component, record)
    if provenance is None:
        return {**result, "executed": True, "ok": False, "status": "installed_unverified",
                "message": "pip exited successfully but installed package metadata does not verify the expected repository and commit."}
    try:
        check = subprocess.run([sys.executable, "-m", "pip", "check", "--disable-pip-version-check"],
                               capture_output=True, text=True, timeout=min(timeout, 120), env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {**result, "executed": True, "installed": True, "ok": False, "status": "dependency_check_failed",
                "installed_provenance": provenance, "message": "Source installation verified; dependency consistency could not be checked."}
    if check.returncode:
        return {**result, "executed": True, "installed": True, "ok": False, "status": "dependency_conflicts",
                "installed_provenance": provenance, "message": "Source installation verified but pip check reports dependency conflicts."}
    return {**result, "executed": True, "installed": True, "ok": True, "status": "installed_verified",
            "installed_provenance": provenance, "dependency_check": "passed", "effectiveness_evaluated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component_id", help="One explicit component ID from the integration registry")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show the plan without installing (default)")
    mode.add_argument("--execute", action="store_true", help="Install into the current isolated Python environment")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    result = install(args.component_id, execute=args.execute, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
