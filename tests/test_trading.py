import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from trader.core import Config, Engine, Intent, TradingError, Rejected, Uncertain, HTTPRejected, decimal, private_json
from trader.brokers import Paper, Toss, KIS, Kiwoom
from trader.transport import HTTP, Response, TokenCache


ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.cfg_path = self.path / "config.json"
        self.data = json.loads((ROOT / "config.json").read_text())
        self.cfg_path.write_text(json.dumps(self.data))
        self.c = Config(self.cfg_path, "paper")
        self.b = Paper(self.c)
        self.e = Engine(self.c, self.b)
        self.addCleanup(self.e.journal.db.close)
        now = datetime.now(timezone.utc)
        self.i = Intent("test-buy-1", "KR", "005930", "BUY", 2, "70000", (now-timedelta(seconds=1)).isoformat(), (now+timedelta(hours=1)).isoformat())

    def profile(self, name):
        c = Config(self.cfg_path, name)
        return c


class LifecycleTests(Base):
    def test_buy_partial_cancel_sell_and_reconcile_cash(self):
        self.assertEqual(self.e.submit(self.i)["state"], "ACCEPTED")
        self.b.fill(self.i.client_id, 1)
        self.assertEqual(self.e.sync(self.i.client_id)["state"], "PARTIAL_FILLED")
        self.e.cancel(self.i.client_id)
        self.assertEqual(self.e.sync(self.i.client_id)["state"], "CANCELED")
        sell = replace(self.i, client_id="sell-1", side="SELL", quantity=1)
        self.e.submit(sell)
        self.b.fill(sell.client_id, 1)
        self.assertEqual(self.e.sync(sell.client_id)["state"], "FILLED")
        self.assertEqual(self.b.holdings()[self.i.symbol], 0)
        self.assertEqual(decimal(self.b.accounts()["cash"]["KRW"]), 1000000)

    def test_restart_reuses_persistent_id(self):
        self.e.submit(self.i)
        e2 = Engine(self.c, Paper(self.c))
        self.addCleanup(e2.journal.db.close)
        self.assertTrue(e2.submit(self.i)["duplicate_prevented"])
        self.assertEqual(len(self.b.orders()), 1)

    def test_same_id_different_payload_fails(self):
        self.e.submit(self.i)
        with self.assertRaises(TradingError):
            self.e.submit(replace(self.i, quantity=1))

    def test_timeout_after_broker_accept_blocks_resend_and_other_order(self):
        original = self.b.submit
        def ambiguous(i):
            original(i)
            raise TimeoutError()
        self.b.submit = ambiguous
        with self.assertRaises(Uncertain):
            self.e.submit(self.i)
        self.assertEqual(self.e.journal.get(self.i.client_id)["state"], "UNKNOWN")
        self.assertTrue(self.e.submit(self.i)["duplicate_prevented"])
        with self.assertRaises(TradingError):
            self.e.submit(replace(self.i, client_id="new-attempt"))
        self.assertEqual(len(self.b.orders()), 1)

    def test_crash_between_reserve_and_network_blocks_new_order(self):
        self.e.journal.reserve(self.i)
        with self.assertRaises(TradingError):
            self.e.submit(replace(self.i, client_id="new"))

    def test_explicit_rejection_not_unknown(self):
        self.b.submit = Mock(side_effect=Rejected("no cash"))
        with self.assertRaises(Rejected):
            self.e.submit(self.i)
        self.assertEqual(self.e.journal.get(self.i.client_id)["state"], "REJECTED")

    def test_cancel_unknown_does_not_resend(self):
        self.e.submit(self.i)
        self.b.cancel = Mock(side_effect=TimeoutError())
        with self.assertRaises(Uncertain):
            self.e.cancel(self.i.client_id)
        with self.assertRaises(TradingError):
            self.e.cancel(self.i.client_id)
        self.assertEqual(self.b.cancel.call_count, 1)
        self.assertEqual(self.e.sync(self.i.client_id)["state"], "CANCEL_UNKNOWN")

    def test_stop_blocks_new_but_allows_cancel(self):
        self.e.submit(self.i)
        (self.c.runtime / "STOP").touch()
        with self.assertRaises(TradingError):
            self.e.submit(replace(self.i, client_id="another"))
        self.assertEqual(self.e.cancel(self.i.client_id)["state"], "CANCEL_REQUESTED")

    def test_stop_during_preflight_blocks_send(self):
        original = self.b.preflight
        def stop(i):
            result = original(i)
            (self.c.runtime / "STOP").touch()
            return result
        self.b.preflight = stop
        with self.assertRaises(TradingError):
            self.e.submit(self.i)
        self.assertFalse(self.b.orders())

    def test_concurrent_workers_send_exactly_one_order(self):
        barrier = threading.Barrier(2)
        def worker():
            c = Config(self.cfg_path, "paper")
            e = Engine(c, Paper(c))
            barrier.wait()
            try:
                return e.submit(self.i)
            finally:
                e.journal.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(bool(r.get("duplicate_prevented")) for r in results), 1)
        self.assertEqual(len(self.b.orders()), 1)

    def test_manual_resolution_records_audit(self):
        self.e.journal.reserve(self.i)
        self.e.resolve_manually(self.i.client_id, "REJECTED", "Verified in broker app: no order was accepted at 09:10.")
        row = self.e.journal.get(self.i.client_id)
        self.assertIn("manual_resolution", row["detail"])
        self.assertEqual(self.e.journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)


class RiskTests(Base):
    def assert_blocked(self, intent):
        with self.assertRaises(TradingError):
            self.e.submit(intent)
        self.assertFalse(self.b.orders())

    def test_no_short_selling(self):
        self.assert_blocked(replace(self.i, side="SELL"))

    def test_cash_with_fees(self):
        d = self.b.data(); d["cash"]["KRW"] = "140000"; private_json(self.b.path, d)
        self.assert_blocked(self.i)

    def test_order_budget(self):
        self.c.limits["max_order"]["KRW"] = "100000"
        self.assert_blocked(self.i)

    def test_daily_budget_accounts_for_cancelled_orders(self):
        self.e.submit(self.i)
        self.e.cancel(self.i.client_id)
        self.e.sync(self.i.client_id)
        self.c.limits["max_daily_turnover"]["KRW"] = "200000"
        with self.assertRaises(TradingError):
            self.e.submit(replace(self.i, client_id="second"))

    def test_daily_order_count(self):
        self.c.limits["max_daily_orders"] = 0
        self.assert_blocked(self.i)

    def test_allowlist(self):
        self.c.profile["allowed_symbols"] = []
        self.assert_blocked(self.i)

    def test_price_band(self):
        self.assert_blocked(replace(self.i, price="75000"))

    def test_expired_order(self):
        self.assert_blocked(replace(self.i, not_before="2000-01-01T00:00:00Z", expires_at="2000-01-01T01:00:00Z"))

    def test_future_order(self):
        self.assert_blocked(replace(self.i, not_before="2099-01-01T00:00:00Z", expires_at="2099-01-01T01:00:00Z"))

    def test_quantity_and_number_validation(self):
        for q in (0, -1, True, 0.5, "1"):
            with self.subTest(q=q): self.assert_blocked(replace(self.i, quantity=q))
        for price in ("NaN", "Infinity", "-1", "0", "1e9", "1.2", 70000):
            with self.subTest(price=price): self.assert_blocked(replace(self.i, price=price))

    def test_timezone_required(self):
        self.assert_blocked(replace(self.i, not_before="2026-09-20T09:00:00"))

    def test_waiting_trigger_makes_no_journal_or_network_write(self):
        result = self.e.submit(replace(self.i, trigger={"op":"lte", "price":"69000"}))
        self.assertTrue(result["waiting_for_trigger"])
        self.assertFalse(self.e.journal.rows())

    def test_us_limits_are_separate_currency(self):
        i = replace(self.i, market="US", symbol="AAPL", price="200", quantity=1)
        self.e.submit(i)
        self.assertEqual(self.e.journal.daily_usage("KRW")[0], 0)
        self.assertEqual(self.e.journal.daily_usage("USD")[0], 200)

    def test_live_requires_two_explicit_switches(self):
        c = self.profile("toss_live")
        for enabled, confirm in ((False, False),(True,False),(False,True)):
            c.profile["live_enabled"] = enabled
            with self.subTest(enabled=enabled, confirm=confirm), self.assertRaises(TradingError):
                c.enabled(confirm)
        c.profile["live_enabled"] = True
        c.enabled(True)

    def test_account_scope_normalizes_dashes_and_survives_key_rotation(self):
        with patch.dict(os.environ, {"KIS_LIVE_ACCOUNT":"12345678-01", "KIS_LIVE_APP_KEY":"old"}):
            a = self.profile("kis_live").scope
        with patch.dict(os.environ, {"KIS_LIVE_ACCOUNT":"1234567801", "KIS_LIVE_APP_KEY":"new"}):
            b = self.profile("kis_live").scope
        self.assertEqual(a,b)


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append({"method":method,"path":path,**kwargs})
        r = self.replies.pop(0)
        return r if isinstance(r, Response) else Response(r,{})


class AdapterTests(Base):
    def test_read_401_refreshes_once_but_write_401_never_replays(self):
        b=Toss(self.profile("toss_live"))
        b.token_store=Mock()
        b.http=Mock()
        b.http.request.side_effect=[HTTPRejected(401),Response({"result":[]},{})]
        with patch.object(b,"_token",return_value="fresh"):
            b.authenticated_request("GET","/api/v1/accounts",{"Authorization":"Bearer old"})
        self.assertEqual(b.http.request.call_count,2)
        self.assertEqual(b.http.request.call_args.kwargs["headers"]["Authorization"],"Bearer fresh")
        b.http.reset_mock();b.token_store.reset_mock()
        b.http.request.side_effect=HTTPRejected(401)
        with self.assertRaises(HTTPRejected):
            b.authenticated_request("POST","/api/v1/orders",{"Authorization":"Bearer old"},body={},write=True)
        self.assertEqual(b.http.request.call_count,1)
        b.token_store.invalidate.assert_not_called()

    def test_toss_holiday_blocks_preflight(self):
        from zoneinfo import ZoneInfo
        b=Toss(self.profile("toss_live"))
        date=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        def call(method,path,**kwargs):
            if path.endswith("/stocks"):return [{"symbol":"005930","currency":"KRW"}]
            return {"today":{"date":date,"integrated":None}}
        with patch.object(b,"call",side_effect=call),patch.object(b,"prices",return_value={"timestamp":datetime.now(timezone.utc).isoformat(),"currency":"KRW","lastPrice":"70000"}):
            with self.assertRaisesRegex(TradingError,"정규장"):
                b.preflight(self.i)

    def test_toss_preflight_uses_cash_buying_power(self):
        from zoneinfo import ZoneInfo
        b=Toss(self.profile("toss_live"))
        now=datetime.now(timezone.utc)
        date=now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        session={"startTime":(now-timedelta(minutes=1)).isoformat(),"endTime":(now+timedelta(minutes=1)).isoformat()}
        def call(method,path,**kwargs):
            if path.endswith("/stocks"):return [{"symbol":"005930","currency":"KRW"}]
            if "market-calendar" in path:return {"today":{"date":date,"integrated":{"regularMarket":session}}}
            if path.endswith("/buying-power"):return {"currency":"KRW","cashBuyingPower":"2489"}
            raise AssertionError(path)
        with patch.object(b,"call",side_effect=call),patch.object(b,"orders",return_value=[]),patch.object(b,"prices",return_value={"timestamp":now.isoformat(),"currency":"KRW","lastPrice":"70000"}):
            self.assertEqual(b.preflight(self.i)["cash"],"2489")

    def test_kis_preflight_excludes_margin_buying_power(self):
        b=KIS(self.profile("kis_demo"))
        reply=Response({"output":{"nrcvb_buy_amt":"50000","nrcvb_buy_qty":"2","max_buy_amt":"9999999"}},{})
        with patch.object(b,"prices",return_value={"stck_prpr":"70000"}),patch.object(b,"orders",return_value=[]),patch.object(b,"account_fields",return_value={}),patch.object(b,"call",return_value=reply):
            self.assertEqual(b.preflight(self.i)["cash"],"50000")

    def test_kis_and_kiwoom_sandbox_host_is_separate(self):
        self.assertEqual(KIS(self.profile("kis_demo")).http.base,"https://openapivts.koreainvestment.com:29443")
        self.assertEqual(KIS(self.profile("kis_live")).http.base,"https://openapi.koreainvestment.com:9443")
        self.assertEqual(Kiwoom(self.profile("kiwoom_demo")).http.base,"https://mockapi.kiwoom.com")
        self.assertEqual(Kiwoom(self.profile("kiwoom_live")).http.base,"https://api.kiwoom.com")

    def test_toss_does_not_assume_empty_page_when_result_missing(self):
        b=Toss(self.profile("toss_live"),FakeHTTP([{}]))
        with patch.object(b,"_token",return_value="test"):
            with self.assertRaises(TradingError):b.accounts()

    def test_toss_orders_use_official_idempotency_field_and_decimal_strings(self):
        h = FakeHTTP([{"result":{"orderId":"broker-123"}}])
        b = Toss(self.profile("toss_live"), h)
        with patch.object(b, "_token", return_value="test-token"), patch.dict(os.environ,{"TOSS_LIVE_ACCOUNT":"1"}):
            self.assertEqual(b.submit(self.i)["broker_id"],"broker-123")
        req=h.calls[0]
        self.assertTrue(req["write"])
        self.assertEqual(req["body"]["clientOrderId"],self.i.client_id)
        self.assertEqual(req["body"]["quantity"],"2")
        self.assertEqual(req["body"]["orderType"],"LIMIT")
        self.assertEqual(req["headers"]["X-Tossinvest-Account"],"1")

    def test_toss_pagination_reads_all_and_preserves_status(self):
        h=FakeHTTP([{"result":{"orders":[{"orderId":"a"}],"hasNext":True,"nextCursor":"cursor"}}, {"result":{"orders":[{"orderId":"b"}],"hasNext":False}}])
        b=Toss(self.profile("toss_live"),h)
        with patch.object(b,"_token",return_value="test"), patch.dict(os.environ,{"TOSS_LIVE_ACCOUNT":"1"}):
            self.assertEqual(len(b.orders("CLOSED")),2)
        self.assertEqual(h.calls[1]["params"]["cursor"],"cursor")

    def test_toss_stale_quote_blocks(self):
        b=Toss(self.profile("toss_live"))
        with patch.object(b,"call",return_value=[{"symbol":"005930","currency":"KRW"}]), patch.object(b,"prices",return_value={"timestamp":"2000-01-01T00:00:00Z","currency":"KRW","lastPrice":"70000"}):
            with self.assertRaisesRegex(TradingError,"120초"):
                b.preflight(self.i)

    def test_toss_different_currency_blocks(self):
        b=Toss(self.profile("toss_live"))
        with patch.object(b,"call",return_value=[{"symbol":"005930","currency":"USD"}]):
            with self.assertRaises(TradingError):b.preflight(self.i)

    def test_kis_current_transaction_ids_and_hash(self):
        h=FakeHTTP([{"HASH":"test-hash"},{"rt_cd":"0","output":{"ODNO":"0001","KRX_FWDG_ORD_ORGNO":"0002"}}])
        b=KIS(self.profile("kis_demo"),h)
        with patch.object(b,"_token",return_value="test"),patch.dict(os.environ,{"KIS_DEMO_ACCOUNT":"12345678-01","KIS_DEMO_APP_KEY":"key","KIS_DEMO_APP_SECRET":"secret"}):
            self.assertEqual(b.submit(self.i)["org_no"],"0002")
        req=h.calls[1]
        self.assertEqual(req["headers"]["tr_id"],"VTTC0012U")
        self.assertEqual(req["headers"]["hashkey"],"test-hash")
        self.assertEqual(req["body"]["CANO"],"12345678")
        self.assertEqual(req["body"]["ACNT_PRDT_CD"],"01")
        self.assertEqual(h.calls[0]["body"],req["body"])
        self.assertEqual(b.order_request(replace(self.i,side="SELL"))["tr_id"],"VTTC0011U")

    def test_kis_business_error_is_rejected(self):
        b=KIS(self.profile("kis_demo"),FakeHTTP([{"rt_cd":"1","msg_cd":"APBK0001"}]))
        with patch.object(b,"_token",return_value="test"),patch.dict(os.environ,{"KIS_DEMO_APP_KEY":"key","KIS_DEMO_APP_SECRET":"secret"}):
            with self.assertRaises(Rejected):b.call("/read","VTTC8434R",{})

    def test_kis_continuation_headers(self):
        h=FakeHTTP([Response({"rt_cd":"0","output1":[{"pdno":"005930"}],"ctx_area_fk100":"f","ctx_area_nk100":"n"},{"tr_cont":"M"}),{"rt_cd":"0","output1":[{"pdno":"000660"}]}])
        b=KIS(self.profile("kis_demo"),h)
        with patch.object(b,"_token",return_value="test"),patch.dict(os.environ,{"KIS_DEMO_ACCOUNT":"1234567801","KIS_DEMO_APP_KEY":"key","KIS_DEMO_APP_SECRET":"secret"}):
            self.assertEqual(len(b.holdings()),2)
        self.assertEqual(h.calls[1]["headers"]["tr_cont"],"N")
        self.assertEqual(h.calls[1]["params"]["CTX_AREA_NK100"],"n")

    def test_kiwoom_limit_order_codes_and_string_quantity(self):
        h=FakeHTTP([{"return_code":0,"ord_no":"0000123"}])
        b=Kiwoom(self.profile("kiwoom_demo"),h)
        with patch.object(b,"_token",return_value="test"):
            b.submit(self.i)
        self.assertEqual(h.calls[0]["headers"]["api-id"],"kt10000")
        self.assertEqual(h.calls[0]["body"]["trde_tp"],"0")
        self.assertEqual(h.calls[0]["body"]["ord_qty"],"2")
        self.assertEqual(b.order_request(replace(self.i,side="SELL"))["api_id"],"kt10001")

    def test_kiwoom_key_account_mismatch(self):
        b=Kiwoom(self.profile("kiwoom_demo"))
        with patch.object(b,"accounts",return_value={"account":"9999999999"}),patch.dict(os.environ,{"KIWOOM_DEMO_ACCOUNT":"1234567890"}):
            with self.assertRaisesRegex(TradingError,"계좌"):
                b.preflight(self.i)

    def test_kiwoom_signed_quote_and_cash_only_buying_power(self):
        b=Kiwoom(self.profile("kiwoom_demo"))
        data={"ord_alowa":"2000000","profa_100ord_alow_amt":"50000","profa_100ord_alowq":"10"}
        with patch.object(b,"accounts",return_value={"account":"1234567890"}),patch.dict(os.environ,{"KIWOOM_DEMO_ACCOUNT":"1234567890"}),patch.object(b,"prices",return_value={"cur_prc":"-70000"}),patch.object(b,"orders",return_value=[]),patch.object(b,"call",return_value=Response(data,{})):
            result=b.preflight(self.i)
        self.assertEqual(result["price"],"70000")
        self.assertEqual(result["cash"],"50000")

    def test_kiwoom_disappearing_order_is_not_assumed_filled(self):
        b=Kiwoom(self.profile("kiwoom_demo"))
        with patch.object(b,"orders",return_value=[]):
            with self.assertRaises(TradingError):
                b.lookup({"broker_id":"0001","created_at":datetime.now(timezone.utc).isoformat()},self.i)

    def test_unsupported_markets_never_silently_route(self):
        for klass,profile in ((KIS,"kis_demo"),(Kiwoom,"kiwoom_demo")):
            with self.assertRaises(TradingError):
                klass(self.profile(profile)).supports(replace(self.i,market="US",symbol="AAPL",price="200"))


class TransportTests(Base):
    def http(self):
        h=HTTP("https://openapi.tossinvest.com",self.c.runtime)
        h._pace=Mock()
        return h

    def test_order_timeout_is_sent_once(self):
        h=self.http();h.opener.open=Mock(side_effect=TimeoutError())
        with self.assertRaises(Uncertain):h.request("POST","/api/v1/orders",body={},write=True)
        self.assertEqual(h.opener.open.call_count,1)

    def test_write_429_is_not_retried(self):
        h=self.http();h.opener.open=Mock(side_effect=urllib.error.HTTPError("https://openapi.tossinvest.com",429,"limited",{},io.BytesIO(b"")))
        with self.assertRaises(Uncertain):h.request("POST","/api/v1/orders",body={},write=True)
        self.assertEqual(h.opener.open.call_count,1)

    def test_read_transient_failure_is_retried(self):
        h=self.http();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"result":[]}';response.headers={}
        h.opener.open=Mock(side_effect=[TimeoutError(),response])
        with patch("trader.transport.time.sleep"):
            self.assertEqual(h.request("GET","/api/v1/orders").data,{"result":[]})
        self.assertEqual(h.opener.open.call_count,2)

    def test_invalid_host_blocked(self):
        with self.assertRaises(TradingError):HTTP("https://attacker.invalid",self.c.runtime)

    def test_token_reused_and_private(self):
        cache=TokenCache(self.c,"test-identity")
        fetch=Mock(return_value=("secret-token",time.time()+3600))
        self.assertEqual(cache.get(fetch),cache.get(fetch))
        self.assertEqual(fetch.call_count,1)
        if os.name!="nt":self.assertEqual(cache.path.stat().st_mode & 0o777,0o600)

    def test_expired_token_refreshed(self):
        cache=TokenCache(self.c,"test")
        private_json(cache.path,{"token":"old","expires_at":time.time()-1})
        self.assertEqual(cache.get(lambda:("new",time.time()+3600)),"new")


if __name__ == "__main__":
    unittest.main()
