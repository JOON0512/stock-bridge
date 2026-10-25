# 키움증권 REST: 계좌 신청부터 실전 국내주식까지

프로필: `kiwoom_demo`, `kiwoom_live`. 키움 REST API로 국내 KRX 현금 지정가 주문을 처리합니다.

## 1. 계좌와 REST API 신청

키움 계좌가 필요합니다. [키움 REST 포털](https://openapi.kiwoom.com/)의 **API 사용신청**에서 로그인하고 안내된 약관·동의 절차를 진행합니다. 예전 OpenAPI+ 신청과 REST API 키 발급을 혼동하지 마세요. [공식 이용안내](https://openapi.kiwoom.com/intro/serviceInfo)

## 2. 모의투자 가입·허용 IP·계좌 등록

모의투자는 별도 참가 신청이 필요합니다. REST 포털의 **모의투자 App Key 관리**에서 허용 IP와 모의 계좌를 등록하고 키를 발급합니다. 실전은 **계좌 App Key 관리**에서 별도로 진행합니다. 공식 문서상 허용된 IP에서만 API 인증이 가능합니다. 실행할 Mac 또는 서버의 공인 IP를 등록하세요. [공식 신청 절차](https://openapi.kiwoom.com/intro/serviceInfo), [모의투자 안내](https://openapi.kiwoom.com/intro/mockInvestInfo?dummyVal=0)

모의 국내주식의 매매·계좌 조회는 KRX 기준으로 검증합니다. 이 어댑터도 KRX만 사용합니다.

## 3. 설정 파일 준비

```bash
cd stock-bridge
cp -n .env.example .env
chmod 600 .env
cp -n config.json config.local.json
```

`.env`에 입력:

```dotenv
KIWOOM_DEMO_APP_KEY=모의앱키
KIWOOM_DEMO_APP_SECRET=모의시크릿
KIWOOM_DEMO_ACCOUNT=앱키에연결된10자리계좌번호
```

## 4. 앱키가 연결된 계좌부터 확인

```bash
python3 -m trader --config config.local.json --profile kiwoom_demo doctor
python3 -m trader --config config.local.json --profile kiwoom_demo accounts
python3 -m trader --config config.local.json --profile kiwoom_demo holdings
python3 -m trader --config config.local.json --profile kiwoom_demo quote 005930
```

`accounts`는 공식 `ka00001`을 사용하여 **현재 토큰의 계좌번호**를 확인합니다. 주문 전 계좌가 `.env`와 다르면 전송을 막습니다. 잔고 API의 `A005930` 형태 코드와 시세의 `+`/`-` 부호도 처리했습니다. 가격의 부호는 음수 주가가 아니라 전일 대비 방향 표기일 수 있어 절댓값으로 해석합니다.

## 5. 모의 주문 파일과 한도 설정

`config.local.json`의 `profiles.kiwoom_demo`에서 선택한 종목만 허용하고 KRW 주문 한도를 입력합니다. 기본 한도는 0입니다.

주문 양식을 `private/kiwoom-demo-order.json`으로 복사해 고유 ID·종목·수량·지정가·실행 시작/만료 시각을 입력합니다. `side=BUY`, `market=KR`로 구조를 확인한 후 아래를 실행합니다.

```bash
python3 -m trader --config config.local.json --profile kiwoom_demo preview --file private/kiwoom-demo-order.json
python3 -m trader --config config.local.json --profile kiwoom_demo preflight --file private/kiwoom-demo-order.json
```

미리보기에는 매수 API `kt10000`, 경로 `/api/dostk/ordr`, 지정가 구분 `trde_tp=0` 등이 표시됩니다. 매수 가능 확인은 `kt00010`의 주문가능현금과 증거금 100% 주문가능금액/수량을 사용합니다. [공식 JSON 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/_data/kiwoom_api_spec.json)

## 6. 모의 주문·체결·취소·매도 검증

```bash
python3 -m trader --config config.local.json --profile kiwoom_demo submit --file private/kiwoom-demo-order.json --execute
python3 -m trader --config config.local.json --profile kiwoom_demo broker-orders
python3 -m trader --config config.local.json --profile kiwoom_demo sync 주문의_client_id
```

미체결 수량이 남으면:

```bash
python3 -m trader --config config.local.json --profile kiwoom_demo cancel 주문의_client_id --execute
python3 -m trader --config config.local.json --profile kiwoom_demo sync 주문의_client_id
```

키움 취소 요청은 `kt10003`, 잔량 전체는 `cncl_qty=0`을 사용합니다. **미체결 목록에서 사라졌다는 이유만으로 전량 체결이나 취소 완료라고 추정하지 않습니다.** 현재 어댑터의 당일 체결 조회로 명확히 확정할 수 없는 경우에는 앱과 대조해 수동 확정해야 합니다. 특히 취소·부분 체결 후 종료 주문이 여기에 해당할 수 있습니다. 이것이 세 후보 중 키움을 주력보다 국내 대안으로 둔 이유 중 하나입니다.

모의 매도 확인은 실제로 보유한 현금 주식 수량 안에서 `SELL` 주문 파일을 만들고 새 ID로 실행합니다. 신용구분 `00`인 현금 보유분만 매도가능수량 집계에 사용합니다.

## 7. 실전 계좌·키·검증용 현금 준비

실전 App Key 관리에서 키와 IP를 등록하고 실계좌 정보를 `.env`에 입력합니다.

```dotenv
KIWOOM_LIVE_APP_KEY=실전앱키
KIWOOM_LIVE_APP_SECRET=실전시크릿
KIWOOM_LIVE_ACCOUNT=실전10자리계좌번호
```

`kiwoom_live`의 허용 종목과 금액은 모의 프로필과 별도로 직접 설정합니다. 실전 계좌 잔고 조회가 성공하고 계좌번호가 일치하는지 확인합니다.

```bash
python3 -m trader --config config.local.json --profile kiwoom_live accounts
python3 -m trader --config config.local.json --profile kiwoom_live holdings
```

## 8. 실전 주문

새 주문 ID·당일 시각·지정가로 `private/kiwoom-live-order.json`을 만들고 `preview`, `preflight`를 확인합니다. `kiwoom_live.live_enabled=true`로 바꾼 후:

```bash
python3 -m trader --config config.local.json --profile kiwoom_live submit --file private/kiwoom-live-order.json --execute --confirm-live
python3 -m trader --config config.local.json --profile kiwoom_live sync 주문의_client_id
```

위 `submit`은 실제 주문입니다. 필요하면 `cancel ... --execute --confirm-live`로 취소를 요청하고 앱에서 미체결·체결수량·현금을 대조합니다. 주문번호는 날짜를 넘어 재사용될 수 있어 이 코드가 전일 주문번호로 취소하지 않도록 제한했습니다.

## 9. 무인 실행으로 넘어가기

인증·모의·실전의 작은 검증을 마친 다음 [공통 운영 가이드](06-operations.md)를 따릅니다. 현재 시간 제한은 한국 평일 09:05~15:15이며 휴장일 별도 캘린더는 이 어댑터에 연결하지 않았습니다. 증권사 거부 응답을 처리하며 장외 예약을 만들지 않습니다. 공식 서비스 장애·허용 IP 변경·약관 갱신이 있으면 확인 후 재개해야 합니다.
