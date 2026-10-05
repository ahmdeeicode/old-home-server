"""Cloudflare Access guard for exposing the manager through the tunnel.

1. probe(): before any ingress exists, request the hostname anonymously and
   require Cloudflare to bounce us to <team>.cloudflareaccess.com. The login
   URL also reveals the team domain and the application AUD (kid=...).
2. verify_request(): every request that did not come in via localhost must
   carry a valid Cf-Access-Jwt-Assertion signed by that team for that AUD.
   If the Access app is ever removed, the manager refuses tunnel traffic.
"""
import json
import subprocess
import time
import urllib.parse
import urllib.request

import jwt

_jwks = {}  # team -> (PyJWKClient, created)


def _resolve(name):
    req = urllib.request.Request("https://cloudflare-dns.com/dns-query?name=%s&type=A" % urllib.parse.quote(name),
                                 headers={"Accept": "application/dns-json"})
    ans = json.loads(urllib.request.urlopen(req, timeout=10).read()).get("Answer", [])
    return [a["data"] for a in ans if a.get("type") == 1]


def _edge_ip(hostname, neighbours=()):
    """Cloudflare's customer edge routes by SNI/Host, so borrow the address of
    an already-live proxied name in the same zone instead of resolving the
    brand-new one (resolvers would cache its not-yet-existing answer)."""
    for name in neighbours:
        try:
            ips = _resolve(name)
        except Exception:
            ips = []
        if ips:
            return ips[0]
    return None


def probe(hostname, neighbours=(), attempts=8):
    """Return {"protected": bool, "team", "aud", "detail"} for https://hostname/.
    neighbours: live proxied names in the same zone (used only for their IP)."""
    last = "لا يوجد رد"
    for _ in range(attempts):
        ip = _edge_ip(hostname, neighbours)
        if ip:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}", "--max-time", "10",
                 "-A", "Mozilla/5.0 old-home-access-probe", "--resolve", "%s:443:%s" % (hostname, ip),
                 "https://%s/" % hostname], capture_output=True, text=True)
            code, _, loc = r.stdout.partition(" ")
            u = urllib.parse.urlparse(loc)
            if code in ("301", "302", "303", "307") and u.hostname and u.hostname.endswith(".cloudflareaccess.com"):
                aud = urllib.parse.parse_qs(u.query).get("kid", [""])[0]
                return {"protected": True, "team": u.hostname, "aud": aud}
            last = "رد Cloudflare: %s %s" % (code, loc or "(بدون تحويل لصفحة الدخول)")
        else:
            last = "تعذّر إيجاد عنوان Cloudflare لهذا الدومين"
        time.sleep(3)
    return {"protected": False, "detail": last}


def _client(team):
    c = _jwks.get(team)
    if not c or time.time() - c[1] > 3600:
        c = (jwt.PyJWKClient("https://%s/cdn-cgi/access/certs" % team), time.time())
        _jwks[team] = c
    return c[0]


def verify(token, team, aud):
    key = _client(team).get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256"], audience=aud, issuer="https://" + team)
