"""Validate the versioned open-source integration registry; never installs code."""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


STATUSES = {'baseline', 'planned_adapter', 'optional_adapter', 'experimental', 'pattern_only',
            'discovery_only', 'evaluation_only'}
PIN_TYPES = {'release', 'commit'}
FIELDS = ('id', 'name', 'kind', 'source', 'pin_type', 'pin', 'license', 'status',
          'default_enabled', 'role', 'input', 'output', 'trust_boundary', 'integration_state')


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def web_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname)
    except (TypeError, ValueError):
        return False


def validate(data):
    errors, seen = [], set()
    if not isinstance(data, dict):
        return {'ok': False, 'components': 0, 'errors': ['registry must be an object']}
    if not filled(data.get('schema_version')):
        errors.append('schema_version missing')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(data.get('verified_at', ''))):
        errors.append('verified_at must be YYYY-MM-DD')
    components = data.get('components')
    if not isinstance(components, list) or not components:
        return {'ok': False, 'components': 0,
                'errors': errors + ['components must be a non-empty list']}
    for index, component in enumerate(components):
        prefix = f'row {index + 1}'
        if not isinstance(component, dict):
            errors.append(f'{prefix}: component must be an object')
            continue
        for field in FIELDS:
            if field == 'default_enabled':
                if type(component.get(field)) is not bool:
                    errors.append(f'{prefix}: default_enabled must be boolean')
            elif not filled(component.get(field)):
                errors.append(f'{prefix}: {field} missing')
        ident = component.get('id')
        if filled(ident):
            if ident in seen:
                errors.append(f'{prefix}: duplicate id {ident}')
            seen.add(ident)
            if not re.fullmatch(r'[a-z0-9_]+', ident):
                errors.append(f'{prefix}: id must be snake_case')
        if not web_url(component.get('source')):
            errors.append(f'{prefix}: source must be an HTTPS URL')
        if component.get('status') not in STATUSES:
            errors.append(f'{prefix}: invalid status')
        state = component.get('integration_state')
        if state not in ('documented_only', 'adapter_implemented'):
            errors.append(f'{prefix}: invalid integration_state')
        if state == 'adapter_implemented':
            root = Path(__file__).resolve().parents[1]
            for field in ('adapter_entrypoint', 'contract_test', 'verification_record'):
                value = component.get(field)
                if not filled(value):
                    errors.append(f'{prefix}: {field} missing')
                    continue
                path = (root / value).resolve()
                if root not in path.parents or not path.is_file():
                    errors.append(f'{prefix}: {field} must identify an existing in-repository file')
        pin_type = component.get('pin_type')
        if pin_type not in PIN_TYPES:
            errors.append(f'{prefix}: invalid pin_type')
        if pin_type == 'commit' and not re.fullmatch(r'[0-9a-f]{40}', str(component.get('pin', ''))):
            errors.append(f'{prefix}: commit pin must be a 40-character lowercase SHA')
        if component.get('default_enabled') is True:
            errors.append(f'{prefix}: optional components require explicit opt-in; defaults must remain false')
    return {'ok': not errors, 'components': len(components), 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registry')
    args = parser.parse_args()
    try:
        with open(args.registry, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2
    result = validate(data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
