# 한국투자증권: 모의투자부터 실전 국내주식까지

구현 프로필: `kis_demo`, `kis_live`. 현재 코드는 **국내 KRX 현금 지정가 주문**입니다. 한국투자 서비스의 해외주식 API는 공식 제공되지만 이번 어댑터에는 연결하지 않았습니다.

## 1. 계좌 개설·ID 연결·API 신청

한국투자증권 계좌와 HTS ID가 필요합니다. 홈페이지/앱의 Open API 서비스 신청에서 실전 앱키·앱시크릿을 발급받습니다. 모의투자 참가 신청도 하고 **모의투자용 키를 별도로 발급**받습니다. [공식 서비스 이용안내](https://apiportal.koreainvestment.com/apiservice-summary), [공식 저장소의 시작 절차](https://github.com/koreainvestment/open-trading-api)

모의투자 계좌에 쓰는 키와 실계좌 키를 섞으면 인증·주문이 거부될 수 있습니다. 종합계좌번호 8자리와 상품코드 2자리를 따로 확인합니다. 계좌 비밀번호 자체는 이 코드의 요청 본문에 넣지 않습니다.

## 2. 로컬 설정 준비

```bash
cd stock-bridge
cp -n .env.example .env
chmod 600 .env
cp -n config.json config.local.json
```

`.env`에 모의 키부터 입력합니다.

```dotenv
KIS_DEMO_APP_KEY=모의앱키
KIS_DEMO_APP_SECRET=모의앱시크릿
KIS_DEMO_ACCOUNT=모의계좌8자리-상품코드2자리
```

처음에는 실전 항목을 비워 두어도 됩니다. 회사/학교 네트워크가 29443 포트나 9443 포트를 막는 경우 다른 허용된 네트워크에서 접속을 확인해야 합니다. 프로그램이 포트 제한을 우회하지 않습니다.

## 3. 인증과 조회부터 확인

```bash
python3 -m trader --config config.local.json --profile kis_demo doctor
python3 -m trader --config config.local.json --profile kis_demo accounts
python3 -m trader --config config.local.json --profile kis_demo holdings
python3 -m trader --config config.local.json --profile kis_demo quote 005930
```

KIS의 `accounts` 명령은 설정 계좌로 잔고 조회가 성공하는지를 검사합니다. 설정하지 않은 모든 계좌 목록을 가져오는 기능은 아닙니다. 샘플 종목코드는 API 확인 예시입니다.

## 4. 모의 주문 예산·양식 설정

`profiles.kis_demo.allowed_symbols`에 선택한 국내 종목을 `KR:005930` 같은 형식으로 넣고 `max_order.KRW`, `max_daily_turnover.KRW`를 **모의용 상한**으로 설정합니다. 다른 증권사의 실전 프로필 설정에는 영향을 주지 않습니다.

`examples/queue.template.json`을 `private/kis-demo-order.json`으로 복사합니다. ID를 새로 정하고 실제 모의투자 실행일의 `not_before`, `expires_at`, 지정가·수량을 입력합니다. `market=KR`, `side=BUY`, `trigger=null`로 시작하면 구조를 확인하기 쉽습니다.

```bash
python3 -m trader --config config.local.json --profile kis_demo preview --file private/kis-demo-order.json
python3 -m trader --config config.local.json --profile kis_demo preflight --file private/kis-demo-order.json
```

현재 엔진은 한국 시간 평일 09:05~15:15에 신규 주문을 허용합니다. KIS 캘린더 별도 연동은 없으므로 휴장 여부는 증권사 주문 응답도 확인해야 합니다. 장외·휴일 주문을 미리 예약하는 코드가 아닙니다.

## 5. 증권사 모의 서버에서 주문 수명주기 확인

```bash
python3 -m trader --config config.local.json --profile kis_demo submit --file private/kis-demo-order.json --execute
python3 -m trader --config config.local.json --profile kis_demo sync 주문의_client_id
python3 -m trader --config config.local.json --profile kis_demo broker-orders
```

모의투자 HTS/앱에서도 같은 주문이 보이는지 확인합니다. 미체결 수량이 남으면 다음을 실행하고 다시 대조합니다.

```bash
python3 -m trader --config config.local.json --profile kis_demo cancel 주문의_client_id --execute
python3 -m trader --config config.local.json --profile kis_demo sync 주문의_client_id
```

전량 체결되면 취소할 수 없습니다. 매도 검증은 보유수량 범위에서 `SELL`과 새로운 주문 ID를 사용합니다. 모의 서버에서 지원하지 않는 기능이 거부되면 실제 계좌로 자동 전환하지 않습니다.

## 6. 실전 계좌 준비와 키 분리

실전 프로필은 연결한 한국투자증권 계좌의 잔고를 사용합니다.

```dotenv
KIS_LIVE_APP_KEY=실전앱키
KIS_LIVE_APP_SECRET=실전앱시크릿
KIS_LIVE_ACCOUNT=실전계좌8자리-상품코드2자리
```

`kis_live`의 허용 종목·금액을 따로 설정합니다. 처음에는 `live_enabled=false`로 유지하고 아래 조회를 합니다.

```bash
python3 -m trader --config config.local.json --profile kis_live doctor
python3 -m trader --config config.local.json --profile kis_live accounts
python3 -m trader --config config.local.json --profile kis_live holdings
```

## 7. 실전 첫 주문과 체결·취소 검증

모의 주문 파일을 그대로 재사용하지 말고 새 ID·시각·가격으로 `private/kis-live-order.json`을 준비합니다. `preview`와 `preflight` 확인 후 `kis_live.live_enabled=true`로 바꿉니다.

```bash
python3 -m trader --config config.local.json --profile kis_live submit --file private/kis-live-order.json --execute --confirm-live
python3 -m trader --config config.local.json --profile kis_live sync 주문의_client_id
```

이때부터 실제 주문이 전송됩니다. 주문번호 `ODNO`와 취소에 필요한 주문조직번호를 원장에 저장합니다. 실전 취소는 정정취소가능주문 조회를 먼저 수행합니다.

```bash
python3 -m trader --config config.local.json --profile kis_live cancel 주문의_client_id --execute --confirm-live
python3 -m trader --config config.local.json --profile kis_live sync 주문의_client_id
python3 -m trader --config config.local.json --profile kis_live holdings
```

KIS 주문은 일반 현금주문 API를 사용하지만 계좌 증거금 설정에 따라 미수 여지가 있으므로, 코드에서는 매수 가능 조회의 **미수 없는 금액·수량**을 검사합니다. 계좌 자체도 현금 100% 운용 설정을 확인해 두면 별도 HTS 주문과 섞일 때의 혼동을 줄일 수 있습니다. [공식 주문·매수 가능 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions.py)

## 8. 자동 실행으로 확장

모의/실전 검증이 끝나면 [공통 운영 절차](06-operations.md)의 큐 실행기를 사용합니다. `kis_demo`와 `kis_live`는 주소·토큰·원장을 분리합니다. 최신 공식 예제의 현금 매수/매도 TR `TTTC0012U`/`TTTC0011U`, 취소 `TTTC0013U`를 사용했고 모의는 대응되는 `V` 접두 TR을 사용합니다. 몇 년 전 블로그의 TR 코드와 다를 수 있어 공식 코드 버전을 기록했습니다.
