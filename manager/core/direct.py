"""Direct access to the manager by server IP: https://<ip>:8443

A second gunicorn (oldhome-manager-direct) listens on 0.0.0.0:8443 with a
self-signed certificate and OLDHOME_DIRECT=1; app.access_guard() then filters
requests by mode:
  off    — listener stopped (SSH / Cloudflare Access only)
  lan    — only private-network clients (10/8, 172.16/12, 192.168/16, 100.64/10)
  public — anyone (cloud servers); login is throttled (5 tries / 15 min)
"""
import ipaddress
import json
import os
import socket
import subprocess

ETC = "/etc/oldhome"
CONF = os.path.join(ETC, "direct.json")
TLS = os.path.join(ETC, "tls")
UNIT = "oldhome-manager-direct"
PORT = int(os.environ.get("OLDHOME_DIRECT_PORT", "8443"))
MODES = ("off", "lan", "public")
LAN_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"]
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mode():
    try:
        m = json.load(open(CONF)).get("mode", "off")
    except (OSError, ValueError):
        m = "off"
    return m if m in MODES else "off"


def is_private(ip):
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or any(a in ipaddress.ip_network(n) for n in LAN_NETS) or \
        (a.version == 6 and (a.is_link_local or a in ipaddress.ip_network("fc00::/7")))


def server_ips():
    out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    return [ip for ip in out if ":" not in ip]


def default_mode():
    ips = server_ips()
    return "lan" if ips and all(is_private(ip) for ip in ips) else "public"


def _ensure_cert():
    cert, key = os.path.join(TLS, "cert.pem"), os.path.join(TLS, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(TLS, mode=0o700, exist_ok=True)
    host = socket.gethostname()
    san = ",".join(["DNS:%s" % host, "DNS:localhost"] + ["IP:%s" % ip for ip in server_ips()] + ["IP:127.0.0.1"])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                    "-subj", "/CN=%s" % host, "-addext", "subjectAltName=%s" % san,
                    "-keyout", key, "-out", cert], check=True, capture_output=True)
    os.chmod(key, 0o600)
    return cert, key


def _write_unit(cert, key):
    unit = """[Unit]
Description=Old-Home Manager (direct HTTPS on :%(port)d)
After=network-online.target

[Service]
WorkingDirectory=%(dir)s
Environment=OLDHOME_DIRECT=1
ExecStart=/usr/bin/gunicorn --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:%(port)d --certfile %(cert)s --keyfile %(key)s app:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
""" % {"port": PORT, "dir": HERE, "cert": cert, "key": key}
    with open("/etc/systemd/system/%s.service" % UNIT, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def _ufw_present():
    return subprocess.run(["which", "ufw"], capture_output=True).returncode == 0


def _ufw(m):
    # Add rules even while ufw is inactive: they are stored and take effect the
    # moment something enables it (the aaPanel installer does exactly that).
    if not _ufw_present():
        return
    rules = [["allow", "from", n, "to", "any", "port", str(PORT), "proto", "tcp"] for n in LAN_NETS]
    public = ["allow", "%d/tcp" % PORT]
    for r in rules + [public]:
        subprocess.run(["ufw", "--force", "delete"] + r, capture_output=True)
    for r in (rules if m == "lan" else [public] if m == "public" else []):
        subprocess.run(["ufw"] + r, capture_output=True)


def set_mode(m):
    if m not in MODES:
        raise ValueError("وضع غير معروف")
    os.makedirs(ETC, mode=0o700, exist_ok=True)
    with open(CONF, "w") as f:
        json.dump({"mode": m}, f)
    # --no-block: this request may itself be served by the direct listener;
    # queue the restart/stop so the response reaches the browser first.
    if m == "off":
        subprocess.run(["systemctl", "disable", UNIT], capture_output=True)
        subprocess.run(["systemctl", "--no-block", "stop", UNIT], capture_output=True)
    else:
        cert, key = _ensure_cert()
        _write_unit(cert, key)
        subprocess.run(["systemctl", "enable", UNIT], capture_output=True)
        subprocess.run(["systemctl", "--no-block", "restart", UNIT], check=True)
    _ufw(m)
    out = info()
    out["active"] = m != "off"   # the queued job hasn't run yet
    return out


def info():
    active = subprocess.run(["systemctl", "is-active", "--quiet", UNIT]).returncode == 0
    return {"mode": mode(), "active": active, "port": PORT,
            "urls": ["https://%s:%d" % (ip, PORT) for ip in server_ips()]}
