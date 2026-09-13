"""Public, explicitly scoped source collection; no search snippets as JD evidence."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from .common import AdapterError, http_bytes


class PageText(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url, self.parts, self.links, self.ignore = url, [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.ignore += 1
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'tr'):
            self.parts.append('\n')
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                link = urljoin(self.url, href)
                parsed = urlsplit(link)
                if parsed.scheme == 'https' and not parsed.username and not parsed.password:
                    self.links.append(link)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg') and self.ignore:
            self.ignore -= 1

    def handle_data(self, data):
        if not self.ignore and data.strip():
            self.parts.append(data.strip() + ' ')


def fetch(spec, config, cache, *, force=False):
    url = spec['url']
    hosts = config.get('allowed_source_hosts', [])
    if urlsplit(url).hostname not in hosts:
        raise AdapterError('source_not_allowed', 'Source hostname is not in the explicit allowed_source_hosts list')
    if config.get('source_terms_accepted') is not True:
        raise AdapterError('terms_confirmation_required', 'Confirm the allowed public-source collection scope first')
    key = cache.key('public_source', '1', {'url': url, 'allowed_hosts': sorted(hosts)})
    if not force:
        cached = cache.get(key)
        if cached is not None:
            if len(cached['text']) > config.get('max_source_chars', 100000):
                raise AdapterError('source_too_large', 'Cached source exceeds the current model scope')
            return {**cached, 'cache_hit': True}
    origin = urlsplit(url)
    robots_url = origin.scheme + '://' + origin.netloc + '/robots.txt'
    robots_key = cache.key('robots', '1', {'url': robots_url})
    robots_text = cache.get(robots_key)
    if robots_text is None:
        try:
            raw, _ = http_bytes(robots_url, timeout=20, max_bytes=500_000, allowed_hosts=hosts)
            robots_text = raw.decode('utf-8', errors='replace')
        except AdapterError as exc:
            if exc.code == 'http_404':
                robots_text = ''
            else:
                raise AdapterError('robots_unavailable', 'Cannot check robots policy; source left uncollected') from None
        cache.put(robots_key, robots_text, ttl=3600)
    robots = RobotFileParser()
    robots.parse(robots_text.splitlines())
    if not robots.can_fetch('JobMatchEvidence', url):
        raise AdapterError('robots_denied', 'Robots policy disallows this source')
    raw, meta = http_bytes(url, timeout=30, max_bytes=2_000_000, allowed_hosts=hosts)
    if urlsplit(meta['url']).hostname not in hosts:
        raise AdapterError('redirect_scope', 'Redirected source leaves the configured source scope')
    if 'html' not in meta['content_type'] and not meta['content_type'].startswith('text/'):
        raise AdapterError('unsupported_source', 'Use a document adapter for non-text sources')
    text = raw.decode('utf-8', errors='replace')
    parser = PageText(meta['url'])
    if 'html' in meta['content_type']:
        parser.feed(text)
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    if len(text) < 80:
        raise AdapterError('incomplete_source', 'Too little readable text; no complete JD inferred')
    if len(text) > config.get('max_source_chars', 100000):
        raise AdapterError('source_too_large', 'Source exceeds model scope; isolate a specific JD first')
    result = {**meta, 'text': text, 'links': sorted(set(parser.links)), 'cache_hit': False,
              'execution': 'real_http_fetch'}
    cache.put(key, result, ttl=min(config.get('source_cache_seconds', 3600), 86400))
    return result
