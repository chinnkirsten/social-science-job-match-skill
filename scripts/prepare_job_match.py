#!/usr/bin/env python3
"""Prepare a resume and scoped recruitment links; export only after human review."""
import argparse
import json
import sys
from pathlib import Path

from jobmatch_runtime.cache import Cache
from jobmatch_runtime.common import AdapterError, atomic_json, private_dir
from jobmatch_runtime.preparation import (prepare_resume, confirm_resume, discover_links,
    confirm_sources, prepared_bundle, build_search_plan, import_search_results)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def private_destination(path):
    target = Path(path).resolve()
    public_root = Path(__file__).resolve().parents[1]
    if target == public_root or public_root in target.parents:
        raise AdapterError('private_output_location', 'Preparation data must remain outside the public Skill directory')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    resume = sub.add_parser('resume')
    resume.add_argument('--resume', required=True)
    resume.add_argument('--intake', required=True)
    resume.add_argument('--config')
    resume.add_argument('--use-model', action='store_true')
    resume.add_argument('--output', required=True)
    discover = sub.add_parser('discover')
    discover.add_argument('--seeds', required=True)
    discover.add_argument('--config', required=True)
    discover.add_argument('--cache-dir', required=True)
    discover.add_argument('--output', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--intake', required=True)
    plan.add_argument('--output', required=True)
    search_results = sub.add_parser('search-results')
    search_results.add_argument('--results', required=True)
    search_results.add_argument('--intake', required=True)
    search_results.add_argument('--output', required=True)
    export = sub.add_parser('export')
    for name in ('resume-draft', 'resume-review', 'source-draft', 'source-review', 'config', 'output-dir'):
        export.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            private_destination(args.output_dir)
        else:
            private_destination(args.output)
        if args.command == 'discover':
            private_destination(args.cache_dir)
        if args.command == 'resume':
            result = prepare_resume(args.resume, read(args.intake), read(args.config) if args.config else {},
                                    use_model=args.use_model)
            atomic_json(args.output, result)
            summary = {'status': result['status'], 'draft_sha256': result['draft_sha256'],
                       'parsed_records': len(result['proposed_records'])}
        elif args.command == 'plan':
            result = build_search_plan(read(args.intake))
            atomic_json(args.output, result)
            summary = {'status': result['status'], 'queries': len(result['queries'])}
        elif args.command == 'search-results':
            result = import_search_results(read(args.results), read(args.intake))
            atomic_json(args.output, result)
            summary = {'status': result['status'], 'draft_sha256': result['draft_sha256'],
                       'unverified_links': len(result['candidate_links']), 'truncated': result['truncated']}
        elif args.command == 'discover':
            result = discover_links(read(args.seeds), read(args.config), Cache(args.cache_dir))
            atomic_json(args.output, result)
            summary = {'status': result['status'], 'draft_sha256': result['draft_sha256'],
                       'unverified_links': len(result['candidate_links']), 'failed_indexes': len(result['failures']),
                       'truncated': result['truncated']}
        else:
            resume_draft, source_draft = read(args.resume_draft), read(args.source_draft)
            candidate, intake = confirm_resume(resume_draft, read(args.resume_review))
            specs = confirm_sources(source_draft, read(args.source_review))
            if source_draft['intake'] != intake:
                raise AdapterError('intake_mismatch', 'Resume and discovery recruitment scope must match')
            bundle = prepared_bundle(candidate, intake, specs, read(args.config), source_draft)
            output = Path(args.output_dir)
            if output.exists():
                raise AdapterError('output_exists', 'Use a new output directory; existing artifacts are not overwritten')
            private_dir(output)
            for name, value in bundle.items():
                atomic_json(output / (name + '.json'), value)
            summary = {'status': 'prepared', 'confirmed_records': len(candidate['candidate_evidence']),
                       'reviewed_source_links': len(specs), 'vacancy_status': 'not_yet_checked',
                       'next': 'Run run_job_match.py run with config.json, candidate.json and sources.json'}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except AdapterError as exc:
        print(json.dumps({'status': 'blocked', 'code': exc.code, 'message': exc.message}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'blocked', 'code': 'invalid_input_or_io', 'message': 'Check input and output permissions; private contents omitted'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
