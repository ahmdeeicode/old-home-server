"""Persistent state: /etc/oldhome/state.json is the single source of truth.

Tunnel config.yml files are generated from it, never edited by hand.
LOCK is held across load→modify→save; it is re-entrant within a thread and
also takes an flock, because the localhost and the direct-HTTPS managers are
two separate processes sharing this file.
"""
import fcntl
import json
import os
import threading

ETC = "/etc/oldhome"
STATE_FILE = os.path.join(ETC, "state.json")
LOCK_FILE = os.path.join(ETC, ".state.lock")

DEFAULT = {"accounts": {}, "zones": {}, "routes": []}


class _Lock:
    def __init__(self):
        self._t = threading.RLock()
        self._depth = 0
        self._fd = None

    def __enter__(self):
        self._t.acquire()
        if self._depth == 0:
            os.makedirs(ETC, mode=0o700, exist_ok=True)
            self._fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        self._depth += 1
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        if self._depth == 0:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        self._t.release()


LOCK = _Lock()


def load():
    with LOCK:
        if not os.path.exists(STATE_FILE):
            return json.loads(json.dumps(DEFAULT))
        with open(STATE_FILE) as f:
            data = json.load(f)
        for k, v in DEFAULT.items():
            data.setdefault(k, type(v)())
        return data


def save(data):
    with LOCK:
        os.makedirs(ETC, mode=0o700, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


def zone_for(data, hostname):
    """Longest zone suffix that matches hostname, or None."""
    hostname = hostname.lower().rstrip(".")
    best = None
    for zone in data["zones"]:
        if hostname == zone or hostname.endswith("." + zone):
            if best is None or len(zone) > len(best):
                best = zone
    return best


def find_route(data, hostname):
    for r in data["routes"]:
        if r["hostname"] == hostname:
            return r
    return None
