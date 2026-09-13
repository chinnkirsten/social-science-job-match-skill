"""Read-only structural gate; never certifies truth of JD/resume evidence."""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from evidence_core import ledger_check, ref_errors, canonical_url
from build_evidence_basis import build as build_corpus


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return parsed


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def url(value):
    try:
        parts = urlsplit(value)
        return parts.scheme in ('http', 'https') and bool(parts.hostname)
    except (ValueError, TypeError):
        return False


def validate(data, now=None):
    now = now or datetime.now(timezone.utc)
    errors, companies, seen = [], set(), set()
    if not isinstance(data, dict):
        return {'ok': False, 'companies': 0, 'errors': ['input must be an object']}
    target = data.get('target_companies', 20)
    if type(target) is not int or target < 1:
        errors.append('target_companies must be a positive integer')
        target = 20
    allowed_modes = ('internship', 'campus_full_time', 'experienced_full_time')
    employment_mode = data.get('employment_mode')
    if employment_mode not in allowed_modes:
        errors.append('employment_mode must be internship, campus_full_time, or experienced_full_time')
    corpus = data.get('corpus')
    corpus_result = build_corpus(corpus, now)
    ledger, ledger_errors = ledger_check(corpus.get('candidate_evidence') if isinstance(corpus, dict) else None)
    errors.extend(ledger_errors)
    if not corpus_result['ok']:
        errors.extend('corpus: ' + error for error in corpus_result['errors'])
    elif data.get('evidence_basis') != corpus_result['evidence_basis']:
        errors.append('evidence_basis must equal metrics rebuilt from corpus')
    if isinstance(corpus, dict) and corpus.get('employment_mode') != employment_mode:
        errors.append('corpus employment_mode mismatch')
    records = corpus.get('records', []) if isinstance(corpus, dict) else []
    corpus_rows = {r['corpus_id']: r for r in records
                   if isinstance(r, dict) and filled(r.get('corpus_id'))} if isinstance(records, list) else {}
    evidence = data.get('evidence_basis')
    source_tiers = ('employer_official', 'official_ats', 'employer_verified_platform',
                    'official_repost', 'aggregator')
    if not isinstance(evidence, dict):
        errors.append('evidence_basis must be an object')
        evidence = {}
    if not filled(evidence.get('method_version')):
        errors.append('evidence_basis method_version missing')
    for field in ('candidate_evidence_count', 'market_corpus_count', 'market_employer_count'):
        if type(evidence.get(field)) is not int or evidence.get(field) < 1:
            errors.append(f'evidence_basis {field} must be a positive integer')
    claim = evidence.get('trend_claim_level')
    if claim not in ('none', 'sample_observation'):
        errors.append('single-batch corpus supports sample_observation or none, not market_pattern')
    try:
        corpus_age = now - timestamp(evidence.get('corpus_as_of', ''))
        if corpus_age < timedelta(0) or corpus_age > timedelta(days=7):
            errors.append('evidence_basis corpus_as_of is future or older than 7 days')
    except (ValueError, TypeError, AttributeError):
        errors.append('evidence_basis corpus_as_of must be ISO timestamp with timezone')
    market_sources = evidence.get('market_sources')
    source_record_total = 0
    if not isinstance(market_sources, list) or not market_sources:
        errors.append('evidence_basis market_sources must be a non-empty list')
    else:
        for index, source in enumerate(market_sources):
            label = f'evidence_basis market_sources row {index + 1}'
            if not isinstance(source, dict):
                errors.append(f'{label} must be an object')
                continue
            if not filled(source.get('name')):
                errors.append(f'{label} name missing')
            if source.get('source_tier') not in source_tiers:
                errors.append(f'{label} source_tier invalid')
            if not url(source.get('url')):
                errors.append(f'{label} URL missing or invalid')
            if type(source.get('records')) is not int or source.get('records') < 1:
                errors.append(f'{label} records must be a positive integer')
            else:
                source_record_total += source['records']
        if type(evidence.get('market_corpus_count')) is int and source_record_total != evidence['market_corpus_count']:
            errors.append('evidence_basis market source records must equal market_corpus_count')
    taxonomies = evidence.get('taxonomies', [])
    if not isinstance(taxonomies, list):
        errors.append('evidence_basis taxonomies must be a list')
    else:
        for index, taxonomy in enumerate(taxonomies):
            label = f'evidence_basis taxonomies row {index + 1}'
            if not isinstance(taxonomy, dict):
                errors.append(f'{label} must be an object')
                continue
            if any(not filled(taxonomy.get(field)) for field in ('name', 'version', 'use')):
                errors.append(f'{label} name, version, and use are required')
            if not url(taxonomy.get('url')):
                errors.append(f'{label} URL missing or invalid')
    jobs = data.get('jobs')
    if not isinstance(jobs, list):
        jobs = []
        errors.append('jobs must be a list')
    for index, job in enumerate(jobs):
        prefix = f'row {index + 1}'
        if not isinstance(job, dict):
            errors.append(f'{prefix}: must be an object')
            continue
        start = len(errors)

        def fail(message):
            errors.append(f'{prefix}: {message}')

        for field in ('id', 'company_key', 'company', 'title'):
            if not filled(job.get(field)):
                fail(f'{field} missing')
        if job.get('employment_mode') not in allowed_modes:
            fail('invalid employment_mode')
        elif job.get('employment_mode') != employment_mode:
            fail('job employment_mode does not match report employment_mode')
        ident = job.get('id')
        if filled(ident):
            if ident in seen:
                fail('duplicate job id')
            seen.add(ident)
        if type(job.get('selected')) is not bool:
            fail('selected must be boolean')
        if job.get('entity_type') not in ('company', 'partnership', 'institution'):
            fail('invalid entity_type')
        if job.get('status') not in ('open', 'closed', 'unknown'):
            fail('invalid status')
        if job.get('eligibility') not in ('pass', 'fail', 'unknown'):
            fail('invalid eligibility')
        if job.get('source_tier') not in source_tiers:
            fail('invalid source_tier')
        if not url(job.get('jd_url')):
            fail('specific JD URL missing or invalid')
        requirements = job.get('requirements')
        if not isinstance(requirements, list) or not requirements:
            fail('requirements missing')
            requirements = []
        results = []
        for req in requirements:
            if not isinstance(req, dict):
                fail('invalid requirement object')
                continue
            for field in ('text', 'jd_evidence', 'candidate_evidence'):
                if not filled(req.get(field)):
                    fail(f'requirement {field} missing')
            if type(req.get('required')) is not bool:
                fail('requirement required must be boolean')
            if req.get('result') not in ('met', 'unmet', 'unknown'):
                fail('invalid requirement result')
            for error in ref_errors(req.get('evidence_refs'), ledger,
                                    factual=req.get('result') == 'met',
                                    allow_empty=req.get('result') != 'met'):
                fail(error)
            if req.get('required') is True:
                results.append(req.get('result'))
        computed = ('fail' if 'unmet' in results else
                    'unknown' if not results or 'unknown' in results else 'pass')
        if job.get('eligibility') != computed:
            fail(f'eligibility contradicts requirements: expected {computed}')
        if job.get('selected') is not True:
            if not filled(job.get('reason')):
                fail('non-selected record needs reason')
            continue
        corpus_id = job.get('corpus_id')
        row = corpus_rows.get(corpus_id) if filled(corpus_id) else None
        if row is None:
            fail('selected job requires existing corpus_id')
        else:
            for field in ('company_key', 'title', 'employment_mode', 'source_tier'):
                if job.get(field) != row.get(field):
                    fail(f'{field} does not match corpus record')
            if (not url(job.get('jd_url')) or not url(row.get('jd_url')) or
                    canonical_url(job['jd_url']) != canonical_url(row['jd_url'])):
                fail('JD URL does not match corpus record')
            if row.get('full_jd') is not True or row.get('comparable') is not True or row.get('status') != 'open':
                fail('selected corpus record must be complete, comparable and open')
            raw_source = row.get('source_text', '')
            reviewed_required = {req['jd_evidence'] for req in requirements
                                 if isinstance(req, dict) and req.get('required') is True
                                 and filled(req.get('jd_evidence'))}
            original_requirements = row.get('requirements')
            if isinstance(original_requirements, list):
                for original in original_requirements:
                    if (isinstance(original, dict) and original.get('required') is True and
                            filled(original.get('source_excerpt')) and
                            original['source_excerpt'] not in reviewed_required):
                        fail('required corpus condition omitted or downgraded in report')
            for req in requirements:
                if isinstance(req, dict) and (not filled(req.get('jd_evidence')) or
                        not isinstance(raw_source, str) or req['jd_evidence'] not in raw_source):
                    fail('JD requirement evidence not found in corpus source')
        if job.get('entity_type') not in ('company', 'partnership'):
            fail('institution cannot count as company')
        if job.get('source_tier') not in ('employer_official', 'official_ats',
                                          'employer_verified_platform'):
            fail('main list requires employer-official, official ATS, or verified employer source')
        if job.get('status') != 'open' or computed != 'pass':
            fail('main list requires open + pass')
        if job.get('priority') not in ('A', 'B'):
            fail('main-list priority must be A or B')
        if job.get('hard_requirements_reviewed') is not True:
            fail('full JD requirement review not confirmed')
        if not filled(job.get('open_evidence')):
            fail('open evidence missing')
        if not url(job.get('apply_url')) and not filled(job.get('application_method')):
            fail('application route missing')
        try:
            age = now - timestamp(job.get('checked_at', ''))
            if age < timedelta(0) or age > timedelta(hours=24):
                fail('check timestamp is future or older than 24 hours')
        except (ValueError, TypeError, AttributeError):
            fail('checked_at must be ISO timestamp with timezone')
        if job.get('closes_at'):
            try:
                if timestamp(job['closes_at']) <= now:
                    fail('deadline passed')
            except (ValueError, TypeError, AttributeError):
                fail('closes_at must be ISO timestamp with timezone')
        for field, minimum, keys in (
            ('mappings', 3, ('requirement', 'resume_evidence', 'gap', 'action')),
            ('rewrites', 2, ('text', 'placement', 'use_status')),
        ):
            items = job.get(field)
            if not isinstance(items, list) or len(items) < minimum:
                fail(f'{field} requires at least {minimum} items')
                continue
            for item in items:
                if not isinstance(item, dict) or any(not filled(item.get(k)) for k in keys):
                    fail(f'incomplete {field} item')
                    continue
                if field == 'rewrites':
                    refs = item.get('evidence_refs')
                    for error in ref_errors(refs, ledger, factual=item.get('use_status') == 'ready'):
                        fail(error)
                    if item.get('use_status') == 'ready' and item.get('fidelity_reviewed') is not True:
                        fail('ready rewrite requires source fidelity review')
                    if item['use_status'] not in ('ready', 'verify_first', 'create_first'):
                        fail('invalid rewrite use_status')
                else:
                    for error in ref_errors(item.get('evidence_refs'), ledger, allow_empty=True):
                        fail(error)
        if len(errors) == start:
            companies.add(job['company_key'].strip().casefold())
    if len(companies) < target:
        errors.append(f'company shortfall: {len(companies)}/{target}; stage report only')
    return {'ok': not errors, 'companies': len(companies), 'target': target, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--as-of', type=timestamp)
    args = parser.parse_args()
    try:
        with open(args.input, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2
    result = validate(data, args.as_of)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
