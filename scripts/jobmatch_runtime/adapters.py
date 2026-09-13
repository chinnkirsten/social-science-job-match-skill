"""Single opt-in dispatch with provenance and an optional private cache."""
import importlib.util
import json
import time
from pathlib import Path
from . import adapters_data, adapters_services
from .common import AdapterError, utcnow

ROOT = Path(__file__).resolve().parents[2]


def registry():
    return {c['id']: c for c in json.loads((ROOT / 'integrations/open_source_stack.json').read_text(encoding='utf-8'))['components']}


def invoke(component_id, payload, config=None, cache=None):
    config = config or {}
    def reject_credentials(value):
        if isinstance(value, dict):
            if any(str(k).lower() in ('api_key', 'token', 'password', 'authorization', 'cookie') for k in value):
                raise AdapterError('inline_credential', 'Use credential environment-variable names, not inline secrets')
            for child in value.values(): reject_credentials(child)
        elif isinstance(value, list):
            for child in value: reject_credentials(child)
    reject_credentials(config)
    components = registry()
    if component_id not in components:
        raise AdapterError('unknown_component', 'Unknown integration component')
    if config.get('enabled') is not True:
        raise AdapterError('component_disabled', 'Enable this specific adapter explicitly before calling it')
    if not isinstance(payload, dict):
        raise AdapterError('invalid_input', 'Adapter payload must be an object')
    row = components[component_id]
    private = payload.get('data_classification') != 'public'
    if private and component_id in ('onet_database', 'esco', 'tabiya_open_dataset', 'jobspy') and config.get('allow_external_private') is not True:
        raise AdapterError('privacy_consent_required', 'Public data queries must contain public terms only, or have explicit remote-data consent')
    # Mutation-like preview/conversation APIs must never be replayed from cache.
    cacheable = component_id not in ('tabiya_compass', 'resume_matcher', 'esco_skill_extractor')
    versions = {k: v for k, v in config.items() if k not in ('api_key_env', 'token_env')}
    key = None
    if cache and cacheable:
        from .common import fingerprint
        material = dict(payload)
        if component_id == 'docling' and material.get('path'):
            import hashlib
            material['file_sha256'] = hashlib.sha256(Path(material['path']).read_bytes()).hexdigest()
        key = cache.key(component_id, row['pin'], {'payload': material, 'config': versions})
        hit = cache.get(key, private=private)
        if hit is not None:
            return {**hit, 'cache_hit': True}
    started = time.monotonic()
    module = adapters_data if component_id in adapters_data.PINS else adapters_services
    result = module.run(component_id, payload, config)
    if not isinstance(result, dict) or 'data' not in result:
        raise AdapterError('adapter_contract', 'Adapter did not produce its declared result')
    result.update(component_id=component_id, status='ok', invoked_at=utcnow(),
                  expected_pin=row['pin'], cache_hit=False,
                  elapsed_seconds=round(time.monotonic() - started, 3))
    if cache and key:
        cache.put(key, result, private=private, ttl=min(config.get('cache_seconds', 86400), 7 * 86400))
    return result


def doctor(config=None):
    config = config or {}
    rows = []
    sdks = {'docling': 'docling', 'crawl4ai': 'crawl4ai', 'jobspy': 'jobspy'}
    for ident, row in registry().items():
        cfg = config.get('adapters', {}).get(ident, {})
        if ident in sdks:
            ready = importlib.util.find_spec(sdks[ident]) is not None
            prerequisite = 'installed SDK; browser assets also required for Crawl4AI'
        elif ident in ('melo_benchmark', 'tgre_classification'):
            ready = bool(cfg.get('checkout')) and Path(cfg['checkout']).is_dir()
            prerequisite = 'clean pinned upstream checkout and evaluation dependencies; MELO trec_eval needs Linux'
        elif ident in ('onet_database', 'esco', 'tabiya_open_dataset'):
            ready = True
            prerequisite = 'public network access; actual request not tested by doctor'
        else:
            ready = bool(cfg.get('service_url')) and cfg.get('service_ready') is True
            prerequisite = 'configured, provisioned upstream service with models/session/data'
        rows.append({'id': ident, 'implementation': 'adapter_implemented', 'enabled': cfg.get('enabled') is True,
                     'configuration_present': ready, 'prerequisite': prerequisite, 'live_verified': False})
    return {'runtime': '0.1.0', 'components': rows, 'note': 'Doctor inspects configuration; it does not certify service or model readiness.'}
