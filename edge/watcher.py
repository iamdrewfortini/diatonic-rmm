"""No remote execution, database credentials, job queue, or unbounded history."""
import json
import concurrent.futures
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.request

MAX_BYTES = 262144


def atomic_json(path, value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("cache exceeds budget")
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(raw)
    temporary.chmod(0o640)
    temporary.replace(path)


def cached(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("cache exceeds budget")
    data = json.loads(path.read_bytes())
    if not isinstance(data.get("snapshot"), dict) or not isinstance(data["snapshot"].get("utc"), (int, float)):
        raise ValueError("invalid cache")
    return data


class Watcher:
    def __init__(self, config):
        if not config["api"].startswith("https://"):
            raise ValueError("HTTPS required")
        if len(config.get("checks", [])) > 16:
            raise ValueError("too many checks")
        self.config = config
        self.cache = Path(config["cache"])
        self.context = ssl.create_default_context(cafile=config["ca_file"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.context), NoRedirect())
        self.cursor = None
        self.state = None
        self.last_write = 0
        try:
            self.state = cached(self.cache)
            self.cursor = self.state.get("cursor")
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def request(self, path, body=None, etag=None):
        # Read credentials each time to support atomic rotation without restarting.
        headers = {"Authorization": "Bearer "+Path(self.config["token_file"]).read_text().strip()}
        if etag:
            headers["If-None-Match"] = etag
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.config["api"]+path,
            data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with self.opener.open(req, timeout=5) as response:
                raw = response.read(MAX_BYTES+1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("API response exceeds budget")
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return None
            raise

    def check(self):
        def probe(check):
            started = time.monotonic()
            status = "down"
            try:
                with socket.create_connection((check["host"], check["port"]), timeout=3):
                    status = "up"
            except OSError:
                pass
            return dict(name=check["name"], status=status,
                latency_ms=round((time.monotonic()-started)*1000, 2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(probe, self.config.get("checks", [])))

    def cycle(self, force=False):
        status = {"checked": time.time(), "checks": self.check(), "api_ok": False}
        try:
            data = self.request("/api/wall/snapshot", etag=self.cursor)
            if data is not None:
                if not isinstance(data.get("snapshot"), dict) or not isinstance(data["snapshot"].get("utc"), (int, float)):
                    raise ValueError("invalid projection")
                self.state, self.cursor = data, data["cursor"]
            if self.state and self.config.get("live_cache"):
                atomic_json(Path(self.config["live_cache"]), self.state)
            if self.state and (force or time.monotonic()-self.last_write >= 60 or not self.cache.exists()):
                atomic_json(self.cache, self.state)
                self.last_write = time.monotonic()
            status["api_ok"] = True
            self.request("/api/observations", {"checks": status["checks"]})
        except (OSError, ValueError, KeyError, TypeError):
            # Keep only last known good state. No retry queue or outage write flood.
            status["api_ok"] = False
        status["projection_age_s"] = max(0, time.time()-self.state["snapshot"]["utc"]) if self.state else None
        atomic_json(Path(self.config["status"]), status)
        return status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward the bearer credential to a redirect target.
        return None


def main():
    config = json.loads(Path(os.environ.get("RMM_EDGE_CONFIG", "/etc/diatonic-edge/config.json")).read_text())
    watcher = Watcher(config)
    while True:
        watcher.cycle()
        time.sleep(max(10, min(300, config.get("interval", 15))))


if __name__ == "__main__":
    main()
