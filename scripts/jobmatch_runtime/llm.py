"""Real model transports. No rule engine or fixture fallback in production."""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from .common import AdapterError, atomic_json, http_json


class ModelClient:
    def __init__(self, config, *, allow_remote_candidate_data=False):
        self.config = config
        self.allow_remote_candidate_data = allow_remote_candidate_data
        self.calls = []

    def _settings(self, role, private=False):
        cfg = {**self.config, **self.config.get('roles', {}).get(role, {})}
        provider = cfg.get('provider', 'openai_compatible')
        base = cfg.get('base_url', '')
        local = urlsplit(base).hostname in ('localhost', '127.0.0.1', '::1')
        # A CLI can itself call cloud models; it is not automatically local inference.
        private_safe = provider == 'openai_compatible' and local and cfg.get('service_egress') == 'local_only'
        if private and not private_safe and not self.allow_remote_candidate_data:
            raise AdapterError('privacy_consent_required', 'Candidate data may leave this machine; explicit opt-in required')
        return cfg

    def preflight(self):
        for role in ('SourceScout', 'EvidenceMapper', 'Auditor'):
            cfg = self._settings(role, private=role != 'SourceScout')
            provider = cfg.get('provider', 'openai_compatible')
            if provider == 'codex_cli':
                if not shutil.which(cfg.get('executable', 'codex')):
                    raise AdapterError('missing_dependency', 'Codex CLI unavailable')
            elif provider == 'openai_compatible':
                if not cfg.get('base_url') or not cfg.get('model'):
                    raise AdapterError('missing_config', 'Model base_url and model are required')
                if urlsplit(cfg['base_url']).hostname not in ('localhost', '127.0.0.1', '::1') and not os.environ.get(cfg.get('api_key_env', 'JOB_MATCH_API_KEY')):
                    raise AdapterError('missing_credentials', 'Model API-key environment variable is unset')
            else:
                raise AdapterError('invalid_config', 'Unsupported model provider')

    def call(self, role, instruction, payload, *, private=False):
        cfg = self._settings(role, private)
        provider = cfg.get('provider', 'openai_compatible')
        attempts = cfg.get('max_attempts', 2)
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise AdapterError('invalid_config', 'max_attempts must be 1 to 3')
        started = time.monotonic()
        last = None
        for attempt in range(attempts):
            try:
                if provider == 'codex_cli':
                    content = self._codex(cfg, instruction, payload)
                elif provider == 'openai_compatible':
                    content = self._chat(cfg, instruction, payload)
                else:
                    raise AdapterError('invalid_config', 'Unsupported model provider')
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError()
                self.calls.append({'role': role, 'provider': provider, 'model': cfg.get('model', 'cli_default'),
                                   'attempts': attempt + 1, 'elapsed_seconds': round(time.monotonic() - started, 3),
                                   'execution': 'real_model_call'})
                return parsed
            except (ValueError, UnicodeError):
                last = AdapterError('invalid_model_json', 'Model must return one JSON object, without Markdown fences')
            except AdapterError as exc:
                last = exc
                if exc.code not in ('network_error', 'http_429', 'http_500', 'http_502', 'http_503'):
                    raise
            if attempt + 1 < attempts:
                time.sleep(min(attempt + 1, 2))
        raise last

    @staticmethod
    def _chat(cfg, instruction, payload):
        if not cfg.get('base_url') or not cfg.get('model'):
            raise AdapterError('missing_config', 'Model base_url and model are required')
        headers = {}
        env_name = cfg.get('api_key_env', 'JOB_MATCH_API_KEY')
        key = os.environ.get(env_name)
        local = urlsplit(cfg['base_url']).hostname in ('localhost', '127.0.0.1', '::1')
        if key:
            headers['Authorization'] = 'Bearer ' + key
        elif not local:
            raise AdapterError('missing_credentials', 'Configured model API-key environment variable is unset')
        response = http_json(cfg['base_url'].rstrip('/') + '/chat/completions', {
            'model': cfg['model'], 'messages': [{'role': 'system', 'content': instruction},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'response_format': {'type': 'json_object'}, 'max_tokens': cfg.get('max_output_tokens', 5000),
        }, headers=headers, timeout=cfg.get('timeout_seconds', 90), allow_local=local)
        try:
            return response['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            raise AdapterError('invalid_response', 'Model API returned no completion text') from None

    @staticmethod
    def _codex(cfg, instruction, payload):
        executable = shutil.which(cfg.get('executable', 'codex'))
        if not executable:
            raise AdapterError('missing_dependency', 'Codex CLI not installed or not found on PATH')
        with tempfile.TemporaryDirectory(prefix='jobmatch-model-') as directory:
            output = Path(directory) / 'response.json'
            args = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--skip-git-repo-check',
                    '--sandbox', 'read-only', '--color', 'never', '-C', directory,
                    '--output-last-message', str(output)]
            if cfg.get('model'):
                args += ['--model', cfg['model']]
            args += ['-']
            prompt = instruction + '\nDo not use tools or read files. Return only JSON.\nINPUT DATA:\n' + json.dumps(payload, ensure_ascii=False)
            try:
                result = subprocess.run(args, input=prompt, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=cfg.get('timeout_seconds', 120),
                                        cwd=directory, shell=False)
            except (OSError, subprocess.TimeoutExpired):
                raise AdapterError('model_process_error', 'Model process unavailable or timed out') from None
            if result.returncode or not output.is_file():
                raise AdapterError('model_process_error', 'Model process failed; check CLI authentication separately')
            if output.stat().st_size > 2_000_000:
                raise AdapterError('response_too_large', 'Model output exceeds limit')
            return output.read_text(encoding='utf-8')
