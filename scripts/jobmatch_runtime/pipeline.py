"""Checkpointed three-model-role orchestration with explicit human finalization."""
import copy
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from build_evidence_basis import build
from evidence_core import ledger_check, ref_errors, digest, canonical_url
from validate_jobs import validate
from . import __version__, prompts, sources
from .adapters import invoke
from .cache import Cache
from .common import AdapterError, atomic_json, fingerprint, private_dir, utcnow
from .llm import ModelClient

MODES = {'internship', 'campus_full_time', 'experienced_full_time'}


def _consolidate_variants(report, model):
    variants = report['resume_variants']
    by_name = {}
    for variant in variants:
        by_name.setdefault(variant['name'], []).extend(variant['job_ids'])
    groups = [{'name': name, 'job_ids': ids} for name, ids in by_name.items()]
    if len(groups) > 3:
        groups = model.call('EvidenceMapper', prompts.VARIANTS, {'variants': variants}, private=True).get('groups')
    if not isinstance(groups, list) or len(groups) > 3:
        raise AdapterError('invalid_variant_groups', 'Resume planning must produce at most three groups')
    expected = [j for v in variants for j in v['job_ids']]
    try:
        actual = [j for g in groups for j in g['job_ids']]
        if sorted(expected) != sorted(actual) or len(actual) != len(set(actual)) or any(not isinstance(g['name'], str) or not g['name'].strip() for g in groups):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise AdapterError('invalid_variant_groups', 'Every job must belong to exactly one named resume group') from None
    report['resume_variants'] = []
    assignments = {}
    for index, group in enumerate(groups, 1):
        vid = f'resume-track-{index}'
        changes = []
        for variant in variants:
            if set(variant['job_ids']) & set(group['job_ids']):
                changes.extend(variant.get('changes', []))
        report['resume_variants'].append({**group, 'id': vid, 'changes': list(dict.fromkeys(changes))})
        assignments.update({job_id: vid for job_id in group['job_ids']})
    for action in report['action_plan']:
        action['resume_variant_id'] = assignments[action['job_id']]


def _configuration(config, ledger, specs):
    if config.get('employment_mode') not in MODES:
        raise AdapterError('mode_required', 'Choose internship, campus_full_time or experienced_full_time before fetching')
    cities = config.get('locations')
    if not isinstance(cities, list) or not cities or not all(isinstance(c, str) and c.strip() for c in cities):
        raise AdapterError('cities_required', 'Specify normalized target locations')
    _, errors = ledger_check(ledger)
    if errors:
        raise AdapterError('invalid_ledger', 'Candidate evidence ledger is invalid; confirmation must come from user input')
    target = config.get('target_companies', 20)
    if type(target) is not int or target < 1:
        raise AdapterError('invalid_config', 'target_companies must be positive')
    limits = config.get('limits', {})
    maximum = limits.get('max_jobs', 60)
    if type(maximum) is not int or not 1 <= maximum <= 300:
        raise AdapterError('invalid_config', 'max_jobs must be 1..300')
    if not isinstance(specs, list) or not specs or len(specs) > maximum:
        raise AdapterError('source_limit', 'Provide a nonempty bounded list of specific source URLs')
    seen = set()
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get('url'), str):
            raise AdapterError('invalid_source', 'Every source needs a URL')
        key = canonical_url(spec['url'])
        if key in seen:
            raise AdapterError('duplicate_source', 'Duplicate source URL in request')
        seen.add(key)
        if spec.get('source_tier') not in ('employer_official', 'official_ats', 'employer_verified_platform', 'official_repost', 'aggregator'):
            raise AdapterError('source_tier_required', 'Source tier must be supplied as reviewable metadata, not guessed by an Agent')
    def secrets(value):
        if isinstance(value, dict):
            if any(str(k).lower() in ('api_key', 'token', 'password', 'authorization', 'cookie') for k in value):
                raise AdapterError('inline_credential', 'Use credential environment-variable names, never inline secrets')
            for v in value.values(): secrets(v)
        elif isinstance(value, list):
            for v in value: secrets(v)
    secrets(config)


def _bind_payload(template, context):
    if isinstance(template, str) and template.startswith('$'):
        value = context
        for part in template[1:].split('.'):
            if not isinstance(value, dict) or part not in value:
                raise AdapterError('invalid_binding', 'Adapter input binding not found')
            value = value[part]
        return copy.deepcopy(value)
    if isinstance(template, dict):
        return {k: _bind_payload(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [_bind_payload(v, context) for v in template]
    return template


def _one(spec, config, ledger, cache, model, loader):
    captured = loader(spec, config, cache, force=config.get('refresh_sources') is True)
    source = {'url': captured['url'], 'text': captured['text'], 'links': captured.get('links', [])}
    extracted = model.call('SourceScout', prompts.SCOUT, {'profile': {'employment_mode': config['employment_mode'],
                            'locations': config['locations']}, 'source': source})
    mode = extracted.get('employment_mode')
    if mode not in MODES or mode != config['employment_mode']:
        return {'excluded': True, 'reason': '招聘模式不符或未确认', 'source_id': fingerprint(spec['url'])[:16]}
    cities = extracted.get('locations')
    if not isinstance(cities, list) or not any(c in config['locations'] for c in cities):
        return {'excluded': True, 'reason': '地点超出范围或未确认', 'source_id': fingerprint(spec['url'])[:16]}
    company, title = extracted.get('company'), extracted.get('title')
    if not all(isinstance(v, str) and v.strip() for v in (company, title)):
        raise AdapterError('invalid_extraction', 'SourceScout did not identify a company and specific title')
    requirements = extracted.get('requirements')
    if not isinstance(requirements, list) or not requirements:
        return {'excluded': True, 'reason': '未能提取完整任职条件', 'source_id': fingerprint(spec['url'])[:16]}
    for req in requirements:
        if (not isinstance(req, dict) or type(req.get('required')) is not bool or
                not isinstance(req.get('text'), str) or not req['text'].strip() or
                not isinstance(req.get('source_excerpt'), str) or not req['source_excerpt'].strip() or
                req['source_excerpt'] not in source['text']):
            raise AdapterError('unsupported_excerpt', 'Extracted requirement is not anchored in the source')
        req['normalized_skills'] = []  # Optional taxonomy tools, not SourceScout, add classifications.
    status = extracted.get('status')
    evidence = extracted.get('open_evidence')
    if status not in ('open', 'closed', 'unknown'):
        raise AdapterError('invalid_extraction', 'Invalid vacancy state')
    if status == 'open' and (not isinstance(evidence, str) or not evidence.strip() or evidence not in source['text']):
        raise AdapterError('unsupported_openness', 'Openness needs an exact captured source excerpt')
    ident = fingerprint(canonical_url(spec['url']))[:20]
    record = {'corpus_id': ident, 'company_key': spec.get('company_key', re.sub(r'\s+', '', company.casefold())),
              'company': company, 'title': title, 'employment_mode': mode, 'locations': cities,
              'geography': '/'.join(cities), 'source_tier': spec['source_tier'], 'jd_url': spec['url'],
              'captured_at': captured['captured_at'], 'status': status,
              'source_text': source['text'], 'source_sha256': digest(source['text']),
              'full_jd': extracted.get('full_jd') is True, 'comparable': False,
              'responsibilities': extracted.get('responsibilities', []), 'requirements': requirements}
    if status != 'open' or record['full_jd'] is not True:
        record['excluded_reason'] = '岗位关闭、开放状态未知或 JD 不完整'
        return {'record': record}
    record['comparable'] = True
    enrichments = []
    for step in config.get('adapter_steps', []):
        component = step['component']
        payload = _bind_payload(step['input'], {'source': source, 'scout': extracted, 'candidate': ledger})
        if '$candidate' in json.dumps(step['input']):
            payload['data_classification'] = 'private'
        enrichments.append(invoke(component, payload, config.get('adapters', {}).get(component, {}), cache))
    mapped = model.call('EvidenceMapper', prompts.MAPPER, {'source': source, 'jd': extracted,
                        'candidate_evidence': ledger, 'enrichments': enrichments}, private=True)
    proposed = mapped.get('requirements')
    if not isinstance(proposed, list) or len(proposed) != len(requirements):
        raise AdapterError('condition_omission', 'Mapper must cover every extracted condition exactly once')
    merged = []
    known, _ = ledger_check(ledger)
    for original, judgment in zip(requirements, proposed):
        if not isinstance(judgment, dict) or judgment.get('source_excerpt') != original['source_excerpt']:
            raise AdapterError('condition_reordered', 'Mapper must bind each judgment to its exact source condition')
        result = judgment.get('result')
        refs = judgment.get('evidence_refs')
        if result not in ('met', 'unmet', 'unknown') or ref_errors(refs, known, factual=result == 'met', allow_empty=result != 'met'):
            raise AdapterError('unsupported_candidate_claim', 'Candidate condition has invalid or unconfirmed evidence')
        merged.append({'text': original['text'], 'required': original['required'], 'result': result,
                       'jd_evidence': original['source_excerpt'], 'candidate_evidence': judgment.get('candidate_evidence', ''),
                       'evidence_refs': refs})
    results = [r['result'] for r in merged if r['required']]
    eligibility = 'fail' if 'unmet' in results else 'unknown' if not results or 'unknown' in results else 'pass'
    audit = model.call('Auditor', prompts.AUDITOR, {'source': source, 'candidate_evidence': ledger,
                       'extraction': extracted, 'proposal': mapped}, private=True)
    if type(audit.get('approved')) is not bool or not isinstance(audit.get('issues'), list):
        raise AdapterError('invalid_audit', 'Auditor must return approved:boolean and issues:list')
    approved = audit['approved'] and not audit['issues']
    apply_url = extracted.get('apply_url', '')
    if apply_url not in [source['url'], spec['url'], *source['links']]:
        apply_url = ''
    selectable = approved and eligibility == 'pass' and bool(apply_url) and spec['source_tier'] in (
        'employer_official', 'official_ats', 'employer_verified_platform')
    rewrites = mapped.get('rewrites', [])
    for rewrite in rewrites:
        if rewrite.get('use_status') not in ('verify_first', 'create_first'):
            rewrite['use_status'] = 'verify_first'
        rewrite['fidelity_reviewed'] = False
    job = {'id': ident, 'corpus_id': ident, 'company_key': record['company_key'], 'company': company,
           'title': title, 'employment_mode': mode, 'entity_type': spec.get('entity_type', 'company'),
           'selected': False, 'proposed_selection': selectable, 'status': status, 'eligibility': eligibility,
           'priority': mapped.get('priority', 'B') if selectable else '', 'source_tier': spec['source_tier'],
           'jd_url': spec['url'], 'apply_url': apply_url, 'checked_at': captured['captured_at'],
           'open_evidence': evidence, 'hard_requirements_reviewed': False, 'requirements': merged,
           'mappings': mapped.get('mappings', []), 'rewrites': rewrites, 'details': extracted.get('details', {}),
           'reason': '机器分析通过，待人工读源复核' if selectable else '；'.join(map(str, audit['issues'])) or mapped.get('reason') or '条件或申请路径未确认'}
    return {'record': record, 'job': job, 'model_audit': audit, 'variant': mapped.get('resume_variant'),
            'action': mapped.get('action'), 'adapters': [{k: e[k] for k in ('component_id', 'upstream_version', 'invoked_at', 'cache_hit')} for e in enrichments]}


def run(config, ledger, specs, output_dir, *, model=None, loader=None):
    _configuration(config, ledger, specs)
    destination = Path(output_dir).resolve()
    skill_root = Path(__file__).resolve().parents[2]
    if skill_root == destination or skill_root in destination.parents:
        raise AdapterError('private_output_location', 'Run data must stay outside the public Skill directory')
    directory = private_dir(destination)
    lock = directory / '.run.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise AdapterError('run_locked', 'Another run owns this directory; inspect before removing a stale lock') from None
    try:
        return _run(config, ledger, specs, directory, model=model, loader=loader)
    finally:
        lock.unlink()


def _run(config, ledger, specs, directory, *, model=None, loader=None):
    ledger = copy.deepcopy(ledger)
    frozen_ledger_hash = fingerprint(ledger)
    identity = fingerprint({'config': config, 'ledger': ledger, 'sources': specs, 'prompts': prompts.VERSION})
    checkpoint = directory / 'checkpoint.json'
    state = {'schema': '1', 'request_sha256': identity, 'jobs': {}, 'created_at': utcnow()}
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if state.get('request_sha256') != identity:
            raise AdapterError('resume_mismatch', 'Profile, evidence, model or source scope changed; use a new run directory')
    elif any(directory.iterdir()) and any(p.name != '.run.lock' for p in directory.iterdir()):
        raise AdapterError('output_not_empty', 'New run directory must be empty')
    private_cache = config.get('privacy', {}).get('cache_candidate_data') is True
    cache = Cache(directory / 'cache', private_enabled=private_cache)
    model = model or ModelClient(config.get('llm', {}), allow_remote_candidate_data=config.get('privacy', {}).get('allow_remote_candidate_data') is True)
    if isinstance(model, ModelClient):
        model.preflight()  # Fail missing consent/credentials before any collection or model charge.
    loader = loader or sources.fetch
    now = datetime.now(timezone.utc)
    pending = []
    for spec in specs:
        key = fingerprint(spec['url'])
        stored = state['jobs'].get(key)
        try:
            timestamp = stored.get('result', {}).get('record', {}).get('captured_at', stored['completed_at'])
            recent = timedelta(0) <= now - datetime.fromisoformat(timestamp) < timedelta(hours=24)
        except (AttributeError, TypeError, KeyError, ValueError):
            recent = False
        if config.get('refresh_sources') is True or not stored or not recent or stored.get('status') != 'complete':
            pending.append((key, spec))
    workers = config.get('limits', {}).get('max_parallel_calls', 2)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise AdapterError('invalid_config', 'max_parallel_calls must be 1..4')
    def process(pair):
        key, spec = pair
        try:
            return key, {'status': 'complete', 'completed_at': utcnow(), 'result': _one(spec, config, ledger, cache, model, loader)}
        except AdapterError as exc:
            return key, {'status': 'error', 'error_code': exc.code}
        except (ValueError, TypeError, KeyError, AttributeError):
            return key, {'status': 'error', 'error_code': 'invalid_stage_output'}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for key, result in executor.map(process, pending):
            result['completed_at'] = utcnow()
            state['jobs'][key] = result
            atomic_json(checkpoint, state, overwrite=True)
    completed = [state['jobs'][fingerprint(s['url'])]['result'] for s in specs
                 if state['jobs'].get(fingerprint(s['url']), {}).get('status') == 'complete']
    records, jobs = [], []
    seen_urls, seen_text = set(), set()
    for item in completed:
        record = item.get('record')
        if record:
            url_key, content = canonical_url(record['jd_url']), record['source_sha256']
            if record['comparable'] and (url_key in seen_urls or content in seen_text):
                item = {**item, 'job': None}
                record = {**record, 'comparable': False, 'excluded_reason': '重复岗位，保留原始抓取记录'}
            seen_urls.add(url_key); seen_text.add(content)
            records.append(record)
        if item.get('job'):
            jobs.append(item['job'])
    corpus = {'method_version': '3.0', 'employment_mode': config['employment_mode'], 'locations': config['locations'],
              'geography': '/'.join(config['locations']), 'collected_at': utcnow(), 'candidate_pool_count': len(specs),
              'candidate_evidence': ledger, 'taxonomies': [], 'records': records}
    summary = build(corpus)
    report = {'runtime_version': __version__, 'target_companies': config.get('target_companies', 20),
              'employment_mode': config['employment_mode'], 'corpus': corpus, 'jobs': jobs,
              'candidate_evidence_sha256': frozen_ledger_hash, 'evidence_basis': summary.get('evidence_basis', {}),
              'report_summary': f'机器分析草稿。读取来源 {len(specs)} 项；生成岗位分析 {len(jobs)} 项。尚未人工核验，已确认可投为 0 家。',
              'resume_variants': [], 'action_plan': [], 'execution': {'model_calls': model.calls,
              'cache': cache.stats(), 'resumed_jobs': len(specs)-len(pending), 'fresh_jobs': len(pending),
              'adapters': [a for item in completed for a in item.get('adapters', [])]}}
    for item in completed:
        job = item.get('job')
        if not job or not job['proposed_selection']:
            continue
        variant, action = item.get('variant'), item.get('action')
        if isinstance(variant, dict):
            vid = 'resume-' + job['id']
            report['resume_variants'].append({**variant, 'id': vid, 'job_ids': [job['id']]})
            if isinstance(action, dict):
                report['action_plan'].append({**action, 'job_id': job['id'], 'resume_variant_id': vid})
    if fingerprint(ledger) != report['candidate_evidence_sha256']:
        raise AdapterError('ledger_mutation', 'Frozen candidate ledger changed unexpectedly')
    _consolidate_variants(report, model)
    validation = validate(report)
    sequence = len(list(directory.glob('report-*.json'))) + 1
    name = f'report-{sequence:03d}'
    atomic_json(directory / (name + '.json'), report)
    review = {'report_sha256': fingerprint(report), 'approvals': [{'job_id': job['id'],
              'source_sha256': next(r['source_sha256'] for r in records if r['corpus_id'] == job['corpus_id']),
              'source_identity_verified': False, 'open_status_verified': False, 'full_jd_reviewed': False,
              'qualification_reviewed': False, 'rewrite_fidelity_reviewed': False,
              'ready_rewrite_indices': []} for job in jobs if job['proposed_selection']]}
    atomic_json(directory / (name + '-review.json'), review)
    result = {'status': 'awaiting_review' if validation.get('evidence_ok') else 'needs_evidence',
              'report_file': name + '.json', 'validation': validation, 'execution': report['execution'],
              'failures': [s['error_code'] for s in state['jobs'].values() if s['status'] == 'error']}
    if validation.get('evidence_ok'):
        from .report import render_report
        result['word'] = render_report(report, directory / (name + '.docx'), stage=True)
    atomic_json(directory / (name + '-result.json'), result)
    return result


def finalize(report, review, output, *, stage=False):
    if review.get('report_sha256') != fingerprint(report):
        raise AdapterError('stale_review', 'Review does not match this exact report; review changed evidence again')
    finalized = copy.deepcopy(report)
    rows = {r['corpus_id']: r for r in finalized['corpus']['records']}
    approvals = review.get('approvals', [])
    if not isinstance(approvals, list) or len({a.get('job_id') for a in approvals}) != len(approvals):
        raise AdapterError('invalid_review', 'Review approvals must have unique job IDs')
    valid_ids = {j['id'] for j in finalized['jobs']}
    if any(a.get('job_id') not in valid_ids for a in approvals):
        raise AdapterError('invalid_review', 'Review references an unknown job')
    by_id = {a['job_id']: a for a in approvals}
    for job in finalized['jobs']:
        item = by_id.get(job['id'], {})
        passed = all(item.get(k) is True for k in ('source_identity_verified', 'open_status_verified',
                    'full_jd_reviewed', 'qualification_reviewed', 'rewrite_fidelity_reviewed'))
        if not passed:
            continue
        if item.get('source_sha256') != rows[job['corpus_id']]['source_sha256'] or not job.get('proposed_selection'):
            raise AdapterError('invalid_review', 'Approval source hash or proposal state is invalid')
        job['selected'], job['proposed_selection'], job['hard_requirements_reviewed'] = True, False, True
        job['human_review'] = item
        for index in item.get('ready_rewrite_indices', []):
            if type(index) is not int or index < 0 or index >= len(job['rewrites']):
                raise AdapterError('invalid_review', 'Rewrite approval index is invalid')
            if job['rewrites'][index]['use_status'] == 'create_first':
                raise AdapterError('new_evidence_required', 'Planned work needs a new confirmed ledger and rerun before ready status')
            job['rewrites'][index]['use_status'] = 'ready'
            job['rewrites'][index]['fidelity_reviewed'] = True
    chosen = {j['id'] for j in finalized['jobs'] if j['selected'] or (stage and j.get('proposed_selection'))}
    finalized['resume_variants'] = [{**v, 'job_ids': [j for j in v['job_ids'] if j in chosen]}
                                  for v in finalized['resume_variants'] if set(v['job_ids']) & chosen]
    finalized['action_plan'] = [a for a in finalized['action_plan'] if a['job_id'] in chosen]
    count = len({j['company_key'] for j in finalized['jobs'] if j['selected']})
    finalized['report_summary'] = f'人工读源复核后，已确认符合已知条件的公司 {count} 家；目标 {finalized["target_companies"]} 家。投递前仍需复查岗位状态。'
    validation = validate(finalized)
    if not validation.get('evidence_ok') or (not stage and not validation['ok']):
        return {'status': 'blocked', 'validation': validation}
    from .report import render_report
    return render_report(finalized, output, stage=stage)
