"""Admin CLI.

  python3 cli.py import         # build state.json from existing /etc/cloudflared/*/config.yml
  python3 cli.py set-password   # set (or reset) the manager login password
  python3 cli.py apply          # regenerate every tunnel config from state.json and restart
  python3 cli.py direct [off|lan|public|auto]   # direct HTTPS access by IP on :8443
"""
import getpass
import glob
import os
import secrets
import sys

import yaml
from werkzeug.security import generate_password_hash

from core import cf, state, system


def _zone_name(cert):
    res = cf._api(cert["apiToken"], "GET", "/zones/%s" % cert["zoneID"])
    return res["name"]


def cmd_import():
    data = state.load()
    used_ports = {a["metrics_port"] for a in data["accounts"].values()}
    panel_port = system.panel_port()
    for cfg_path in sorted(glob.glob(os.path.join(cf.CF_DIR, "*", "config.yml"))):
        account = os.path.basename(os.path.dirname(cfg_path))
        cfg = yaml.safe_load(open(cfg_path))
        cert_path = cfg.get("origincert") or os.path.join(os.path.dirname(cfg_path), "cert.pem")
        cert = cf.read_cert(cert_path)
        port = next(p for p in range(cf.METRICS_BASE_PORT, cf.METRICS_BASE_PORT + 100) if p not in used_ports)
        used_ports.add(port)
        acct = data["accounts"].setdefault(account, {})
        acct.update(tunnel_id=cfg["tunnel"],
                    tunnel_name=os.path.basename(cfg["credentials-file"])[:-5],
                    cert=cert_path, account_id=cert["accountID"])
        acct.setdefault("metrics_port", port)
        zone = _zone_name(cert)
        data["zones"][zone] = {"account": account, "cert": cert_path, "zone_id": cert["zoneID"]}
        for rule in cfg.get("ingress", []):
            host = rule.get("hostname")
            if not host or state.find_route(data, host):
                continue
            svc = rule["service"]
            route = {"hostname": host, "service": svc, "kind": "site"}
            if svc.startswith("ssh://"):
                route.update(kind="ssh", locked=True, label="SSH")
            elif svc == "https://localhost:%d" % panel_port:
                route.update(kind="panel", locked=True, label="aaPanel")
            if (rule.get("originRequest") or {}).get("noTLSVerify"):
                route["no_tls_verify"] = True
            data["routes"].append(route)
        print("imported account %-10s zone %-20s tunnel %s" % (account, zone, acct["tunnel_name"]))
    state.save(data)
    print("routes:", ", ".join(r["hostname"] for r in data["routes"]))


def cmd_set_password(pw=None):
    pw = pw or os.environ.get("OLDHOME_PASSWORD")
    if not pw:
        if sys.stdin.isatty():
            pw = getpass.getpass("New manager password: ")
        else:
            pw = secrets.token_urlsafe(12)
            print("Generated password:", pw)
    path = os.path.join(state.ETC, "manager_password.hash")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, generate_password_hash(pw).encode())
    os.close(fd)
    print("password saved")


def cmd_apply():
    data = state.load()
    for account in data["accounts"]:
        cf.apply_config(data, account)
        print("applied", account)


def cmd_direct():
    from core import direct
    m = sys.argv[2] if len(sys.argv) > 2 else "auto"
    if m == "auto":
        m = direct.default_mode()
    i = direct.set_mode(m)
    print("direct access:", i["mode"], *i["urls"]) if i["mode"] != "off" else print("direct access: off")


if __name__ == "__main__":
    cmds = {"import": cmd_import, "set-password": cmd_set_password, "apply": cmd_apply, "direct": cmd_direct}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
