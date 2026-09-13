"""Content-addressed private cache; cache hits never refresh capture times."""
import json
import time
from pathlib import Path
from .common import AdapterError, atomic_json, fingerprint, private_dir


class Cache:
    def __init__(self, directory, *, private_enabled=False, clock=time.time):
        self.directory = private_dir(directory)
        self.private_enabled = private_enabled
        self.clock = clock
        self.hits = self.misses = self.writes = 0

    @staticmethod
    def key(component, version, payload):
        return fingerprint({"component": component, "version": version, "input": payload})

    def get(self, key, *, private=False):
        if private and not self.private_enabled:
            self.misses += 1
            return None
        path = self._path(key)
        try:
            if path.is_symlink():
                raise ValueError()
            item = json.loads(path.read_text(encoding="utf-8"))
            if item.get("private", True) and not self.private_enabled:
                raise ValueError()
            if item["key"] != key or item["expires_at"] <= self.clock() or item["created_at"] > self.clock():
                raise ValueError()
            if item["value_sha256"] != fingerprint(item["value"]):
                raise ValueError()
            self.hits += 1
            return item["value"]
        except (OSError, ValueError, KeyError, TypeError):
            self.misses += 1
            return None

    def put(self, key, value, *, ttl=3600, private=False):
        if private and not self.private_enabled:
            return
        if not isinstance(ttl, (int, float)) or not 0 < ttl <= 366 * 86400:
            raise AdapterError("invalid_ttl", "Cache TTL must be positive and at most one year")
        now = self.clock()
        item = {"key": key, "private": private, "created_at": now, "expires_at": now + ttl,
                "value": value, "value_sha256": fingerprint(value)}
        atomic_json(self._path(key), item, overwrite=True)
        self.writes += 1

    def _path(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise AdapterError("invalid_cache_key", "Invalid cache key")
        return self.directory / (key + ".json")

    def prune(self):
        removed = 0
        for path in self.directory.glob("*.json"):
            if path.is_symlink() or len(path.stem) != 64 or any(c not in "0123456789abcdef" for c in path.stem):
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                expired = item["expires_at"] <= self.clock()
            except (OSError, ValueError, KeyError, TypeError):
                expired = True
            if expired:
                path.unlink()
                removed += 1
        return {"removed_cache_entries": removed}

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "writes": self.writes}
