"""Compare implemented write payloads with locally downloaded official specs.

These are source-contract checks, not authenticated broker acceptance tests.
"""
import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trader.core import Config, Intent
from trader.brokers import Toss, KIS, Kiwoom


def main():
    ref = ROOT / "docs/reference"
    now = datetime.now(timezone.utc)
    intent = Intent("contract-check", "KR", "005930", "BUY", 1, "70000", now.isoformat(), (now+timedelta(hours=1)).isoformat())
    results = []
    toss = json.loads((ref / "toss-openapi.json").read_text())
    request = Toss(Config(ROOT / "config.json", "toss_live")).order_request(intent)
    schema = toss["paths"][request["path"]]["post"]["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = toss["components"]["schemas"][schema["$ref"].split("/")[-1]]
    schema = schema["oneOf"][0]
    assert set(schema["required"]) <= set(request["body"])
    assert set(request["body"]) <= set(schema["properties"])
    for key, value in request["body"].items():
        prop = schema["properties"][key]
        if "enum" in prop:
            assert value in prop["enum"], key
    results.append("Toss: quantity-based LIMIT/DAY fields and enums match canonical OpenAPI")
    kw = json.loads((ref / "kiwoom-spec.json").read_text())["apis"]
    for side in ("BUY", "SELL"):
        from dataclasses import replace
        request = Kiwoom(Config(ROOT / "config.json", "kiwoom_demo")).order_request(replace(intent, side=side))
        api = next(d for d in kw.values() if d["meta"]["API ID"] == request["api_id"])
        fields = api["request"]["body"]
        assert request["path"] == api["meta"]["URL"]
        assert {r["element"] for r in fields if r["required"] == "Y"} <= set(request["body"])
        assert set(request["body"]) <= {r["element"] for r in fields}
    results.append("Kiwoom: kt10000/kt10001 required fields and URLs match official JSON spec")
    source = (ref / "kis-domestic.py").read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "order_cash")
    parameter_dicts = [n.value for n in ast.walk(node) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict) and any(isinstance(t, ast.Name) and t.id == "params" for t in n.targets)]
    fields = {k.value for d in parameter_dicts for k in d.keys if isinstance(k, ast.Constant)}
    request = KIS(Config(ROOT / "config.json", "kis_demo")).order_request(intent)
    assert fields <= set(request["body"])
    tr_ids = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith(("TTTC", "VTTC"))}
    assert request["tr_id"] in tr_ids
    assert "TTTC0011U" in tr_ids and "TTTC0012U" in tr_ids
    results.append("KIS: body fields and 2026 transaction IDs match official order_cash sample")
    print(json.dumps({"checks": results, "passed": len(results), "authenticated": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
