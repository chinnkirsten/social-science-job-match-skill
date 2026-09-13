"""Non-provisioning readiness inspection; HTTP reachability is not model readiness."""
import importlib.util
import os
import platform
import re
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import registry
from .adapters_services import _headers
from .common import AdapterError, http_bytes, utcnow


SDKS = {'docling': 'docling', 'crawl4ai': 'crawl4ai', 'jobspy': 'jobspy'}
SERVICES = {'tabiya_livelihoods_classifier', 'esco_skill_extractor',
            'tabiya_compass', 'resume_matcher'}
DATASETS = {'onet_database', 'esco', 'tabiya_open_dataset'}
PROFILES = {
    'standard': {
        'purpose': '简历文字解析、公开招聘页采集与职业资料参考；不是自动安装方案。',
        'components': ['docling', 'crawl4ai', 'onet_database', 'esco', 'tabiya_open_dataset'],
        'workflow_requirements': ['确认求职类型与城市', '核对简历提取内容', '核对官方 JD 与申请条件',
                                  '逐岗修改建议与人工复核', '不足 20 家时说明缺口'],
    },
    'enhanced': {
        'purpose': '按需增加岗位发现、职业分类、技能分析和简历建议。',
        'components': ['jobspy', 'tabiya_livelihoods_classifier', 'esco_skill_extractor',
                       'tabiya_compass', 'resume_matcher'],
        'workflow_requirements': ['事先确认调用预算与资料去向', '岗位线索返回官方 JD 核查'],
    },
    'evaluation': {
        'purpose': '独立研究评估，不作为每次出报告的必备依赖。',
        'components': ['melo_benchmark', 'tgre_classification'],
        'workflow_requirements': ['固定数据与版本', '使用获授权的标注样本并报告适用范围'],
    },
}


def _safe_config(value):
    if isinstance(value, dict):
        if any(str(k).lower() in ('api_key', 'token', 'password', 'authorization', 'cookie') for k in value):
            raise AdapterError('inline_credential', 'Use credential environment-variable names, not inline secrets')
        for child in value.values():
            _safe_config(child)
    elif isinstance(value, list):
        for child in value:
            _safe_config(child)


def _service_base(cfg):
    """Syntax only: passive checks must not resolve DNS."""
    base = cfg.get('service_url')
    if not isinstance(base, str):
        return None
    try:
        parts = urlsplit(base)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or
                parts.username or parts.password or parts.query or parts.fragment):
            return None
        parts.port
    except ValueError:
        return None
    return base.rstrip('/')


def _probe(cfg, component_id):
    base = _service_base(cfg)
    path = cfg.get('health_path')
    if not base:
        return None, 'invalid_service_url'
    if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or
            not re.fullmatch(r'/[A-Za-z0-9_./-]*', path) or '..' in path.split('/')):
        return None, 'health_path_required'
    timeout = cfg.get('health_timeout', 5)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 10:
        return None, 'invalid_health_timeout'
    try:
        _, metadata = http_bytes(
            base + path, headers=_headers(cfg, component_id == 'tabiya_livelihoods_classifier'),
            timeout=timeout, allow_local=cfg.get('allow_local') is True,
            max_bytes=65536, allowed_hosts={urlsplit(base).hostname})
        # No response body is retained: health pages can contain operational secrets.
        return True, 'http_' + str(metadata['status'])
    except AdapterError as exc:
        # 401/403/404/5xx establish a responding HTTP endpoint, not a healthy service.
        if re.fullmatch(r'http_[0-9]{3}', exc.code):
            return True, exc.code
        return False, exc.code


def inspect_readiness(config=None, *, live=False, historical=None):
    config = {} if config is None else config
    if not isinstance(config, dict) or not isinstance(config.get('adapters', {}), dict):
        raise AdapterError('invalid_config', 'Configuration and adapters must be objects')
    _safe_config(config)
    observed_at = utcnow()
    historical = historical or {}
    previous = {item.get('id'): item for item in historical.get('components', []) if isinstance(item, dict)}
    rows = []
    for ident in registry():
        cfg = config.get('adapters', {}).get(ident, {})
        if not isinstance(cfg, dict):
            raise AdapterError('invalid_config', 'Each adapter configuration must be an object')
        row = {'id': ident, 'enabled': cfg.get('enabled') is True,
               'installed': None, 'configured': False, 'reachable': None,
               'live_operation_verified': None, 'effectiveness_evaluated': None,
               'checked_at': observed_at, 'missing': [], 'observations': []}
        if ident in SDKS:
            try:
                row['installed'] = importlib.util.find_spec(SDKS[ident]) is not None
            except (ImportError, ValueError):
                row['installed'] = False
            row['configured'] = ident == 'docling' or cfg.get('terms_accepted') is True
            if not row['installed']:
                row['missing'].append('安装固定版本 SDK，并验证依赖；本检查不会安装软件。')
            if ident == 'crawl4ai':
                row['missing'].append('验证浏览器资源可用，并实测获准采集的公开页面。')
            if ident == 'docling':
                row['missing'].append('用无敏感信息的文档实测解析；当前适配器不自动配置 OCR。')
            if ident == 'jobspy':
                row['missing'].append('实测所选平台；结果仅作线索，仍需官方 JD 核查。')
            if not row['configured']:
                row['missing'].append('阅读目标站点条款后配置 terms_accepted。')
        elif ident in SERVICES:
            row['configured'] = bool(_service_base(cfg)) and cfg.get('service_ready') is True
            row['observations'].append('远程安装状态未知；service_ready 只是配置声明。')
            if not _service_base(cfg):
                row['missing'].append('配置不含凭据、查询参数的 service_url。')
            if cfg.get('service_ready') is not True:
                row['missing'].append('部署上游服务并完成实际调用后再声明 service_ready。')
            key = 'api_key_env' if ident == 'tabiya_livelihoods_classifier' else 'token_env'
            env_name = cfg.get(key)
            if env_name is not None and (not isinstance(env_name, str) or
                    not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env_name) or not os.environ.get(env_name)):
                row['configured'] = False
                row['missing'].append('凭据环境变量名称无效或尚未设置；不会输出凭据。')
            if ident == 'esco_skill_extractor':
                if not (cfg.get('models_ready') is True and cfg.get('llm_model') and cfg.get('embedding_model')):
                    row['configured'] = False
                    row['missing'].append('准备本地模型、嵌入模型及职业数据，配置模型名称并实测提取。')
            elif ident == 'tabiya_compass':
                row['missing'].append('准备模型和已有会话，用合成内容实测；服务可能保存会话。')
            elif ident == 'resume_matcher':
                row['missing'].append('准备模型与已有简历、岗位记录，仅实测预览，不自动保存修改。')
            else:
                row['missing'].append('准备分类模型并用公开文本实测分类结果结构。')
            if live:
                row['reachable'], code = _probe(cfg, ident)
                row['observations'].append(code)
                if code != 'http_200':
                    row['missing'].append('健康检查未返回 HTTP 200；查看检查代码并定位服务配置。')
            else:
                row['observations'].append('passive_no_network')
            row['missing'].append('确认服务资料留存与模型联网行为；外发私人资料需单独授权。')
        elif ident in DATASETS:
            row['installed'] = None  # A built-in client does not establish a local dataset.
            row['configured'] = True
            row['observations'].append('内置公共资料客户端；不代表本机已有数据或当前网络可用。')
            row['missing'].append('实际下载并核对版本、记录数与校验值；需要时准备固定版本本地副本。')
        else:
            checkout = cfg.get('checkout')
            row['installed'] = isinstance(checkout, str) and Path(checkout).is_dir()
            row['configured'] = row['installed']
            row['missing'].append('验证上游 checkout 的固定提交、干净状态、评估依赖与实际评分运行。')
            if ident == 'melo_benchmark' and (platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64')):
                row['observations'].append('pinned_trec_eval_requires_linux_x86_64')
                row['missing'].append('当前固定的 trec_eval 二进制需要 Linux x86_64 评估环境。')
        row['missing'].append('本次未执行组件业务调用，也未开展匹配效果评估。')
        if ident in previous:
            row['historical_verification'] = {
                'recorded_at': historical.get('verified_at'),
                'live_status': previous[ident].get('live_status'),
                'applies_to_current_environment': False,
            }
        rows.append(row)
    by_id = {row['id']: row for row in rows}
    profiles = {name: {**profile, 'automatically_enabled': False,
                      'missing_by_component': {ident: by_id[ident]['missing'] for ident in profile['components']}}
                for name, profile in PROFILES.items()}
    return {'schema_version': '1.0', 'checked_at': observed_at, 'mode': 'live_http_only' if live else 'passive',
            'components': rows, 'profiles': profiles,
            'note': 'null 表示未核查或不适用；reachable 仅表示 HTTP 端点有响应（包括错误响应）。'
                    'HTTP 200、历史记录或配置声明均不等于模型可用、业务调用成功或效果已评估。'}
