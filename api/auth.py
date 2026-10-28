"""Revocable, expiring service identities; HTTP identity headers are ignored."""
import hashlib
import json
import math
from pathlib import Path
import re
import time


class AuthError(Exception):
    pass


class Identities:
    def __init__(self, path):
        self.path = Path(path)

    def authenticate(self, authorization):
        if not authorization or not re.fullmatch(r"Bearer [A-Za-z0-9_-]{40,128}", authorization):
            raise AuthError()
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        try:
            # Atomic registry replacement revokes on the very next request.
            registry = json.loads(self.path.read_text())
            principal = registry.get(digest) if isinstance(registry, dict) else None
            if not isinstance(principal, dict) or principal.get("revoked", False):
                raise AuthError()
            expiry = principal.get("expires")
            if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time():
                raise AuthError()
            for key in ("tenant", "actor"):
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", principal[key]):
                    raise AuthError()
            if principal["role"] not in {"observer", "collector", "technician", "approver"}:
                raise AuthError()
            return principal
        except (OSError, ValueError, TypeError, KeyError):
            raise AuthError() from None
