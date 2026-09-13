"""Bounded network, private atomic files and reproducible hashes."""
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class AdapterError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise AdapterError("unsafe_path", "Directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def atomic_json(path, value, *, overwrite=False):
    path = Path(path)
    private_dir(path.parent)
    if path.is_symlink() or (path.exists() and not overwrite):
        raise AdapterError("output_exists", "Refusing to overwrite existing output")
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(name, path)
        else:
            os.link(name, path)  # Atomic no-clobber publication.
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_url(url, allow_local=False):
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise ValueError()
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
            if not ip.is_global and not (allow_local and ip.is_loopback):
                raise AdapterError("unsafe_url", "Private, reserved or link-local network target rejected")
        if parts.scheme != "https" and not (allow_local and all(
                ipaddress.ip_address(a[4][0]).is_loopback for a in addresses)):
            raise AdapterError("unsafe_url", "HTTPS required except explicitly configured loopback services")
        return url
    except AdapterError:
        raise
    except (ValueError, TypeError, OSError):
        raise AdapterError("invalid_url", "URL or DNS resolution is invalid") from None


def http_bytes(url, payload=None, headers=None, timeout=30, allow_local=False, max_bytes=10_000_000, allowed_hosts=None):
    def check_scope(target):
        if allowed_hosts is not None and urlsplit(target).hostname not in allowed_hosts:
            raise AdapterError('redirect_scope', 'Request leaves the configured source scope')
    check_scope(url)
    validate_url(url, allow_local)
    origin = urlsplit(url).netloc

    class Guard(urllib.request.HTTPRedirectHandler):
        max_redirections = 4

        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            check_scope(newurl)
            validate_url(newurl, allow_local)
            if payload is not None or any(k.lower() in ("authorization", "cookie", "x-api-key") for k in (headers or {})):
                raise AdapterError("redirect_rejected", "Credentialed or POST redirects are not followed")
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    request_headers = {"User-Agent": "JobMatchEvidence/0.1 (+manual-review-required)", **(headers or {})}
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=request_headers)
    try:
        handlers = [Guard()]
        if allow_local and urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'):
            handlers.append(urllib.request.ProxyHandler({}))
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise AdapterError("response_too_large", "Response exceeded configured size limit")
            return raw, {"url": response.url, "content_type": response.headers.get("Content-Type", ""),
                         "status": response.status, "captured_at": utcnow()}
    except AdapterError:
        raise
    except urllib.error.HTTPError as exc:
        raise AdapterError("http_" + str(exc.code), "HTTP request rejected; vacancy status remains unknown") from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise AdapterError("network_error", "Request failed or timed out; no source status inferred") from None


def http_json(url, payload=None, headers=None, timeout=30, allow_local=False):
    raw, _ = http_bytes(url, payload, headers, timeout, allow_local)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise AdapterError("invalid_response", "Upstream response is not valid JSON") from None
