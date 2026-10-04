"""aaPanel HTTP API client (API is restricted to 127.0.0.1)."""
import hashlib
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

CRED_FILE = "/etc/oldhome/aapanel_api.json"
PHP_DIR = "/www/server/php"
_CTX = ssl._create_unverified_context()  # panel uses a self-signed cert on localhost


class PanelError(Exception):
    pass


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def call(endpoint, **params):
    with open(CRED_FILE) as f:
        cred = json.load(f)
    t = str(int(time.time()))
    params.update(request_time=t, request_token=_md5(t + _md5(cred["key"])))
    body = urllib.parse.urlencode(params).encode()
    raw = urllib.request.urlopen(urllib.request.Request(cred["url"] + endpoint, data=body),
                                 context=_CTX, timeout=60).read()
    try:
        res = json.loads(raw)
    except ValueError:
        raise PanelError("رد غير متوقع من aaPanel")
    if isinstance(res, dict) and res.get("status") is False:
        raise PanelError(res.get("msg") or "aaPanel رفض الطلب")
    return res


def list_sites():
    res = call("/data?action=getData", table="sites", limit=500, p=1)
    return [{"id": s["id"], "name": s["name"], "path": s["path"],
             "status": str(s.get("status")) == "1", "php": s.get("php_version", ""),
             "ps": s.get("ps", "")} for s in res.get("data", [])]


def php_versions():
    if not os.path.isdir(PHP_DIR):
        return []
    return sorted((v for v in os.listdir(PHP_DIR) if re.fullmatch(r"\d{2}", v)), reverse=True)


def add_site(domain, php="", with_db=False, note=""):
    php = php or (php_versions() or ["00"])[0]
    params = dict(
        webname=json.dumps({"domain": domain, "domainlist": [], "count": 0}),
        path="/www/wwwroot/" + domain, type_id=0, type="PHP", version=php,
        port=80, ps=note or domain, ftp="false", codeing="utf8",
        sql="MySQL" if with_db else "false",
    )
    if with_db:
        base = re.sub(r"[^a-z0-9]", "_", domain.lower())[:16].strip("_")
        params.update(datauser=base, datapassword=_md5(str(time.time()) + domain)[:16])
    res = call("/site?action=AddSite", **params)
    if not res.get("siteStatus"):
        raise PanelError(res.get("msg") or "فشل إنشاء الموقع في aaPanel")
    out = {"id": res.get("siteId")}
    if with_db and res.get("databaseStatus"):
        out["db"] = {"name": res.get("databaseUser"), "user": res.get("databaseUser"),
                     "password": res.get("databasePass")}
    return out


def delete_site(site_id, domain, delete_files=False, delete_db=False):
    params = dict(id=site_id, webname=domain)
    if delete_files:
        params["path"] = 1
    if delete_db:
        params["database"] = 1
    return call("/site?action=DeleteSite", **params)
