# Toss

프로필: `toss_live`. 국내·미국 주식의 정수 수량 지정가 주문, 취소, 상태 조회를 지원합니다. 로컬 `paper`는 별도 어댑터입니다.

## 1. 계좌와 API 이용 가능 여부 확인

토스증권 웹에 평소 방식으로 로그인하고 **설정 → Open API**를 엽니다. 해당 메뉴에서 이용약관과 허용 범위를 확인하고 `client_id`, `client_secret`을 발급합니다. 계좌를 새로 만들 필요가 있는지는 현재 계좌의 이용 자격에 따라 확인해야 합니다. 공식 계좌 API는 현재 종합매매 계좌를 반환한다고 설명합니다. [공식 개요](https://openapi.tossinvest.com/openapi-docs/overview.md), [계좌 API 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)

## 2. 허용 IP 등록

같은 Open API 화면의 **허용 IP 관리**에 프로그램이 실행될 컴퓨터/서버의 공인 IP를 등록합니다. `192.168.x.x` 같은 공유기 내부 IP가 아닙니다. VPN, 다른 Wi-Fi, 모바일 핫스팟으로 바뀌면 외부 IP도 달라질 수 있습니다. 고정 IP 서버를 쓸 때는 서버 IP를 등록해야 합니다. [공식 시작 절차·FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)

키 발급 전에도 `demo`와 `preview`는 실행할 수 있습니다.

## 3. 비밀값을 로컬 파일에 입력

```bash
cd stock-bridge
cp -n .env.example .env
chmod 600 .env
cp -n config.json config.local.json
```

로컬 편집기로 `.env`의 아래 항목을 채웁니다. 키를 채팅이나 Git에 올리지 마세요.

```dotenv
TOSS_LIVE_CLIENT_ID=발급받은값
TOSS_LIVE_CLIENT_SECRET=발급받은값
TOSS_LIVE_ACCOUNT=
```

계좌 식별자는 다음 단계에서 확인합니다.

## 4. 계좌·잔고·시세 확인

```bash
python3 -m trader --config config.local.json --profile toss_live doctor
python3 -m trader --config config.local.json --profile toss_live accounts
```

`accounts` 결과에서 본인의 계좌번호를 대조하고 `accountSeq`를 `.env`의 `TOSS_LIVE_ACCOUNT`에 넣습니다. **실제 계좌번호 대신 accountSeq가 들어갑니다.** 아직 이 값이 비어 있어도 `accounts` 자체는 호출할 수 있습니다.

```bash
python3 -m trader --config config.local.json --profile toss_live holdings
python3 -m trader --config config.local.json --profile toss_live quote AAPL
python3 -m trader --config config.local.json --profile toss_live broker-orders
```

주식 평가액·달러 잔액이 아니라 API의 현금 주문가능금액이 주문 직전 검사에 쓰입니다.

## 5. 허용 종목과 예산 설정

`config.local.json`의 `profiles.toss_live`에서:

- `allowed_symbols`: 직접 고른 종목만 `KR:종목코드` 또는 `US:티커`로 등록합니다.
- `limits.max_order`: 1회 주문금액 상한. KRW와 USD를 별도로 설정합니다.
- `limits.max_daily_turnover`: 한국 시각 하루의 매수·매도 주문금액 합계 상한. 취소한 주문도 보수적으로 계속 계산합니다.
- `max_daily_orders`, `max_open_orders`: 일일 신규 주문 및 미체결 개수 상한입니다.
- `live_enabled`는 여기까지 `false`로 둡니다.

숫자는 JSON 문자열로 입력합니다. 예시 금액을 제공된 자산 규모에 맞는 권장 투자금으로 해석하지 마세요. 최초 실전 확인 금액은 직접 정해야 하므로 기본값은 0입니다.

## 6. 주문 파일을 만들고 미리보기

`examples/queue.template.json`을 `private/toss-order.json`으로 복사하고 다음을 직접 수정합니다.

| 필드 | 입력 |
|---|---|
| client_id | 이번 주문만의 ID. 같은 주문 재시도에 새 ID를 만들지 않음 |
| market | 국내 KR / 미국 US |
| symbol | 국내 6자리 코드 / 미국 티커 |
| side | BUY 또는 SELL |
| quantity | 정수 주식 수량 |
| price | 국내 원 또는 미국 달러 지정가 문자열 |
| not_before | 실행 시작 시각. 시간대 포함 |
| expires_at | 만료 시각. 시작부터 최대 24시간 |
| trigger | 즉시 실행 후보는 null. 가격 조건은 예: `{"op":"lte","price":"190"}` |

기본 양식은 2000년으로 만료되어 있으므로 그대로는 전송되지 않습니다. 실제 시세와 다른 예시 가격은 수정해야 합니다.

```bash
python3 -m trader --config config.local.json --profile toss_live preview --file private/toss-order.json
python3 -m trader --config config.local.json --profile toss_live preflight --file private/toss-order.json
```

`preview`는 키 없이도 요청 구조를 보여줍니다. `preflight`는 설정·시각을 점검하고 실제 잔고·시세를 조회하지만 주문하지 않습니다. 최종 한도 검사는 제출 직전에 다시 합니다. 토스 시세가 120초보다 오래되면 주문을 막고, 증권사 거래일 캘린더로 휴장·정규장 여부도 확인합니다.

## 7. 실전 활성화와 주문

종목·수량·가격·현금 한도를 확인하고 `profiles.toss_live.live_enabled`를 `true`로 바꿉니다. 아래 명령은 실제 돈을 쓰는 주문을 전송할 수 있습니다.

```bash
python3 -m trader --config config.local.json --profile toss_live submit --file private/toss-order.json --execute --confirm-live
```

`--execute`가 없으면 미리보기만 합니다. `--confirm-live` 또는 설정의 `live_enabled=true`가 없으면 실전 전송이 차단됩니다. 접수 후 출력의 `broker_id`와 토스 앱 주문번호/주문 상세를 대조하세요. 가격이 맞지 않으면 지정가는 미체결일 수 있습니다.

## 8. 체결·취소·잔고까지 확인

```bash
python3 -m trader --config config.local.json --profile toss_live sync 주문의_client_id
python3 -m trader --config config.local.json --profile toss_live broker-orders
python3 -m trader --config config.local.json --profile toss_live holdings
```

취소가 필요한 미체결 주문은:

```bash
python3 -m trader --config config.local.json --profile toss_live cancel 주문의_client_id --execute --confirm-live
python3 -m trader --config config.local.json --profile toss_live sync 주문의_client_id
```

취소 요청을 보냈다는 것과 취소 완료는 다릅니다. 부분 체결 수량은 취소 이후에도 계좌에 남습니다. 매도 기능 확인은 보유·매도가능 수량 범위에서 `side=SELL`인 **새 주문 ID**로 같은 절차를 거칩니다.

## 9. 정해진 규칙으로 계속 실행

한 번의 소규모 매수/매도/미체결 취소를 앱과 대조한 후에만 [운영 가이드](06-operations.md)의 `run` 명령으로 넘어갑니다. 미국 정규장 운용 시간은 서머타임을 고려해 뉴욕 시간으로 계산합니다. 시간외 거래나 소수점 거래는 현재 프로그램에서 보내지 않습니다.

토스의 `clientOrderId` 서버 중복 방지는 공식 명세상 10분입니다. 프로그램의 로컬 원장은 그보다 오래 같은 ID 재사용을 차단합니다. 토큰은 클라이언트당 하나만 유효하므로 국내·미국 프로세스를 별도로 만들더라도 이 프로젝트의 공통 캐시를 유지해야 합니다. [토스 공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)
