"""Build reproducible corpus evidence metrics from normalized job records."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit
from evidence_core import canonical_url, digest, content_key, ledger_check


ALLOWED_MODES = {'internship', 'campus_full_time', 'experienced_full_time'}
SOURCE_TIERS = {'employer_official', 'official_ats', 'employer_verified_platform',
                'official_repost', 'aggregator'}
RELIABLE_TIERS = {'employer_official', 'official_ats', 'employer_verified_platform'}


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return parsed


def web_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname)
    except (TypeError, ValueError):
        return False


def build(data, now=None):
    now = now or datetime.now(timezone.utc)
    errors = []
    if not isinstance(data, dict):
        return {'ok': False, 'errors': ['input must be an object']}
    mode = data.get('employment_mode')
    if mode not in ALLOWED_MODES:
        errors.append('employment_mode invalid')
    for field in ('method_version', 'geography'):
        if not filled(data.get(field)):
            errors.append(f'{field} missing')
    ledger, ledger_errors = ledger_check(data.get('candidate_evidence'))
    errors.extend(ledger_errors)
    candidate_count = len(ledger)
    locations = data.get('locations')
    if not isinstance(locations, list) or not locations or not all(filled(x) for x in locations):
        errors.append('locations must be a non-empty list of normalized cities')
        locations = []
    pool_count = data.get('candidate_pool_count')
    if type(pool_count) is not int or pool_count < 1:
        errors.append('candidate_pool_count must be a positive integer')
    try:
        collected = timestamp(data.get('collected_at', ''))
        if not timedelta(0) <= now - collected <= timedelta(days=7):
            errors.append('collected_at is future or older than 7 days')
    except (ValueError, TypeError, AttributeError):
        errors.append('collected_at must be ISO timestamp with timezone')
        collected = now
    taxonomies = data.get('taxonomies', [])
    if not isinstance(taxonomies, list):
        errors.append('taxonomies must be a list')
        taxonomies = []
    else:
        for index, taxonomy in enumerate(taxonomies):
            if (not isinstance(taxonomy, dict) or
                    any(not filled(taxonomy.get(k)) for k in ('name', 'version', 'use')) or
                    not web_url(taxonomy.get('url'))):
                errors.append(f'taxonomies row {index + 1} incomplete')
    records = data.get('records')
    if not isinstance(records, list):
        errors.append('records must be a list')
        records = []
    comparable, seen = [], set()
    seen_urls, seen_content, seen_postings = set(), set(), set()
    excluded_count = 0
    for index, record in enumerate(records):
        prefix = f'record {index + 1}'
        if not isinstance(record, dict):
            errors.append(f'{prefix}: must be an object')
            continue
        required = ('corpus_id', 'company_key', 'company', 'title', 'employment_mode',
                    'geography', 'source_tier', 'jd_url', 'captured_at', 'status')
        for field in required:
            if not filled(record.get(field)):
                errors.append(f'{prefix}: {field} missing')
        corpus_id = record.get('corpus_id')
        if filled(corpus_id):
            if corpus_id in seen:
                errors.append(f'{prefix}: duplicate corpus_id')
            seen.add(corpus_id)
        if record.get('employment_mode') != mode:
            errors.append(f'{prefix}: employment_mode mismatch')
        if record.get('source_tier') not in SOURCE_TIERS:
            errors.append(f'{prefix}: invalid source_tier')
        if not web_url(record.get('jd_url')):
            errors.append(f'{prefix}: invalid jd_url')
        try:
            captured = timestamp(record.get('captured_at', ''))
            if captured > collected or captured > now:
                errors.append(f'{prefix}: captured_at is after collection time')
            if record.get('comparable') is True and now - captured > timedelta(days=7):
                errors.append(f'{prefix}: comparable evidence older than 7 days')
        except (ValueError, TypeError, AttributeError):
            errors.append(f'{prefix}: captured_at must include timezone')
        if record.get('status') not in ('open', 'closed', 'unknown'):
            errors.append(f'{prefix}: invalid status')
        if type(record.get('full_jd')) is not bool or type(record.get('comparable')) is not bool:
            errors.append(f'{prefix}: full_jd and comparable must be boolean')
        requirements = record.get('requirements', [])
        if not isinstance(requirements, list):
            errors.append(f'{prefix}: requirements must be a list')
            requirements = []
        for req in requirements:
            if (not isinstance(req, dict) or not filled(req.get('text')) or
                    type(req.get('required')) is not bool or
                    not isinstance(req.get('normalized_skills'), list) or
                    not all(filled(s) for s in req.get('normalized_skills', []))):
                errors.append(f'{prefix}: malformed requirement')
        if record.get('full_jd') is True and record.get('comparable') is True:
            if record.get('status') != 'open':
                errors.append(f'{prefix}: current comparable corpus requires open status')
            cities = record.get('locations')
            if (not isinstance(cities, list) or not cities or
                    not all(filled(x) for x in cities) or
                    not any(x in locations for x in cities)):
                errors.append(f'{prefix}: locations outside report scope or missing')
            if not requirements:
                errors.append(f'{prefix}: full JD requires requirements')
            duties = record.get('responsibilities')
            if not isinstance(duties, list) or not duties or not all(filled(x) for x in duties):
                errors.append(f'{prefix}: full JD requires responsibilities')
            source = record.get('source_text')
            if not filled(source):
                errors.append(f'{prefix}: source_text missing')
            else:
                if record.get('source_sha256') != digest(source):
                    errors.append(f'{prefix}: source_sha256 mismatch')
                key = content_key(record)
                if key in seen_content:
                    errors.append(f'{prefix}: duplicate source content; review syndication')
                seen_content.add(key)
                for req in requirements:
                    if isinstance(req, dict):
                        excerpt = req.get('source_excerpt')
                        if not filled(excerpt) or excerpt not in source:
                            errors.append(f'{prefix}: requirement excerpt not in source_text')
            if web_url(record.get('jd_url')):
                key = canonical_url(record['jd_url'])
                if key in seen_urls:
                    errors.append(f'{prefix}: duplicate canonical JD URL')
                seen_urls.add(key)
            if filled(record.get('employer_job_id')) and filled(record.get('company_key')):
                key = (record['company_key'].casefold(), record['employer_job_id'])
                if key in seen_postings:
                    errors.append(f'{prefix}: duplicate employer job ID')
                seen_postings.add(key)
            comparable.append(record)
        else:
            if not filled(record.get('excluded_reason')):
                errors.append(f'{prefix}: excluded/incomplete record needs excluded_reason')
            excluded_count += 1
    if type(pool_count) is int and pool_count < len(records):
        errors.append('candidate_pool_count cannot be smaller than records')
    if errors:
        return {'ok': False, 'errors': errors}

    companies = {r['company_key'].strip().casefold() for r in comparable}
    source_counts = Counter()
    required_skills, preferred_skills = Counter(), Counter()
    reliable_count = 0
    for record in comparable:
        parsed = urlsplit(record['jd_url'])
        origin = f'{parsed.scheme}://{parsed.hostname}'
        source_counts[(origin, record['source_tier'])] += 1
        if record['source_tier'] in RELIABLE_TIERS:
            reliable_count += 1
        seen_required, seen_preferred = set(), set()
        for requirement in record.get('requirements', []):
            if not isinstance(requirement, dict):
                continue
            target = seen_required if requirement.get('required') is True else seen_preferred
            skills = requirement.get('normalized_skills', [])
            if isinstance(skills, list):
                target.update(skill.strip() for skill in skills if filled(skill))
        required_skills.update(seen_required)
        preferred_skills.update(seen_preferred)

    corpus_count = len(comparable)
    reliable_ratio = reliable_count / corpus_count if corpus_count else 0.0
    claim = 'sample_observation' if corpus_count else 'none'
    market_sources = [
        {'name': urlsplit(origin).hostname, 'source_tier': tier,
         'url': origin, 'records': count}
        for (origin, tier), count in sorted(source_counts.items())
    ]
    evidence_basis = {
        'method_version': data['method_version'],
        'candidate_evidence_count': candidate_count,
        'market_corpus_count': corpus_count,
        'market_employer_count': len(companies),
        'corpus_as_of': min((r['captured_at'] for r in comparable),
                            key=timestamp, default=data['collected_at']),
        'trend_claim_level': claim,
        'market_sources': market_sources,
        'taxonomies': taxonomies,
    }
    summary = {
        'employment_mode': mode,
        'geography': data['geography'],
        'candidate_pool_count': pool_count,
        'normalized_record_count': len(records),
        'comparable_full_jd_count': corpus_count,
        'different_employer_count': len(companies),
        'reliable_source_count': reliable_count,
        'reliable_source_ratio': round(reliable_ratio, 4),
        'excluded_or_incomplete_count': excluded_count,
        'top_required_skills': required_skills.most_common(15),
        'top_preferred_skills': preferred_skills.most_common(15),
        'limitations': [
            'Corpus frequency does not override any specific JD requirement.',
            'Taxonomy mappings are normalization aids, not candidate evidence.',
            'The sample is not claimed to represent the entire labor market.',
        ],
    }
    return {'ok': True, 'evidence_basis': evidence_basis, 'corpus_summary': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--output')
    parser.add_argument('--as-of', type=timestamp, help='Test clock only')
    args = parser.parse_args()
    try:
        with open(args.input, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2
    result = build(data, args.as_of)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output and result.get('ok'):
        if Path(args.output).resolve() == Path(args.input).resolve():
            print('output must not overwrite input', file=sys.stderr)
            return 2
        try:
            with open(args.output, 'x', encoding='utf-8') as handle:
                handle.write(rendered)
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print(rendered, end='')
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
