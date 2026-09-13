#!/usr/bin/env python3
"""Inspect prerequisites without installing, enabling or invoking upstream models."""
import argparse
import json
from pathlib import Path

from jobmatch_runtime.common import AdapterError, atomic_json
from jobmatch_runtime.readiness import inspect_readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--live', action='store_true', help='GET explicitly configured service health paths only')
    parser.add_argument('--output', type=Path, help='New private JSON output; existing files are not overwritten')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8')) if args.config else {}
        history_path = Path(__file__).resolve().parents[1] / 'integrations/runtime-verification.json'
        history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else {}
        result = inspect_readiness(config, live=args.live, historical=history)
        if args.output:
            atomic_json(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AdapterError, OSError, ValueError, TypeError):
        # Do not echo paths, credentials, URLs or input values in diagnostic errors.
        print(json.dumps({'error': 'readiness_check_failed', 'message': '检查配置结构、输入文件或输出位置；未执行部署。'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
