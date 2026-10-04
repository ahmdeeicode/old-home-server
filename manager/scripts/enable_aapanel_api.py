"""Enable the aaPanel API for localhost only and store the key for the manager.

Must run with aaPanel's own interpreter:  btpython enable_aapanel_api.py
Idempotent: keeps an existing key if /etc/oldhome/aapanel_api.json is valid.
"""
import json
import os
import shutil
import sys
import time

PANEL = "/www/server/panel"
CRED = "/etc/oldhome/aapanel_api.json"

sys.path.insert(0, os.path.join(PANEL, "class"))
os.chdir(PANEL)
import public  # noqa: E402  (aaPanel module)

cfg_path = os.path.join(PANEL, "config", "api.json")
data = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {"open": False, "token": "", "limit_addr": []}
port = open(os.path.join(PANEL, "data", "port.pl")).read().strip()

sk = None
if os.path.exists(CRED) and data.get("open") and data.get("token"):
    sk = json.load(open(CRED)).get("key")
    if sk and public.md5(sk) != data["token"]:
        sk = None

if not sk:
    if os.path.exists(cfg_path):
        shutil.copy2(cfg_path, "%s.bak-%d" % (cfg_path, time.time()))
    sk = public.GetRandomString(32)
    data["token"] = public.md5(sk)
    data["token_crypt"] = public.en_crypt(data["token"], sk).decode("utf-8")

data["open"] = True
if "127.0.0.1" not in data.get("limit_addr", []):
    data["limit_addr"] = list(data.get("limit_addr", [])) + ["127.0.0.1"]
json.dump(data, open(cfg_path, "w"))

os.makedirs(os.path.dirname(CRED), mode=0o700, exist_ok=True)
fd = os.open(CRED, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps({"url": "https://127.0.0.1:%s" % port, "key": sk}).encode())
os.close(fd)
print("aaPanel API enabled (127.0.0.1 only), port", port)
