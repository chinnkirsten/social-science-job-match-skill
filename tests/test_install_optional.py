"""Installer unit tests: all installation processes are mocked; no packages installed."""
import contextlib
import importlib.metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install_optional as installer


READY = {"python_version": "3.11.0", "python_compatible": True, "pip_available": True,
         "git_available": True, "isolated_environment": True}


class OptionalInstallerTest(unittest.TestCase):
    def test_docling_installs_model_free_format_dependencies(self):
        result = installer.install("docling")
        target = result["command"][-1]
        self.assertTrue(target.startswith("docling-slim[convert-core,format-pdf,format-docx,format-web] @ git+"))
        self.assertIn("@5ea6490ffdc57b2fd7de5cc436f2d0a22f2214d4", target)
        self.assertNotIn("models-local", target)
        self.assertNotIn("feat-ocr", target)

    def test_default_cli_is_dry_run(self):
        with patch.object(installer.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(installer.main(["jobspy"]), 0)
        run.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["installed"])
        self.assertEqual(result["command"][0], "<current-python>")
        self.assertIn("@fda080a373e8226f3fd60635323f5da9af9892b1", result["command"][-1])

    def test_only_three_sdks_and_explicit_component(self):
        for component in ("esco", "onet_database", "tabiya_compass", "melo_benchmark", "tgre_classification"):
            with patch.object(installer.subprocess, "run") as run:
                result = installer.install(component, execute=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "manual_provisioning_required")
            run.assert_not_called()
        self.assertEqual(installer.install("all")["status"], "invalid_registry")

    def test_reject_modified_source_or_unpinned_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.json"
            original = installer._record("jobspy", installer.REGISTRY)
            for change in ({"source": "https://unreviewed.invalid/repo"}, {"pin": "main"}, {"pin_type": "release"}):
                registry.write_text(json.dumps({"components": [{**original, **change}]}), encoding="utf-8")
                with patch.object(installer.subprocess, "run") as run:
                    result = installer.install("jobspy", execute=True, registry_path=registry)
                self.assertEqual(result["status"], "invalid_registry")
                run.assert_not_called()

    def test_python_requirement_check(self):
        with patch.object(installer.sys, "version_info", (3, 9, 0)):
            self.assertFalse(installer.requirements("docling")["python_compatible"])
        with patch.object(installer.sys, "version_info", (4, 0, 0)):
            self.assertFalse(installer.requirements("jobspy")["python_compatible"])

    def test_execute_requires_ready_isolated_environment(self):
        for key in ("python_compatible", "pip_available", "git_available", "isolated_environment"):
            with patch.object(installer, "requirements", return_value={**READY, key: False}), patch.object(installer.subprocess, "run") as run:
                result = installer.install("jobspy", execute=True)
            self.assertEqual(result["status"], "requirements_not_met")
            run.assert_not_called()

    def test_success_requires_actual_package_commit_metadata_and_pip_check(self):
        provenance = {"distribution": "python-jobspy", "version": "1.1.82", "commit_verified": True}
        success = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(installer, "requirements", return_value=READY), patch.object(installer, "_installed_provenance", return_value=provenance), patch.object(installer.subprocess, "run", return_value=success) as run:
            result = installer.install("jobspy", execute=True)
        self.assertEqual(result["status"], "installed_verified")
        self.assertFalse(result["effectiveness_evaluated"])
        self.assertEqual(run.call_count, 2)
        first = run.call_args_list[0]
        self.assertEqual(first.args[0][:4], [sys.executable, "-m", "pip", "install"])
        self.assertNotIn("shell", first.kwargs)
        self.assertEqual(first.kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(first.kwargs["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"], "1")
        self.assertEqual(run.call_args.args[0][1:4], ["-m", "pip", "check"])

    def test_pip_zero_without_provenance_is_not_success(self):
        with patch.object(installer, "requirements", return_value=READY), patch.object(installer, "_installed_provenance", return_value=None), patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            result = installer.install("docling", execute=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "installed_unverified")

    def test_failures_and_timeout_do_not_leak_subprocess_output(self):
        with patch.object(installer, "requirements", return_value=READY), patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "secret", "private path")):
            result = installer.install("docling", execute=True)
        self.assertEqual(result["status"], "install_failed")
        self.assertNotIn("private path", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))
        with patch.object(installer, "requirements", return_value=READY), patch.object(installer.subprocess, "run", side_effect=subprocess.TimeoutExpired("pip", 1)):
            result = installer.install("docling", execute=True)
        self.assertEqual(result["status"], "install_timeout")

    def test_pip_check_conflict_preserves_partial_status(self):
        with patch.object(installer, "requirements", return_value=READY), patch.object(installer, "_installed_provenance", return_value={"commit_verified": True}), patch.object(installer.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "")]):
            result = installer.install("jobspy", execute=True)
        self.assertTrue(result["installed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dependency_conflicts")

    def test_pep610_provenance(self):
        record = installer._record("docling", installer.REGISTRY)
        metadata = Mock(version="2.126.0")
        direct = {"url": record["source"], "vcs_info": {"vcs": "git", "commit_id": record["pin"]}}
        metadata.read_text.return_value = json.dumps(direct)
        with patch.object(installer.importlib.metadata, "distribution", return_value=metadata) as distribution:
            result = installer._installed_provenance("docling", record)
        self.assertTrue(result["commit_verified"])
        distribution.assert_called_once_with("docling-slim")
        metadata.read_text.return_value = json.dumps({**direct, "vcs_info": {"vcs": "git", "commit_id": "wrong"}})
        with patch.object(installer.importlib.metadata, "distribution", return_value=metadata):
            self.assertIsNone(installer._installed_provenance("docling", record))

    def test_crawl_browser_is_separate_and_invalid_timeout_does_not_install(self):
        result = installer.install("crawl4ai")
        self.assertIn("separately", result["manual_next_step"])
        with patch.object(installer.subprocess, "run") as run:
            result = installer.install("crawl4ai", execute=True, timeout=0)
        self.assertEqual(result["status"], "invalid_timeout")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
