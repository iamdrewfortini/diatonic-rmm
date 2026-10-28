"""Bounded, allowlisted wall projections and a small RESP2 Valkey adapter."""
import hashlib
import json
import math
import socket
import time

MAX_BYTES = 262144
FIELDS = {"id", "type", "node", "name", "status", "vmid", "template", "cpu", "maxcpu",
          "mem", "maxmem", "disk", "maxdisk", "storage", "plugintype", "networks", "storages",
          "role", "address", "description", "uptime", "content", "shared", "active"}


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def wall_projection(body):
    if not isinstance(body, dict):
        raise ValueError("JSON object required")
    result = {}
    for kind in ("resources", "guests", "metals"):
        items = body.get(kind)
        if not isinstance(items, list) or len(items) > 256:
            raise ValueError("bounded topology lists required")
        result[kind] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("topology objects required")
            clean = {k: v for k, v in item.items() if k in FIELDS}
            for value in clean.values():
                if isinstance(value, (dict, list)):
                    if not isinstance(value, list) or len(value) > 32 or any(not isinstance(x, str) or len(x) > 128 for x in value):
                        raise ValueError("invalid topology list")
                elif not isinstance(value, (str, int, float, bool, type(None))) or isinstance(value, str) and len(value) > 512:
                    raise ValueError("invalid topology field")
            if kind == "guests":
                clean.update(controllable=False)
                clean.setdefault("networks", [])
                clean.setdefault("storages", [])
            result[kind].append(clean)
    utc = body.get("utc")
    if type(utc) not in (int, float) or not math.isfinite(utc) or not time.time()-60 <= utc <= time.time()+5:
        raise ValueError("fresh collector timestamp required")
    result.update(utc=utc, allowed_actions=[], allowed_guests=[], source="Authenticated observer API")
    if len(encode(result)) > MAX_BYTES - 4096:
        raise ValueError("projection too large")
    return result


def observation(body, expected_host=None):
    if not isinstance(body, dict) or not isinstance(body.get("checks"), list) or len(body["checks"]) > 16:
        raise ValueError("bounded checks required")
    checks = []
    for check in body["checks"]:
        if not isinstance(check, dict) or check.get("status") not in ("up", "down"):
            raise ValueError("invalid check")
        name, latency = check.get("name"), check.get("latency_ms")
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or type(latency) not in (int, float) or not math.isfinite(latency) or not 0 <= latency <= 10000:
            raise ValueError("invalid check fields")
        checks.append(dict(name=name, status=check["status"], latency_ms=latency))
    return {"checks": checks, "received": time.time()}


class Valkey:
    """Only AUTH, GET and SET EX; independent ACL enforcement on the server."""
    def __init__(self, password, host="127.0.0.1", port=6379, user="diatonic-rmm"):
        self.password, self.host, self.port, self.user = password, host, port, user

    @staticmethod
    def reply(stream):
        line = stream.readline(4096)
        if not line.endswith(b"\r\n"):
            raise OSError("invalid Valkey response")
        if line[:1] == b"+":
            return line[1:-2]
        if line[:1] == b"-":
            raise OSError("Valkey rejected operation")
        if line[:1] == b"$":
            length = int(line[1:-2])
            if length == -1:
                return None
            if not 0 <= length <= MAX_BYTES:
                raise OSError("Valkey response too large")
            data = stream.read(length)
            if len(data) != length or stream.read(2) != b"\r\n":
                raise OSError("truncated Valkey response")
            return data
        raise OSError("unexpected Valkey response")

    def command(self, *args):
        def packet(parts):
            encoded = [p if isinstance(p, bytes) else str(p).encode() for p in parts]
            return b"*%d\r\n" % len(encoded) + b"".join(b"$%d\r\n" % len(p)+p+b"\r\n" for p in encoded)
        with socket.create_connection((self.host, self.port), timeout=3) as sock:
            with sock.makefile("rb") as stream:
                sock.sendall(packet(("AUTH", self.user, self.password)))
                self.reply(stream)
                sock.sendall(packet(args))
                return self.reply(stream)

    def put(self, tenant, slot, value, ttl=90):
        body = encode(value)
        if len(body) > MAX_BYTES:
            raise ValueError("projection too large")
        self.command("SET", f"diatonic:rmm:{tenant}:{slot}", body, "EX", ttl)

    def get(self, tenant, slot):
        data = self.command("GET", f"diatonic:rmm:{tenant}:{slot}")
        return json.loads(data) if data is not None else None


def cursor(snapshot):
    return '"'+hashlib.sha256(encode(snapshot)).hexdigest()+'"'
