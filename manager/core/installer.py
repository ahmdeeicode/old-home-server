"""Install aaPanel from a pasted official command — safely.

The pasted text is NEVER executed. We only extract the official script URL
(must be https://www.aapanel.com/script/<name>.sh) and plain word arguments
(e.g. "ipssl"), then run our own wrapper with those.
"""
import os
import re
import socket
import subprocess

from . import system

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(HERE, "scripts", "install_aapanel.sh")
LOG = "/var/log/oldhome-aapanel-install.log"
STATUS = "/etc/oldhome/aapanel_install.status"
UNIT = "oldhome-aapanel-install"

URL_RE = re.compile(r"https://(?:www\.)?aapanel\.com/script/[A-Za-z0-9_.-]+\.sh")
ARG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse(command):
    urls = set(URL_RE.findall(command or ""))
    if len(urls) != 1:
        raise ValueError("لم أجد رابط سكربت aaPanel الرسمي (https://www.aapanel.com/script/...sh) في النص الملصوق")
    url = urls.pop()
    script = url.rsplit("/", 1)[1]
    m = re.search(r"bash\s+(?:\S*/)?%s([^;&|\n]*)" % re.escape(script), command)
    args = (m.group(1).split() if m else [])
    bad = [a for a in args if not ARG_RE.match(a)]
    if bad or len(args) > 5:
        raise ValueError("خيارات غير مسموحة في الأمر: %s" % " ".join(bad or args))
    return url, args


def running():
    return subprocess.run(["systemctl", "is-active", "--quiet", UNIT]).returncode == 0


def start(command):
    if os.path.isdir("/www/server/panel"):
        raise ValueError("aaPanel مثبّت مسبقاً على هذا الخادم")
    if running():
        raise ValueError("التثبيت جارٍ بالفعل")
    url, args = parse(command)
    os.makedirs("/etc/oldhome", mode=0o700, exist_ok=True)
    with open(LOG, "w") as f:
        f.write("")
    subprocess.run(["systemd-run", "--unit", UNIT, "--collect", "--quiet",
                    "/bin/bash", WRAPPER, url] + args, check=True)
    return {"url": url, "args": args}


def _log_tail(n=120):
    try:
        with open(LOG, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    return "\n".join(ANSI_RE.sub("", l) for l in lines[-n:])


def _credentials(text):
    """Pull the login block the official installer prints at the end."""
    out = {}
    for line in text.splitlines():
        l = line.strip()
        m = re.match(r"(?i)username:\s*(\S+)", l)
        if m:
            out["username"] = m.group(1)
        m = re.match(r"(?i)password:\s*(\S+)", l)
        if m:
            out["password"] = m.group(1)
    return out


def status():
    installed = os.path.isdir("/www/server/panel")
    info = {"installed": installed}
    if installed:
        port = system.panel_port()
        path = system.panel_admin_path()
        try:
            lan = socket.gethostbyname(socket.gethostname())
            if lan.startswith("127."):
                lan = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
        except Exception:
            lan = "<ip>"
        ver = ""
        try:
            m = re.search(r"g\.version\s*=\s*'([^']+)'", open("/www/server/panel/class/common.py").read())
            ver = m.group(1) if m else ""
        except OSError:
            pass
        info.update(port=port, admin_path=path, version=ver, lan_url="https://%s:%s%s" % (lan, port, path))
    try:
        job_state = open(STATUS).read().strip()
    except OSError:
        job_state = "none"
    if job_state == "running" and not running():
        job_state = "done" if installed else "failed"
    tail = _log_tail() if job_state != "none" else ""
    job = {"state": job_state, "log": tail}
    if job_state == "done":
        job["credentials"] = _credentials(_log_tail(400))
    return {"aapanel": info, "job": job}
