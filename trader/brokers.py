from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .core import TradingError, Rejected, Uncertain, HTTPRejected, decimal, parsed_time, private_json, stamp
from .transport import HTTP, TokenCache


def required(d, key):
    if key not in d or d[key] is None or d[key] == "":
        raise TradingError(f"증권사 응답의 필수 필드 누락: {key}")
    return d[key]


class Broker:
    def __init__(self, config, http=None):
        self.c = config
        self.http = http

    def supports(self, intent):
        if self.c.broker in ("kis", "kiwoom") and intent.market != "KR":
            raise TradingError("이 어댑터의 현재 구현 범위는 국내 현금 주식입니다. 미국 주식은 toss를 사용하세요.")

    def token_cache(self, identity, fetch):
        self.token_store = TokenCache(self.c, identity)
        return self.token_store.get(fetch)

    def authenticated_request(self, method, path, headers, **kwargs):
        try:
            return self.http.request(method, path, headers=headers, **kwargs)
        except HTTPRejected as error:
            if error.status != 401 or kwargs.get("write") or not hasattr(self, "token_store"):
                raise
            self.token_store.invalidate()
            key = "Authorization" if "Authorization" in headers else "authorization"
            headers = {**headers, key: "Bearer " + self._token()}
            return self.http.request(method, path, headers=headers, **kwargs)


class Toss(Broker):
    def __init__(self, config, http=None):
        super().__init__(config, http or HTTP("https://openapi.tossinvest.com", config.runtime))

    def _token(self):
        client, secret = self.c.secret("CLIENT_ID"), self.c.secret("CLIENT_SECRET")
        def fetch():
            d = self.http.request("POST", "/oauth2/token", body={"grant_type": "client_credentials", "client_id": client, "client_secret": secret}, form=True).data
            return required(d, "access_token"), time.time() + int(required(d, "expires_in"))
        return self.token_cache(client + secret, fetch)

    def call(self, method, path, *, params=None, body=None, account=True, write=False):
        h = {"Authorization": "Bearer " + self._token()}
        if account:
            h["X-Tossinvest-Account"] = self.c.secret("ACCOUNT")
        d = self.authenticated_request(method, path, h, params=params, body=body, write=write).data
        if d.get("error"):
            # A nonstandard success response carrying an error cannot prove no order was accepted.
            raise (Uncertain if write else TradingError)("토스 API 오류 응답")
        return required(d, "result")

    def accounts(self):
        return self.call("GET", "/api/v1/accounts", account=False)

    def holdings(self):
        return self.call("GET", "/api/v1/holdings")

    def prices(self, symbol):
        rows = self.call("GET", "/api/v1/prices", params={"symbols": symbol}, account=False)
        return next((r for r in rows if r.get("symbol") == symbol), {})

    def orders(self, status="OPEN"):
        result, cursor, seen = [], None, set()
        for _ in range(100):
            params = {"status": status, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self.call("GET", "/api/v1/orders", params=params)
            result.extend(required(page, "orders"))
            if not page.get("hasNext"):
                return result
            cursor = required(page, "nextCursor")
            if cursor in seen:
                raise TradingError("주문 조회 페이지가 반복됩니다.")
            seen.add(cursor)
        raise TradingError("주문 페이지 한도 초과. 부분 결과는 사용하지 않습니다.")

    def order_request(self, i):
        return {"method": "POST", "path": "/api/v1/orders", "body": {"clientOrderId": i.client_id, "symbol": i.symbol, "side": i.side, "orderType": "LIMIT", "timeInForce": "DAY", "quantity": str(i.quantity), "price": i.price, "confirmHighValueOrder": False}}

    def preflight(self, i):
        # Validate the symbol's market/currency against server-owned stock metadata.
        stocks = self.call("GET", "/api/v1/stocks", params={"symbols": i.symbol}, account=False)
        stock = next((x for x in stocks if x.get("symbol") == i.symbol), {})
        if required(stock, "currency") != i.currency:
            raise TradingError("종목과 주문 통화 불일치")
        q = self.prices(i.symbol)
        age = (datetime.now(timezone.utc) - parsed_time(required(q, "timestamp"))).total_seconds()
        if not -5 <= age <= 120:
            raise TradingError("현재가가 120초보다 오래되었거나 미래 시각입니다.")
        if required(q, "currency") != i.currency:
            raise TradingError("시세 통화 불일치")
        zone = ZoneInfo("Asia/Seoul" if i.market == "KR" else "America/New_York")
        date = datetime.now(zone).date().isoformat()
        calendar = self.call("GET", f"/api/v1/market-calendar/{i.market}", params={"date": date}, account=False)
        day = required(calendar, "today")
        if day.get("date") != date:
            raise TradingError("거래일 캘린더 불일치")
        session = (day.get("integrated") or {}).get("regularMarket") if i.market == "KR" else day.get("regularMarket")
        now = datetime.now(timezone.utc)
        if not session or not parsed_time(session["startTime"]) <= now < parsed_time(session["endTime"]):
            raise TradingError("증권사 캘린더상 정규장 운영시간이 아닙니다.")
        orders = self.orders()
        result = {"price": required(q, "lastPrice"), "open_orders": len(orders), "symbol_has_open_order": any(o.get("symbol") == i.symbol for o in orders)}
        if i.side == "BUY":
            result["cash"] = required(self.call("GET", "/api/v1/buying-power", params={"currency": i.currency}), "cashBuyingPower")
        else:
            result["sellable"] = required(self.call("GET", "/api/v1/sellable-quantity", params={"symbol": i.symbol}), "sellableQuantity")
        return result

    def submit(self, i):
        req = self.order_request(i)
        d = self.call("POST", req["path"], body=req["body"], write=True)
        return {"broker_id": required(d, "orderId")}

    def cancel(self, row, i):
        d = self.call("POST", f"/api/v1/orders/{quote(row['broker_id'], safe='')}/cancel", body={}, write=True)
        return {"cancel_id": required(d, "orderId")}

    def lookup(self, row, i):
        d = self.call("GET", f"/api/v1/orders/{quote(row['broker_id'], safe='')}")
        if d.get("symbol") != i.symbol or d.get("side") != i.side:
            raise TradingError("주문 상세가 로컬 주문과 일치하지 않습니다.")
        return {"state": d.get("status"), "execution": d.get("execution"), "order_id": d.get("orderId")}


class KIS(Broker):
    def __init__(self, config, http=None):
        base = "https://openapivts.koreainvestment.com:29443" if config.mode == "sandbox" else "https://openapi.koreainvestment.com:9443"
        super().__init__(config, http or HTTP(base, config.runtime))

    def tr(self, real):
        return "V" + real[1:] if self.c.mode == "sandbox" else real

    def account_fields(self):
        account = self.c.secret("ACCOUNT").replace("-", "")
        if len(account) != 10 or not account.isdigit():
            raise TradingError("KIS ACCOUNT는 8자리 계좌번호+2자리 상품코드입니다.")
        return {"CANO": account[:8], "ACNT_PRDT_CD": account[8:]}

    def _token(self):
        key, secret = self.c.secret("APP_KEY"), self.c.secret("APP_SECRET")
        def fetch():
            d = self.http.request("POST", "/oauth2/tokenP", body={"grant_type": "client_credentials", "appkey": key, "appsecret": secret}).data
            return required(d, "access_token"), time.time() + int(required(d, "expires_in"))
        return self.token_cache(key + secret, fetch)

    def call(self, path, tr, fields, write=False, continuation=""):
        h = {"authorization": "Bearer " + self._token(), "appkey": self.c.secret("APP_KEY"), "appsecret": self.c.secret("APP_SECRET"), "tr_id": tr, "custtype": "P", "tr_cont": continuation}
        if write:
            # Hash the exact final payload, as documented by KIS.
            hashed = self.http.request("POST", "/uapi/hashkey", headers={"appkey": h["appkey"], "appsecret": h["appsecret"]}, body=fields).data
            h["hashkey"] = required(hashed, "HASH")
        r = self.authenticated_request("POST" if write else "GET", path, h, body=fields if write else None, params=None if write else fields, write=write)
        if str(r.data.get("rt_cd")) != "0":
            if "rt_cd" not in r.data:
                raise (Uncertain if write else TradingError)("KIS 결과 코드 누락")
            code = str(r.data.get("msg_cd", "unknown"))[:30]
            raise Rejected(f"KIS 업무 오류 {code}; 공식 오류코드/계좌 설정 확인")
        return r

    def pages(self, path, tr, fields, key="output1"):
        result, seen, continuation = [], set(), ""
        for _ in range(100):
            r = self.call(path, tr, fields, continuation=continuation)
            result.extend(required(r.data, key))
            if r.headers.get("tr_cont") not in ("F", "M"):
                return result
            fk, nk = required(r.data, "ctx_area_fk100"), required(r.data, "ctx_area_nk100")
            if (fk, nk) in seen:
                raise TradingError("KIS 연속조회 키 반복")
            seen.add((fk, nk))
            fields = {**fields, "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk}
            continuation = "N"
        raise TradingError("KIS 페이지 한도 초과")

    def accounts(self):
        self.holdings()
        return {"configured_account_verified": True}

    def holdings(self):
        p = {**self.account_fields(), "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        return self.pages("/uapi/domestic-stock/v1/trading/inquire-balance", self.tr("TTTC8434R"), p)

    def prices(self, symbol):
        return required(self.call("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}).data, "output")

    def orders(self, status="OPEN", date=None):
        date = date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        p = {**self.account_fields(), "INQR_STRT_DT": date, "INQR_END_DT": date, "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "", "CCLD_DVSN": "02" if status == "OPEN" else "00", "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "", "INQR_DVSN_3": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "", "EXCG_ID_DVSN_CD": "KRX"}
        return self.pages("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", self.tr("TTTC0081R"), p)

    def order_request(self, i):
        return {"method": "POST", "path": "/uapi/domestic-stock/v1/trading/order-cash", "tr_id": self.tr("TTTC0012U" if i.side == "BUY" else "TTTC0011U"), "body": {"CANO": "<account-8>", "ACNT_PRDT_CD": "<product-2>", "PDNO": i.symbol, "ORD_DVSN": "00", "ORD_QTY": str(i.quantity), "ORD_UNPR": i.price, "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if i.side == "SELL" else "", "CNDT_PRIC": ""}}

    def preflight(self, i):
        q = self.prices(i.symbol)
        orders = [r for r in self.orders() if decimal(required(r, "rmn_qty")) > 0]
        result = {"price": required(q, "stck_prpr"), "open_orders": len(orders), "symbol_has_open_order": any(o.get("pdno") == i.symbol for o in orders)}
        if i.side == "BUY":
            p = {**self.account_fields(), "PDNO": i.symbol, "ORD_UNPR": i.price, "ORD_DVSN": "01", "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N"}
            d = required(self.call("/uapi/domestic-stock/v1/trading/inquire-psbl-order", self.tr("TTTC8908R"), p).data, "output")
            result["cash"] = required(d, "nrcvb_buy_amt")
            if decimal(required(d, "nrcvb_buy_qty")) < i.quantity:
                raise TradingError("KIS 미수 없는 매수가능수량 부족")
        else:
            holdings = self.holdings()
            found = [r for r in holdings if r.get("pdno") == i.symbol]
            result["sellable"] = str(sum((decimal(required(r, "ord_psbl_qty")) for r in found), Decimal(0)))
        return result

    def submit(self, i):
        req = self.order_request(i)
        d = required(self.call(req["path"], req["tr_id"], {**req["body"], **self.account_fields()}, write=True).data, "output")
        return {"broker_id": required(d, "ODNO"), "org_no": required(d, "KRX_FWDG_ORD_ORGNO")}

    def cancel(self, row, i):
        # Actual cancelable quantity lookup is mandatory in KIS documentation (live).
        if parsed_time(row["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).date() != datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise Rejected("KIS 전일 주문은 앱에서 확인/취소하세요.")
        if self.c.mode == "live":
            p = {**self.account_fields(), "CTX_AREA_FK100": "", "CTX_AREA_NK100": "", "INQR_DVSN_1": "0", "INQR_DVSN_2": "0"}
            rows = self.pages("/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl", "TTTC0084R", p, "output")
            found = [r for r in rows if str(r.get("odno")) == row["broker_id"]]
            if not found or decimal(required(found[0], "psbl_qty")) <= 0:
                raise Rejected("KIS 정정취소가능수량이 없습니다.")
        detail = json.loads(row["detail"])
        p = {**self.account_fields(), "KRX_FWDG_ORD_ORGNO": required(detail, "org_no"), "ORGN_ODNO": row["broker_id"], "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0", "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX"}
        return required(self.call("/uapi/domestic-stock/v1/trading/order-rvsecncl", self.tr("TTTC0013U"), p, write=True).data, "output")

    def lookup(self, row, i):
        date = parsed_time(row["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        rows = self.orders("CLOSED", date)
        d = next((r for r in rows if str(r.get("odno")) == row["broker_id"] and r.get("pdno") == i.symbol), None)
        if not d:
            raise TradingError("KIS 조회에서 해당 주문을 찾지 못했습니다. 미확인 상태 유지")
        filled = decimal(required(d, "tot_ccld_qty"))
        state = "CANCELED" if d.get("cncl_yn") == "Y" else "FILLED" if filled >= i.quantity else "PARTIAL_FILLED" if filled > 0 else "PENDING"
        return {"state": state, "filled_quantity": str(filled)}


class Kiwoom(Broker):
    def __init__(self, config, http=None):
        base = "https://mockapi.kiwoom.com" if config.mode == "sandbox" else "https://api.kiwoom.com"
        super().__init__(config, http or HTTP(base, config.runtime))

    def _token(self):
        key, secret = self.c.secret("APP_KEY"), self.c.secret("APP_SECRET")
        def fetch():
            d = self.http.request("POST", "/oauth2/token", body={"grant_type": "client_credentials", "appkey": key, "secretkey": secret}).data
            expiry = datetime.strptime(required(d, "expires_dt"), "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Seoul")).timestamp()
            return required(d, "token"), expiry
        return self.token_cache(key + secret, fetch)

    def call(self, api, path, body, write=False, next_key=""):
        h = {"authorization": "Bearer " + self._token(), "api-id": api, "cont-yn": "Y" if next_key else "N", "next-key": next_key}
        r = self.authenticated_request("POST", path, h, body=body, write=write)
        if str(r.data.get("return_code")) != "0":
            if "return_code" not in r.data:
                raise (Uncertain if write else TradingError)("키움 결과 코드 누락")
            raise Rejected(f"키움 업무 오류 {r.data['return_code']}; 공식 오류코드 확인")
        return r

    def pages(self, api, body, key):
        result, next_key, seen = [], "", set()
        for _ in range(100):
            r = self.call(api, "/api/dostk/acnt", body, next_key=next_key)
            result.extend(required(r.data, key))
            if r.headers.get("cont-yn") != "Y":
                return result
            next_key = r.headers.get("next-key")
            if not next_key or next_key in seen:
                raise TradingError("키움 연속조회 키 누락/반복")
            seen.add(next_key)
        raise TradingError("키움 페이지 한도 초과")

    def accounts(self):
        d = self.call("ka00001", "/api/dostk/acnt", {}).data
        return {"account": required(d, "acctNo")}

    def holdings(self):
        return self.pages("kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"}, "acnt_evlt_remn_indv_tot")

    def prices(self, symbol):
        return self.call("ka10001", "/api/dostk/stkinfo", {"stk_cd": symbol}).data

    def orders(self, status="OPEN"):
        if status == "OPEN":
            return self.pages("ka10075", {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "1"}, "oso")
        return self.pages("ka10076", {"stk_cd": "", "qry_tp": "0", "sell_tp": "0", "ord_no": "", "stex_tp": "1"}, "cntr")

    def order_request(self, i):
        return {"method": "POST", "path": "/api/dostk/ordr", "api_id": "kt10000" if i.side == "BUY" else "kt10001", "body": {"dmst_stex_tp": "KRX", "stk_cd": i.symbol, "ord_qty": str(i.quantity), "ord_uv": i.price, "trde_tp": "0", "cond_uv": ""}}

    def preflight(self, i):
        if self.accounts()["account"] != self.c.secret("ACCOUNT").replace("-", ""):
            raise TradingError("키움 앱키에 연결된 계좌가 설정된 계좌와 다릅니다.")
        q, orders = self.prices(i.symbol), self.orders()
        result = {"price": str(abs(decimal(required(q, "cur_prc")))), "open_orders": len(orders), "symbol_has_open_order": any(o.get("stk_cd", "").removeprefix("A") == i.symbol for o in orders)}
        if i.side == "BUY":
            d = self.call("kt00010", "/api/dostk/acnt", {"io_amt": "0", "stk_cd": i.symbol, "trde_tp": "2", "trde_qty": str(i.quantity), "uv": i.price, "exp_buy_unp": i.price}).data
            result["cash"] = str(min(decimal(required(d, "ord_alowa")), decimal(required(d, "profa_100ord_alow_amt"))))
            if decimal(required(d, "profa_100ord_alowq")) < i.quantity:
                raise TradingError("키움 증거금100% 매수가능수량 부족")
        else:
            rows = [r for r in self.holdings() if r.get("stk_cd", "").removeprefix("A") == i.symbol and r.get("crd_tp") == "00"]
            result["sellable"] = str(sum((decimal(required(r, "trde_able_qty")) for r in rows), Decimal(0)))
        return result

    def submit(self, i):
        r = self.order_request(i)
        d = self.call(r["api_id"], r["path"], r["body"], write=True).data
        return {"broker_id": required(d, "ord_no")}

    def cancel(self, row, i):
        # Order numbers are reused across dates; do not cancel an old order number.
        if self.accounts()["account"] != self.c.secret("ACCOUNT").replace("-", ""):
            raise Rejected("키움 앱키 계좌 불일치")
        if parsed_time(row["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).date() != datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise Rejected("키움 전일 주문은 앱에서 확인/취소하세요.")
        found = [x for x in self.orders() if str(x.get("ord_no")) == row["broker_id"] and x.get("stk_cd", "").removeprefix("A") == i.symbol]
        if not found or decimal(required(found[0], "oso_qty")) <= 0:
            raise Rejected("키움 미체결 수량이 없습니다.")
        return self.call("kt10003", "/api/dostk/ordr", {"dmst_stex_tp": "KRX", "orig_ord_no": row["broker_id"], "stk_cd": i.symbol, "cncl_qty": "0"}, write=True).data

    def lookup(self, row, i):
        if parsed_time(row["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).date() != datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise TradingError("키움 당일 조회 범위 밖입니다. 증권사 앱의 과거 주문으로 확인하세요.")
        match = lambda x: str(x.get("ord_no")) == row["broker_id"] and x.get("stk_cd", "").removeprefix("A") == i.symbol
        opened = next((x for x in self.orders() if match(x)), None)
        if opened:
            remaining = decimal(required(opened, "oso_qty"))
            return {"state": "PARTIAL_FILLED" if remaining < i.quantity else "PENDING", "remaining": str(remaining)}
        filled_rows = [x for x in self.orders("CLOSED") if match(x)]
        # Don't sum potential per-fill/aggregate duplicate representations.
        if len(filled_rows) == 1 and decimal(required(filled_rows[0], "cntr_qty")) == i.quantity and decimal(required(filled_rows[0], "oso_qty")) == 0:
            return {"state": "FILLED", "filled_quantity": str(i.quantity)}
        raise TradingError("키움 체결/취소 상태를 확정하지 못했습니다. 앱과 대조하세요.")


class Paper(Broker):
    """Deterministic offline simulator, with explicit match/fill, no network calls."""
    def __init__(self, config, http=None):
        super().__init__(config)
        self.path = config.runtime / f"paper-{config.scope}.json"
        if not self.path.exists():
            private_json(self.path, {"cash": {"KRW": "1000000", "USD": "1000"}, "positions": {}, "orders": {}})

    def data(self):
        return json.loads(self.path.read_text())

    def accounts(self):
        return {"simulated": True, "cash": self.data()["cash"]}

    def holdings(self):
        return self.data()["positions"]

    def prices(self, symbol):
        return {"symbol": symbol, "price": self.c.profile["paper_prices"][symbol], "simulated": True}

    def orders(self, status="OPEN"):
        return [o for o in self.data()["orders"].values() if (o["state"] in ("PENDING", "PARTIAL_FILLED")) == (status == "OPEN")]

    def order_request(self, i):
        return {"method": "LOCAL", "path": "paper-ledger", "body": i.payload}

    def preflight(self, i):
        d, orders = self.data(), self.orders()
        reserved = sum((decimal(o["price"]) * (o["quantity"] - o["filled"]) for o in orders if o["side"] == "BUY" and o["market"] == i.market), Decimal(0))
        reserved_sell = sum(o["quantity"] - o["filled"] for o in orders if o["side"] == "SELL" and o["symbol"] == i.symbol)
        return {"price": self.prices(i.symbol)["price"], "cash": str(decimal(d["cash"][i.currency]) - reserved), "sellable": d["positions"].get(i.symbol, 0) - reserved_sell, "open_orders": len(orders), "symbol_has_open_order": any(o["symbol"] == i.symbol for o in orders)}

    def submit(self, i):
        d = self.data()
        d["orders"][i.client_id] = {**i.payload, "state": "PENDING", "filled": 0}
        private_json(self.path, d)
        return {"broker_id": i.client_id, "simulated": True}

    def cancel(self, row, i):
        d = self.data()
        o = d["orders"][row["broker_id"]]
        if o["state"] not in ("PENDING", "PARTIAL_FILLED"):
            raise Rejected("모의 주문이 이미 종료되었습니다.")
        o["state"] = "CANCELED"
        private_json(self.path, d)
        return {"canceled": True, "filled_quantity": o["filled"]}

    def lookup(self, row, i):
        o = self.data()["orders"][row["broker_id"]]
        return {"state": o["state"], "filled_quantity": o["filled"]}

    def fill(self, client_id, quantity):
        d = self.data()
        o = d["orders"].get(client_id)
        if not o or o["state"] not in ("PENDING", "PARTIAL_FILLED") or type(quantity) is not int or not 0 < quantity <= o["quantity"] - o["filled"]:
            raise TradingError("모의 체결 수량/상태 오류")
        currency = "KRW" if o["market"] == "KR" else "USD"
        amount = decimal(o["price"]) * quantity
        sign = 1 if o["side"] == "BUY" else -1
        d["cash"][currency] = str(decimal(d["cash"][currency]) - sign * amount)
        d["positions"][o["symbol"]] = d["positions"].get(o["symbol"], 0) + sign * quantity
        o["filled"] += quantity
        o["state"] = "FILLED" if o["filled"] == o["quantity"] else "PARTIAL_FILLED"
        private_json(self.path, d)
        return o


def make_broker(config, http=None):
    return {"paper": Paper, "toss": Toss, "kis": KIS, "kiwoom": Kiwoom}[config.broker](config, http)
