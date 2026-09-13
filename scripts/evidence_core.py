"""Shared provenance checks. Text truth still requires source review."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def canonical_url(value):
    """Drop known tracking only; preserve job identifiers and SPA fragments."""
    parts = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                       urlencode(sorted(query)), parts.fragment))


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def ledger_check(items):
    errors, ledger = [], {}
    if not isinstance(items, list) or not items:
        return {}, ['candidate_evidence must be a non-empty list']
    for i, item in enumerate(items):
        prefix = f'candidate_evidence row {i + 1}'
        if not isinstance(item, dict):
            errors.append(f'{prefix}: must be an object')
            continue
        for key in ('id', 'source_id', 'locator', 'text', 'state'):
            if not filled(item.get(key)):
                errors.append(f'{prefix}: {key} missing')
        if item.get('state') not in ('completed', 'ongoing', 'planned', 'unconfirmed'):
            errors.append(f'{prefix}: invalid state')
        if type(item.get('confirmed')) is not bool:
            errors.append(f'{prefix}: confirmed must be boolean')
        ident = item.get('id')
        if filled(ident):
            if ident in ledger:
                errors.append(f'{prefix}: duplicate evidence id')
            ledger[ident] = item
    return ledger, errors


def ref_errors(refs, ledger, factual=False, allow_empty=False):
    if not isinstance(refs, list) or (not refs and not allow_empty):
        return ['evidence_refs must be a non-empty list']
    errors = []
    for ref in refs:
        if not filled(ref) or ref not in ledger:
            errors.append('unknown candidate evidence reference')
        elif factual and (ledger[ref].get('confirmed') is not True or
                          ledger[ref].get('state') not in ('completed', 'ongoing')):
            errors.append('factual claim requires confirmed completed/ongoing evidence')
    return errors


def content_key(record):
    return digest(re.sub(r'\s+', ' ', record['source_text']).strip())
