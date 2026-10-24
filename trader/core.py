from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo


class TradingError(Exception):
    pass


class Rejected(TradingError):
    """Broker explicitly rejected a request, or a local validation failed."""


class Uncertain(TradingError):
    """A write may have reached the broker. Never resend automatically."""


class HTTPRejected(Rejected):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}: 키·허용 IP·계좌·주문 조건을 확인하세요.")


def decimal(value):
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise TradingError("숫자 형식이 올바르지 않습니다.") from None
    if not result.is_finite():
        raise TradingError("NaN/Infinity는 사용할 수 없습니다.")
    return result


def stamp():
    return datetime.now(timezone.utc).isoformat()


def parsed_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise TradingError("시각에는 시간대가 필요합니다. 예: 2026-09-21T09:10:00+09:00") from None


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "a+b") as f:
        os.chmod(path, 0o600)
        if os.name == "nt":
            import msvcrt
            if f.seek(0, 2) == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def load_env(path):
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise TradingError(".env 형식 오류")
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Intent:
    client_id: str
    market: str
    symbol: str
    side: str
    quantity: int
    price: str
    not_before: str
    expires_at: str
    trigger: dict | None = None

    @property
    def currency(self):
        return "KRW" if self.market == "KR" else "USD"

    @property
    def notional(self):
        return decimal(self.price) * self.quantity

    @property
    def payload(self):
        return asdict(self)

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", self.client_id):
            raise TradingError("client_id는 영숫자·-·_ 1~36자입니다.")
        if self.market not in ("KR", "US") or self.side not in ("BUY", "SELL"):
            raise TradingError("market=KR/US, side=BUY/SELL만 지원합니다.")
        pattern = r"[A-Z0-9]{6}" if self.market == "KR" else r"[A-Z][A-Z0-9.\-]{0,14}"
        if not re.fullmatch(pattern, self.symbol):
            raise TradingError("종목코드 형식 오류")
        if type(self.quantity) is not int or not 1 <= self.quantity <= 1_000_000:
            raise TradingError("수량은 양의 정수여야 합니다.")
        if not isinstance(self.price, str) or not re.fullmatch(r"\d+(\.\d+)?", self.price):
            raise TradingError("가격은 양의 십진수 문자열로 입력하세요.")
        p = decimal(self.price)
        if p <= 0 or p > 1_000_000_000:
            raise TradingError("지정가 범위 오류")
        if self.market == "KR" and p != p.to_integral_value():
            raise TradingError("국내 가격은 정수 원 단위입니다.")
        if self.market == "US" and p != p.quantize(Decimal("0.01") if p >= 1 else Decimal("0.0001")):
            raise TradingError("미국 호가 자릿수 오류 ($1 이상 2자리, 미만 4자리)")
        start, end = parsed_time(self.not_before), parsed_time(self.expires_at)
        if not 0 < (end - start).total_seconds() <= 86400:
            raise TradingError("주문 유효기간은 0초 초과, 최대 24시간입니다.")
        if self.trigger is not None:
            if set(self.trigger) != {"op", "price"} or self.trigger["op"] not in ("lte", "gte") or decimal(self.trigger["price"]) <= 0:
                raise TradingError("trigger는 {op: lte 또는 gte, price: 양수} 형식입니다.")
        return self


def read_intents(path):
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, list) or len(data) > 100:
            raise TradingError("큐는 최대 100건의 JSON 배열입니다.")
        result = [Intent(**item).validate() for item in data]
        if len({i.client_id for i in result}) != len(result):
            raise TradingError("큐 안의 client_id가 중복됩니다.")
        return result
    except (TypeError, KeyError, json.JSONDecodeError) as e:
        raise TradingError("주문 JSON 구조가 올바르지 않습니다.") from e


class Config:
    def __init__(self, path, profile):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        load_env(self.root / ".env")
        self.data = json.loads(self.path.read_text())
        if profile not in self.data["profiles"]:
            raise TradingError("설정에 없는 프로필입니다.")
        self.name = profile
        self.profile = self.data["profiles"][profile]
        self.broker = self.profile["broker"]
        self.mode = self.profile["mode"]
        if self.broker not in ("paper", "toss", "kis", "kiwoom") or self.mode not in ("paper", "sandbox", "live"):
            raise TradingError("지원하지 않는 증권사/모드입니다.")
        if (self.broker == "paper") != (self.mode == "paper") or (self.broker == "toss" and self.mode != "live"):
            raise TradingError("모의/실전 환경 조합 오류. 토스 sandbox는 구현하지 않았습니다.")
        self.runtime = self.root / self.data.get("runtime_dir", "runtime")
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limits = self.profile["limits"]
        prefix = self.profile.get("env_prefix", "")
        identity = self.secret("ACCOUNT", optional=True).replace("-", "")
        self.scope = hashlib.sha256(f"{self.broker}:{self.mode}:{identity or prefix or profile}".encode()).hexdigest()[:24]

    def secret(self, name, optional=False):
        key = f"{self.profile.get('env_prefix', '').rstrip('_')}_{name}"
        value = os.environ.get(key, "").strip()
        if not value and not optional:
            raise TradingError(f"설정 필요: {key} (.env에 직접 입력)")
        return value

    def enabled(self, confirm_live=False):
        if self.mode == "live" and (self.profile.get("live_enabled") is not True or not confirm_live):
            raise TradingError("실전은 live_enabled=true와 --confirm-live가 모두 필요합니다.")


class Journal:
    def __init__(self, config):
        self.config = config
        self.path = config.runtime / "orders.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=30)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
          scope TEXT, client_id TEXT, payload TEXT, state TEXT,
          broker_id TEXT, detail TEXT, created_at TEXT, updated_at TEXT,
          currency TEXT, notional TEXT, PRIMARY KEY(scope, client_id));
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY, scope TEXT, client_id TEXT, at TEXT, state TEXT, detail TEXT);
        """)

    def get(self, client_id):
        row = self.db.execute("SELECT * FROM orders WHERE scope=? AND client_id=?", (self.config.scope, client_id)).fetchone()
        return dict(row) if row else None

    def rows(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM orders WHERE scope=? ORDER BY created_at DESC", (self.config.scope,))]

    def reserve(self, intent):
        self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)", (self.config.scope, intent.client_id, json.dumps(intent.payload, sort_keys=True), "SUBMITTING", None, "{}", stamp(), stamp(), intent.currency, str(intent.notional)))
        self.db.commit()
        self.update(intent.client_id, "SUBMITTING", {})

    def update(self, client_id, state, detail, broker_id=None):
        encoded = json.dumps(detail, ensure_ascii=False)
        with self.db:
            self.db.execute("UPDATE orders SET state=?,detail=?,updated_at=?,broker_id=COALESCE(?,broker_id) WHERE scope=? AND client_id=?", (state, encoded, stamp(), broker_id, self.config.scope, client_id))
            self.db.execute("INSERT INTO events(scope,client_id,at,state,detail) VALUES (?,?,?,?,?)", (self.config.scope, client_id, stamp(), state, encoded))

    def uncertainty(self):
        return [r for r in self.rows() if r["state"] in ("UNKNOWN", "SUBMITTING", "CANCEL_UNKNOWN", "CANCEL_SUBMITTING")]

    def daily_usage(self, currency):
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        rows = [r for r in self.rows() if r["state"] != "REJECTED" and parsed_time(r["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).date() == today]
        return sum((decimal(r["notional"]) for r in rows if r["currency"] == currency), Decimal(0)), len(rows)


class Engine:
    def __init__(self, config, broker):
        self.config, self.broker = config, broker
        self.journal = Journal(config)

    def preview(self, intent):
        intent.validate()
        self.broker.supports(intent)
        return {"profile": self.config.name, "mode": self.config.mode, "intent": intent.payload, "currency": intent.currency, "notional": str(intent.notional), "request": self.broker.order_request(intent), "sent": False}

    def _risk(self, intent):
        c = self.config
        if (c.runtime / "STOP").exists():
            raise TradingError("STOP 활성화: 신규 주문이 중지되었습니다.")
        if self.journal.uncertainty():
            raise TradingError("결과 불명 주문이 있습니다. broker-orders와 sync로 먼저 대조하세요.")
        now = datetime.now(timezone.utc)
        if not parsed_time(intent.not_before) <= now < parsed_time(intent.expires_at):
            raise TradingError("주문 실행시각 전이거나 만료된 주문입니다.")
        if f"{intent.market}:{intent.symbol}" not in c.profile.get("allowed_symbols", []):
            raise TradingError("허용 목록에 없는 종목입니다.")
        limits = c.limits
        for key in ("max_order", "max_daily_turnover"):
            if decimal(limits.get(key, {}).get(intent.currency, "0")) <= 0:
                raise TradingError(f"{key}.{intent.currency} 예산을 먼저 설정하세요.")
        if intent.notional > decimal(limits["max_order"][intent.currency]):
            raise TradingError("1회 주문금액 한도 초과")
        used, count = self.journal.daily_usage(intent.currency)
        if used + intent.notional > decimal(limits["max_daily_turnover"][intent.currency]):
            raise TradingError("일일 주문금액 한도 초과 (매수·매도·취소된 주문 포함)")
        if count >= int(limits.get("max_daily_orders", 0)):
            raise TradingError("일일 주문 건수 한도 초과")
        if c.mode != "paper":
            zone = ZoneInfo("Asia/Seoul" if intent.market == "KR" else "America/New_York")
            local = now.astimezone(zone)
            hhmm = local.strftime("%H:%M")
            start, end = ("09:05", "15:15") if intent.market == "KR" else ("09:35", "15:45")
            if local.weekday() > 4 or not start <= hhmm < end:
                raise TradingError("초기 운용은 정규장 내부 시간에만 가능합니다.")
        return now

    def submit(self, intent, confirm_live=False):
        intent.validate()
        self.broker.supports(intent)
        self.config.enabled(confirm_live)
        with file_lock(self.config.runtime / "execution.lock"):
            old = self.journal.get(intent.client_id)
            if old:
                if json.loads(old["payload"]) != intent.payload:
                    raise TradingError("같은 client_id에 다른 주문 내용이 있습니다.")
                return {"duplicate_prevented": True, "order": old}
            self._risk(intent)
            check = self.broker.preflight(intent)
            quote = decimal(check["price"])
            if quote <= 0:
                raise TradingError("유효한 시세가 없습니다.")
            deviation = decimal(self.config.limits.get("max_price_deviation", "0.03"))
            if not 0 <= deviation <= Decimal("0.10"):
                raise TradingError("시세 괴리 한도는 0~0.10 범위여야 합니다.")
            if abs(decimal(intent.price) / quote - 1) > deviation:
                raise TradingError("지정가가 현재가 허용 범위를 벗어났습니다.")
            if intent.trigger:
                threshold = decimal(intent.trigger["price"])
                if not (quote <= threshold if intent.trigger["op"] == "lte" else quote >= threshold):
                    return {"waiting_for_trigger": True, "price": str(quote)}
            if check.get("open_orders", 0) >= int(self.config.limits.get("max_open_orders", 0)) or check.get("symbol_has_open_order"):
                raise TradingError("미체결 주문 한도 또는 동일 종목 미체결 주문에 걸렸습니다.")
            if intent.side == "BUY":
                buffer = decimal(self.config.limits.get("fee_buffer", "0.01"))
                if not Decimal("0.001") <= buffer <= Decimal("0.10"):
                    raise TradingError("수수료 여유율은 0.001~0.10이어야 합니다.")
                if decimal(check["cash"]) < intent.notional * (1 + buffer):
                    raise TradingError("현금 주문가능금액 부족 (수수료 여유 포함)")
            elif decimal(check["sellable"]) < intent.quantity:
                raise TradingError("현금 보유주식의 매도가능수량 부족")
            self._risk(intent)  # network checks may outlast the intent or STOP may be set
            self.journal.reserve(intent)
            try:
                result = self.broker.submit(intent)
                if not result.get("broker_id"):
                    raise Uncertain("주문번호가 없는 응답")
            except Rejected as e:
                self.journal.update(intent.client_id, "REJECTED", {"error": str(e)})
                raise
            except BaseException as e:
                self.journal.update(intent.client_id, "UNKNOWN", {"error": type(e).__name__, "action": "증권사 주문내역에서 확인. 자동 재전송 금지"})
                raise Uncertain("주문 결과 불명: 전송을 반복하지 말고 증권사 주문내역을 확인하세요.") from None
            self.journal.update(intent.client_id, "ACCEPTED", result, result["broker_id"])
            return self.journal.get(intent.client_id)

    def cancel(self, client_id, confirm_live=False):
        self.config.enabled(confirm_live)
        with file_lock(self.config.runtime / "execution.lock"):
            row = self.journal.get(client_id)
            if not row or not row["broker_id"]:
                raise TradingError("로컬 기록과 증권사 주문번호가 필요합니다.")
            if row["state"] in ("CANCEL_SUBMITTING", "CANCEL_UNKNOWN", "CANCEL_REQUESTED"):
                raise TradingError("기존 취소 요청을 sync로 먼저 대조하세요.")
            if row["state"] in ("FILLED", "CANCELED", "REJECTED", "REPLACED"):
                raise TradingError("이미 종료된 주문입니다.")
            intent = Intent(**json.loads(row["payload"]))
            self.journal.update(client_id, "CANCEL_SUBMITTING", json.loads(row["detail"]))
            try:
                result = self.broker.cancel(row, intent)
            except Rejected as e:
                self.journal.update(client_id, row["state"], {**json.loads(row["detail"]), "cancel_error": str(e)})
                raise
            except BaseException:
                self.journal.update(client_id, "CANCEL_UNKNOWN", json.loads(row["detail"]))
                raise Uncertain("취소 결과 불명. 재전송하지 말고 sync로 확인하세요.") from None
            self.journal.update(client_id, "CANCEL_REQUESTED", {**json.loads(row["detail"]), "cancel": result})
        return self.journal.get(client_id)

    def resolve_manually(self, client_id, state, evidence):
        if state not in ("FILLED", "CANCELED", "REJECTED") or len(evidence.strip()) < 15:
            raise TradingError("확정 상태와 15자 이상의 증권사 확인 근거를 입력하세요.")
        with file_lock(self.config.runtime / "execution.lock"):
            row = self.journal.get(client_id)
            if not row or row["state"] in ("FILLED", "CANCELED", "REJECTED"):
                raise TradingError("복구할 미확정 주문이 없습니다.")
            self.journal.update(client_id, state, {**json.loads(row["detail"]), "manual_resolution": evidence, "source": "user-attested broker record"})
            return self.journal.get(client_id)

    def sync(self, client_id):
        with file_lock(self.config.runtime / "execution.lock"):
            row = self.journal.get(client_id)
            if not row or not row["broker_id"]:
                raise TradingError("주문번호를 모르면 자동 대조할 수 없습니다. 복구 절차를 확인하세요.")
            result = self.broker.lookup(row, Intent(**json.loads(row["payload"])))
            state = result.get("state")
            if state not in ("FILLED", "CANCELED", "REJECTED", "REPLACED", "PARTIAL_FILLED", "PENDING"):
                raise TradingError("조회로 상태를 확정하지 못했습니다. 기존 상태를 유지합니다.")
            # A pending original order does not prove a pending cancel failed.
            if row["state"].startswith("CANCEL_") and state in ("PENDING", "PARTIAL_FILLED"):
                return {"state": row["state"], "broker_observation": result}
            self.journal.update(client_id, state, {**json.loads(row["detail"]), "observation": result})
            return self.journal.get(client_id)

    def sync_active(self):
        results = []
        for row in self.journal.rows():
            if row["state"] in ("ACCEPTED", "PENDING", "PARTIAL_FILLED", "CANCEL_REQUESTED", "CANCEL_UNKNOWN") and row["broker_id"]:
                try:
                    results.append(self.sync(row["client_id"]))
                except TradingError as error:
                    results.append({"client_id": row["client_id"], "sync_error": str(error)})
        return results
