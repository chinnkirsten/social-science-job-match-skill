#!/usr/bin/env python3
"""CLI: opt-in adapters, cache, real three-role runs, and evidence-gated Word."""
import argparse
import json
import sys
from pathlib import Path
from jobmatch_runtime.adapters import doctor, invoke
from jobmatch_runtime.cache import Cache
from jobmatch_runtime.common import AdapterError, atomic_json
from jobmatch_runtime.pipeline import run, finalize


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    d = sub.add_parser('doctor'); d.add_argument('--config')
    a = sub.add_parser('adapter'); a.add_argument('component'); a.add_argument('--input', required=True)
    a.add_argument('--config', required=True); a.add_argument('--output', required=True); a.add_argument('--cache-dir')
    r = sub.add_parser('run'); r.add_argument('--config', required=True); r.add_argument('--candidate', required=True)
    r.add_argument('--sources', required=True); r.add_argument('--output-dir', required=True)
    w = sub.add_parser('report'); w.add_argument('input'); w.add_argument('--output', required=True); w.add_argument('--stage', action='store_true')
    f = sub.add_parser('finalize'); f.add_argument('input'); f.add_argument('--review', required=True)
    f.add_argument('--output', required=True); f.add_argument('--stage', action='store_true')
    c = sub.add_parser('cache-prune'); c.add_argument('--cache-dir', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            result = doctor(read(args.config) if args.config else None)
        elif args.command == 'adapter':
            config = read(args.config)
            settings = config.get('adapters', {}).get(args.component, config)
            cache = Cache(args.cache_dir) if args.cache_dir else None
            value = invoke(args.component, read(args.input), settings, cache)
            atomic_json(args.output, value)
            result = {k: value[k] for k in ('component_id', 'status', 'upstream_version', 'cache_hit')}
        elif args.command == 'run':
            evidence = read(args.candidate)
            result = run(read(args.config), evidence.get('candidate_evidence') if isinstance(evidence, dict) else evidence,
                         read(args.sources), args.output_dir)
        elif args.command == 'report':
            from jobmatch_runtime.report import render_report
            result = render_report(read(args.input), args.output, stage=args.stage)
        elif args.command == 'finalize':
            result = finalize(read(args.input), read(args.review), args.output, stage=args.stage)
        else:
            result = Cache(args.cache_dir).prune()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') in ('blocked', 'needs_evidence') or result.get('ok') is False else 0
    except AdapterError as exc:
        print(json.dumps({'status': 'blocked', 'code': exc.code, 'message': exc.message}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        # Do not print full private paths, payloads, credentials or upstream bodies.
        print(json.dumps({'status': 'blocked', 'code': 'invalid_input_or_io', 'message': 'Check input JSON, dependency and output path'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
