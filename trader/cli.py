from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import Config, Engine, Intent, TradingError, Uncertain, file_lock, read_intents, stamp
from .brokers import Paper, make_broker


def output(data):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str), flush=True)


def doctor(c):
    names = {"paper": [], "toss": ["CLIENT_ID", "CLIENT_SECRET", "ACCOUNT"], "kis": ["APP_KEY", "APP_SECRET", "ACCOUNT"], "kiwoom": ["APP_KEY", "APP_SECRET", "ACCOUNT"]}[c.broker]
    missing = [f"{c.profile['env_prefix']}_{n}" for n in names if not c.secret(n, optional=True)]
    return {"profile": c.name, "broker": c.broker, "mode": c.mode, "python": sys.version.split()[0], "missing_settings": missing, "live_enabled": c.profile.get("live_enabled", False), "allowed_symbols": c.profile.get("allowed_symbols", []), "limits": c.limits, "stop_active": (c.runtime / "STOP").exists(), "network_checked": False, "next": "인증값을 .env에 입력 → accounts → holdings → preflight → 직접 실전 활성화"}


def demo(c):
    if c.broker != "paper":
        raise TradingError("demo는 paper 프로필에서만 실행됩니다.")
    b = Paper(c)
    e = Engine(c, b)
    now = datetime.now(timezone.utc)
    prefix = now.strftime("demo-%Y%m%d%H%M%S%f")
    common = dict(market="KR", symbol="005930", price="70000", not_before=(now - timedelta(seconds=2)).isoformat(), expires_at=(now + timedelta(hours=1)).isoformat())
    buy = Intent(client_id=prefix+"b", side="BUY", quantity=2, **common)
    events = []
    events.append({"step": "매수 접수", "result": e.submit(buy)})
    events.append({"step": "중복 전송 차단", "result": e.submit(buy)})
    with file_lock(c.runtime / "execution.lock"):
        b.fill(buy.client_id, 1)
    events.append({"step": "1주 부분 체결", "result": e.sync(buy.client_id)})
    events.append({"step": "미체결 잔량 취소", "result": e.cancel(buy.client_id)})
    events.append({"step": "취소 확인", "result": e.sync(buy.client_id)})
    sell = Intent(client_id=prefix+"s", side="SELL", quantity=1, **common)
    events.append({"step": "보유 1주 매도 접수", "result": e.submit(sell)})
    with file_lock(c.runtime / "execution.lock"):
        b.fill(sell.client_id, 1)
    events.append({"step": "매도 체결 확인", "result": e.sync(sell.client_id)})
    events.append({"step": "최종 현금·주식", "result": {"account": b.accounts(), "holdings": b.holdings()}})
    return {"simulated": True, "real_orders_sent": 0, "note": "고정 가격, 수수료·세금·슬리피지 없는 기능 검증용 모의 거래", "steps": events}


def main():
    p = argparse.ArgumentParser(description="공식 API 주식 주문 실행기. 기본은 오프라인 미리보기입니다.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--profile", default="paper")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "accounts", "holdings", "journal", "demo", "sync-all"):
        sub.add_parser(name)
    q = sub.add_parser("quote"); q.add_argument("symbol")
    q = sub.add_parser("broker-orders"); q.add_argument("--status", choices=["OPEN", "CLOSED"], default="OPEN")
    for name in ("preview", "preflight", "submit"):
        q = sub.add_parser(name); q.add_argument("--file", required=True)
        if name == "submit":
            q.add_argument("--execute", action="store_true"); q.add_argument("--confirm-live", action="store_true")
    q = sub.add_parser("run")
    q.add_argument("--queue", required=True); q.add_argument("--execute", action="store_true"); q.add_argument("--confirm-live", action="store_true")
    q.add_argument("--poll", type=int, default=30); q.add_argument("--max-cycles", type=int, default=1)
    for name in ("sync", "cancel"):
        q = sub.add_parser(name); q.add_argument("client_id")
        if name == "cancel":
            q.add_argument("--execute", action="store_true"); q.add_argument("--confirm-live", action="store_true")
    q = sub.add_parser("stop"); q.add_argument("action", choices=["on", "off"])
    q = sub.add_parser("paper-fill"); q.add_argument("client_id"); q.add_argument("quantity", type=int)
    q = sub.add_parser("resolve")
    q.add_argument("client_id"); q.add_argument("--state", required=True, choices=["FILLED", "CANCELED", "REJECTED"])
    q.add_argument("--evidence", required=True); q.add_argument("--acknowledge-manual-check", action="store_true")
    args = p.parse_args()
    try:
        c = Config(args.config, args.profile)
        if args.command == "doctor":
            output(doctor(c)); return
        if args.command == "stop":
            if args.action == "on":
                (c.runtime / "STOP").write_text(stamp())
            else:
                (c.runtime / "STOP").unlink(missing_ok=True)
            output({"stop_active": args.action == "on", "note": "신규 주문 차단. 이미 증권사에 접수된 주문은 개별 취소 필요"}); return
        if args.command == "demo":
            output(demo(c)); return
        b = make_broker(c)
        e = Engine(c, b)
        if args.command in ("accounts", "holdings"):
            output(getattr(b, args.command)())
        elif args.command == "quote":
            output(b.prices(args.symbol))
        elif args.command == "broker-orders":
            output(b.orders(args.status))
        elif args.command == "journal":
            output(e.journal.rows())
        elif args.command == "paper-fill":
            if not isinstance(b, Paper):
                raise TradingError("paper-fill은 오프라인 모의 거래 전용입니다.")
            with file_lock(c.runtime / "execution.lock"):
                output(b.fill(args.client_id, args.quantity))
        elif args.command == "sync":
            output(e.sync(args.client_id))
        elif args.command == "sync-all":
            output(e.sync_active())
        elif args.command == "cancel":
            if not args.execute:
                output({"preview": True, "client_id": args.client_id, "sent": False})
            else:
                output(e.cancel(args.client_id, args.confirm_live))
        elif args.command == "resolve":
            if not args.acknowledge_manual_check:
                raise TradingError("증권사 주문내역을 직접 확인한 뒤 --acknowledge-manual-check를 지정하세요.")
            output(e.resolve_manually(args.client_id, args.state, args.evidence))
        elif args.command in ("preview", "preflight", "submit"):
            intents = read_intents(args.file)
            for i in intents:
                if args.command == "preflight":
                    b.supports(i)
                    e._risk(i)
                    output({"client_id": i.client_id, "broker_checks": b.preflight(i), "sent": False, "note": "최종 위험검사는 submit 직전에 다시 수행됩니다."})
                elif args.command == "submit" and args.execute:
                    output(e.submit(i, args.confirm_live))
                else:
                    output(e.preview(i))
        elif args.command == "run":
            if args.poll < 10 or args.max_cycles < 0:
                raise TradingError("poll>=10, max-cycles>=0 필요")
            if not args.execute:
                output([e.preview(i) for i in read_intents(args.queue)]); return
            c.enabled(args.confirm_live)
            cycle, last = 0, {}
            while True:
                # Reload policy changes as well as queue changes at every cycle.
                c = Config(args.config, args.profile)
                c.enabled(args.confirm_live)
                b = make_broker(c)
                e.journal.db.close()
                e = Engine(c, b)
                if (c.runtime / "STOP").exists():
                    output({"stopped": True}); return
                for result in e.sync_active():
                    key = "sync:" + result.get("client_id", "unknown")
                    # Print meaningful state changes, not timestamps from routine polling.
                    meaningful = {k: v for k, v in result.items() if k not in ("updated_at", "created_at")}
                    encoded = json.dumps(meaningful, sort_keys=True)
                    if last.get(key) != encoded:
                        output(result)
                        last[key] = encoded
                for i in read_intents(args.queue):
                    if e.journal.get(i.client_id):
                        continue
                    try:
                        result = e.submit(i, args.confirm_live)
                    except Uncertain:
                        raise  # halt runner immediately; do not advance to another order
                    except TradingError as error:
                        result = {"client_id": i.client_id, "blocked": str(error)}
                    encoded = json.dumps(result, sort_keys=True)
                    if last.get(i.client_id) != encoded:
                        output(result)
                        last[i.client_id] = encoded
                cycle += 1
                if args.max_cycles and cycle >= args.max_cycles:
                    break
                time.sleep(args.poll)
    except (TradingError, OSError, ValueError, KeyError) as error:
        output({"error": str(error), "type": type(error).__name__})
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        output({"stopped": True, "note": "이미 접수된 주문은 증권사에 남아 있습니다."})


if __name__ == "__main__":
    main()
