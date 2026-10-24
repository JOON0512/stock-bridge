from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .core import TradingError, HTTPRejected, Uncertain, file_lock, private_json


@dataclass
class Response:
    data: dict
    headers: dict


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTP:
    """No write retry and no redirects. Credentials only go to fixed broker hosts."""
    HOSTS = {
        "https://openapi.tossinvest.com", "https://openapi.koreainvestment.com:9443",
        "https://openapivts.koreainvestment.com:29443", "https://api.kiwoom.com",
        "https://mockapi.kiwoom.com",
    }

    def __init__(self, base, runtime):
        if base not in self.HOSTS:
            raise TradingError("허용되지 않은 API 호스트")
        self.base, self.runtime = base, runtime
        # Do not inherit a proxy that might relay credentials.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.host_key = hashlib.sha256(base.encode()).hexdigest()[:12]

    def _pace(self):
        # Conservative cross-process throttle: at most one request per 1.1s per host.
        path = self.runtime / f"rate-{self.host_key}.json"
        with file_lock(path.with_suffix(".lock")):
            previous = json.loads(path.read_text())["at"] if path.exists() else 0
            time.sleep(max(0, min(1.1, 1.1 - (time.time() - previous))))
            private_json(path, {"at": time.time()})

    def request(self, method, path, *, headers=None, body=None, params=None, form=False, write=False):
        if not path.startswith("/") or path.startswith("//"):
            raise TradingError("API 경로 오류")
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        h = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json; charset=utf-8"
            data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
        for attempt in range(1 if write else 3):
            self._pace()
            try:
                req = urllib.request.Request(url, data=data, headers=h, method=method)
                with self.opener.open(req, timeout=15) as response:
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise ValueError("oversized response")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ValueError("response must be an object")
                    return Response(result, {k.lower(): v for k, v in response.headers.items()})
            except urllib.error.HTTPError as e:
                # Never echo broker request bodies, bearer tokens, or raw error responses.
                code = e.code
                if not write and code in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        wait = float(e.headers.get("Retry-After", 2 ** attempt))
                    except ValueError:
                        wait = 2 ** attempt
                    time.sleep(max(1, min(wait, 10)))
                    continue
                if write and (code >= 500 or code in (408, 409, 429) or 300 <= code < 400):
                    raise Uncertain(f"HTTP {code}: 주문 처리 여부 확인 필요") from None
                raise HTTPRejected(code) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                if not write and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                exc = Uncertain if write else TradingError
                raise exc("API 통신 또는 응답 형식 오류") from None


class TokenCache:
    def __init__(self, config, identity):
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.path = config.runtime / f"token-{config.broker}-{config.mode}-{digest}.json"

    def get(self, fetch):
        with file_lock(self.path.with_suffix(".lock")):
            if self.path.exists():
                try:
                    saved = json.loads(self.path.read_text())
                    if float(saved["expires_at"]) > time.time() + 90:
                        return saved["token"]
                except (ValueError, KeyError):
                    pass
            token, expires_at = fetch()
            if not token or expires_at <= time.time() + 90:
                raise TradingError("유효한 인증 토큰을 발급받지 못했습니다.")
            private_json(self.path, {"token": token, "expires_at": expires_at})
            return token

    def invalidate(self):
        with file_lock(self.path.with_suffix(".lock")):
            self.path.unlink(missing_ok=True)
