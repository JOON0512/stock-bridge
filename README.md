# stock-bridge

증권사 API를 공통 인터페이스로 다루는 Python CLI. 주문 원장, 중복 주문 방지, 취소와 체결 상태 조회를 포함합니다.

토스·한국투자·키움 어댑터가 있으며 기본값은 네트워크를 쓰지 않는 로컬 모의 거래입니다. 인증된 증권사 API와 실제 주문은 아직 검증하지 않았습니다.

## Run

Python 3.11 이상. 기본 실행에는 외부 패키지가 필요 없습니다.

```sh
python3 -m trader doctor
python3 -m trader demo
python3 -m unittest discover -s tests -v
```

`demo`는 고정 가격으로 매수, 중복 차단, 부분 체결, 취소, 매도를 실행합니다. 결과는 `runtime/`에 저장됩니다.

## Brokers

| 어댑터 | 지원 주문 | 설정 |
| --- | --- | --- |
| Toss | 국내·미국 주식, 정수 수량 지정가 | [설정](docs/02-toss.md) |
| KIS | 국내 현금 주식, KRX 지정가 | [설정](docs/03-kis.md) |
| Kiwoom | 국내 현금 주식, KRX 지정가 | [설정](docs/04-kiwoom.md) |

실전 전송은 기본적으로 꺼져 있습니다. 로컬 모의 거래는 증권사의 모의투자 서버와 별개입니다.

## Code

- `trader/core.py` — 주문 검사, SQLite 원장, 상태 전이
- `trader/brokers.py` — 증권사별 어댑터
- `trader/transport.py` — HTTP와 토큰 관리
- `trader/cli.py` — 명령행, 주문 큐, 복구

전략 코드는 `Intent`를 만들고 `Engine.submit()`으로 전달합니다. 종목 선정이나 가격 예측 모델은 포함하지 않습니다.

`runtime/`에는 주문 ID와 한도 사용 기록이 있으므로 운용 중 삭제하면 안 됩니다. 설정과 운영 절차는 아래 문서를 참고하세요.

[API 비교](docs/01-research.md) · [연결 체크리스트](docs/05-morning-checklist.md) · [운영과 복구](docs/06-operations.md) · [테스트 범위](docs/07-verification.md)
