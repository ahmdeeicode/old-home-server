"""Self-update from GitHub: version info + one-click update from the UI.

The update runs in its own transient unit (oldhome-update) so it survives
install.sh restarting the manager it was launched from.
"""
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "manager", "scripts", "update.sh")
LOG = "/var/log/oldhome-update.log"
STATUS = "/etc/oldhome/update.status"
UNIT = "oldhome-update"
_remote = {"at": 0, "sha": None}


def _git(*args):
    r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else ""


def version():
    try:
        v = open(os.path.join(ROOT, "VERSION")).read().strip()
    except OSError:
        v = "?"
    head = _git("rev-parse", "HEAD")
    if time.time() - _remote["at"] > 600:          # ask GitHub at most every 10 min
        out = _git("ls-remote", "origin", "refs/heads/main")
        _remote.update(at=time.time(), sha=out.split()[0] if out else None)
    remote = _remote["sha"]
    return {"version": v, "commit": head[:7], "date": _git("log", "-1", "--format=%cs"),
            "remote": (remote or "")[:7], "update_available": bool(remote and head and remote != head)}


def running():
    return subprocess.run(["systemctl", "is-active", "--quiet", UNIT]).returncode == 0


def start():
    if running():
        raise ValueError("التحديث جارٍ بالفعل")
    with open(LOG, "w"):
        pass
    subprocess.run(["systemd-run", "--unit", UNIT, "--collect", "--quiet", "--property=KillMode=process",
                    "/bin/bash", SCRIPT], check=True)
    _remote["at"] = 0
    return {"ok": True}


def status():
    try:
        st = open(STATUS).read().strip()
    except OSError:
        st = "none"
    if st == "running" and not running():
        st = "failed"
    try:
        log = open(LOG, errors="replace").read()[-6000:]
    except OSError:
        log = ""
    return {"state": st, "log": log}
