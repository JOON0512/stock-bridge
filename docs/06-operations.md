# 실행·복구·API 구조

## 세 가지 환경

| 모드 | 돈/시세 | 준비 |
|---|---|---|
| `paper` | 고정 시세·가상 현금, 네트워크 없음 | 바로 실행 |
| `kis_demo`, `kiwoom_demo` | 증권사 모의투자 서버 | 모의 계좌·별도 키 |
| `toss_live`, `kis_live`, `kiwoom_live` | 실제 계좌 | 실전 키·계좌·예산·명시적 실행 |

토스의 별도 모의 API 환경은 확인하지 못했습니다. `paper` 검증이 토스 서버의 실전 주문 수락을 증명하지 않습니다.

## 자동 실행

주문 후보 JSON 배열을 `private/queue.json`에 저장합니다. 각 주문은 고유 ID, 종목, 방향, 지정가, 수량, 시작시각, 만료시각을 가져야 합니다. 조건이 없으면 `trigger=null`, 예를 들어 가격이 임계값 이하일 때만 실행하려면 `{"op":"lte","price":"..."}`를 사용합니다. `gte`도 지원합니다.

```bash
# 기본: 한 번 읽고 요청 미리보기만 출력
python3 -m trader --config config.local.json --profile toss_live run --queue private/queue.json

# 아래는 실제 주문을 할 수 있는 장기 실행. 검증 후 본인이 실행합니다.
python3 -m trader --config config.local.json --profile toss_live run --queue private/queue.json --execute --confirm-live --poll 30 --max-cycles 0
```

`--max-cycles 0`은 계속 실행, 기본값 1은 한 차례 점검입니다. `--poll`은 최소 10초입니다. 각 주기마다 파일과 정책을 다시 읽고 이미 처리한 ID는 건너뜁니다. 확인 가능한 미완료 주문은 동기화합니다. 가격 조건은 매수·매도를 예측하지 않고 사용자가 정한 조건만 검사합니다. 지정가 자체가 현재가 괴리 한도(기본 3%) 안에 있어야 합니다.

새벽부터 계속 실행해도 허용 시각 전에는 주문하지 않습니다. 국내는 서울 시간 평일 09:05~15:15, 미국은 뉴욕 시간 평일 09:35~15:45로 제한합니다. 미국의 서머타임은 시스템 시간대 데이터로 계산합니다. 토스는 추가로 공식 거래일 캘린더를 확인합니다. KIS·키움은 공휴일/임시휴장 캘린더를 별도 연동하지 않았으므로 이 시간 창이 개장을 보장하지 않습니다.

실전 운용 중 새 입력 파일을 읽거나 `live_enabled` 설정을 끄면 다음 주기에 반영됩니다. 단일 주문의 최종 전송 직전에는 STOP와 만료시각을 재검사합니다. 실행 중 설정의 계좌·runtime 경로를 바꾸지 마세요. 변경하려면 프로세스를 멈추고 주문을 대조한 뒤 재시작합니다.

## 비상 정지와 취소

```bash
python3 -m trader --config config.local.json stop on
```

공통 `runtime/STOP`가 생성되어 해당 runtime을 쓰는 모든 프로필의 신규 주문을 막습니다. **이미 접수된 주문을 취소하는 기능은 아닙니다.** 네트워크 전송 직전에 정지가 걸리는 극히 짧은 경쟁 구간에는 이미 요청이 나갔을 수 있으므로 증권사 미체결 목록을 함께 확인하세요.

```bash
python3 -m trader --config config.local.json --profile toss_live broker-orders
python3 -m trader --config config.local.json --profile toss_live cancel 주문의_client_id --execute --confirm-live
python3 -m trader --config config.local.json --profile toss_live sync 주문의_client_id
```

STOP가 켜져 있어도 취소는 가능하게 만들었습니다. 단, 실전 활성화 및 명시적 취소 명령은 필요합니다. 프로그램을 끄는 Ctrl+C도 이미 접수된 주문을 취소하지 않습니다. 문제를 해결한 후 `stop off`로 신규 주문 차단을 해제합니다.

## 상태와 결과 불명 처리

```text
SUBMITTING → ACCEPTED → PENDING / PARTIAL_FILLED → FILLED
           ↘ REJECTED
           ↘ UNKNOWN

취소: CANCEL_SUBMITTING → CANCEL_REQUESTED → CANCELED 또는 FILLED
                         ↘ CANCEL_UNKNOWN
```

전송 전에 원장을 먼저 기록합니다. 프로그램이 죽거나 응답을 받지 못하면 `SUBMITTING`, `UNKNOWN`, `CANCEL_UNKNOWN` 등으로 남고 같은 계좌의 신규 주문을 차단합니다. 주문 API는 통신 오류·429·서버 오류가 나도 자동 반복하지 않습니다. 조회 API에만 제한적인 백오프 재시도를 적용합니다.

**결과 불명 주문에 새 ID를 붙여 다시 보내면 안 됩니다.** 먼저 증권사 앱과 `broker-orders`에서 주문일·계좌·종목·매수/매도·수량·가격·주문번호를 대조하세요.

```bash
python3 -m trader --config config.local.json --profile toss_live journal
python3 -m trader --config config.local.json --profile toss_live sync-all
python3 -m trader --config config.local.json --profile toss_live broker-orders --status CLOSED
```

로컬 기록에 주문번호가 있으면 `sync`가 원장 상태를 업데이트합니다. 주문번호까지 유실됐거나 키움의 당일 조회만으로 확정할 수 없는 상태면 사람이 직접 증권사 기록을 확인해야 합니다. 수동 확정은 해당 확인 근거와 함께 다음 명령으로 남길 수 있습니다.

```bash
python3 -m trader --config config.local.json --profile toss_live resolve 주문의_client_id --state CANCELED --evidence '증권사 앱에서 당일 주문번호와 부분체결 수량, 잔량취소 완료를 확인함' --acknowledge-manual-check
```

`FILLED`, `CANCELED`, `REJECTED` 중 증권사가 확인한 상태만 선택합니다. `REJECTED`는 접수/체결이 없었다고 확인한 경우에만 사용합니다. 부분 체결 후 취소는 `CANCELED`로 기록하면서 근거에 체결수량을 포함하세요. **이 명령은 증권사 주문을 바꾸지 않으며, 사람이 확인한 결과를 로컬 원장에 기록하는 복구 도구입니다.** 자동 대조와 구별해 감사 기록에 표시합니다.

키움은 미체결/당일 체결 조회에서 명확한 전량 체결만 확정하고, 사라진 주문을 완료로 추측하지 않습니다. KIS 조회는 기록된 주문일을 사용합니다. 오래된 주문·취소 불일치는 앱 내역 확인이 필요할 수 있습니다.

## 한도의 정확한 의미

- 한도 단위는 거래통화입니다. 원화와 달러를 임의 환율로 합치지 않습니다.
- 일일 금액은 서울 날짜 기준 **이 프로그램에서 요청한 매수+매도의 명목금액**입니다. 미체결·취소도 보수적으로 포함하고 명시적 거부만 제외합니다.
- 앱/HTS에서 따로 낸 주문은 로컬 일일 합계에 포함되지 않습니다. 다만 매번 조회하는 현재 구매력·매도가능수량·미체결 제한에는 영향을 줄 수 있습니다.
- 누적 총자산 한도, 평가손실 한도, 매도 후 자동 재투자 제한, 자동 손절은 아직 없습니다.
- 주문 직전 조회 이후 다른 앱이 주문하면 잔고가 달라질 수 있습니다. 최종 수락은 증권사가 판단합니다. 한 계좌를 여러 자동화가 동시에 사용하는 운용은 피하고 계좌 증거금 설정도 확인하세요.

## 안정적으로 켜 두기

현재는 서버나 자동 시작 서비스를 설치하지 않았습니다. 우선 터미널에서 한 프로세스로 실행하고 첫 주문을 관찰하세요. 실제 무인 운영으로 갈 때는 다음 순서입니다.

1. 테스트 계좌·금액으로 API 장애/재시작 복구까지 검증합니다.
2. 고정 IP 서버가 필요하면 요금과 실행 위치를 결정합니다.
3. 실행기를 멈추고 미확정 주문을 정리한 뒤 코드·config·원장을 이관합니다.
4. `.env`를 서버에 안전하게 입력하고 토스/키움의 허용 IP를 변경합니다.
5. 서버에서 `doctor → accounts → holdings → preflight`를 다시 확인합니다.
6. 하나의 서비스 프로세스로 실행하고 종료·재시작·로그 보존 정책을 붙입니다.

이관 중 Mac과 서버를 동시에 실전 실행하지 않습니다. 로컬 파일 잠금은 동일 머신/runtime에만 적용됩니다. 다른 컴퓨터 간의 중복 주문을 막는 분산 잠금은 구현하지 않았습니다. 별도 프로필·runtime을 만들거나 원장을 삭제하면 기존 주문 ID/한도 기록이 공유되지 않습니다.

## API 구현을 간단히 다시 설명

| 단계 | 토스 | 한국투자 | 키움 |
|---|---|---|---|
| 인증 | `/oauth2/token`, client_id/secret | `/oauth2/tokenP`, appkey/appsecret | `/oauth2/token`, appkey/secretkey |
| 계좌 전달 | `X-Tossinvest-Account: accountSeq` | 본문의 CANO/ACNT_PRDT_CD | 키에 연결된 계좌, ka00001로 검증 |
| 매수·매도 | `/api/v1/orders` + side | `/order-cash` + TR 코드 | `/api/dostk/ordr` + api-id |
| 지정가 | LIMIT + price | ORD_DVSN=00 | trde_tp=0 |
| 취소 | 주문 ID의 `/cancel` | 원주문번호+주문조직번호 | 원주문번호+잔량취소 수량 |
| 주문 추적 | orderId | ODNO | ord_no |

토큰은 만료 전까지 재사용하고, 만료하면 재발급합니다. 조회 요청이 HTTP 401로 실패하면 한 번 토큰을 새로 받아 조회를 다시 시도합니다. 주문 전송에는 이 자동 재시도를 적용하지 않습니다. 토스는 새 토큰이 이전 토큰을 무효화하므로 동일 계정의 다른 프로그램과 키를 공유할 때 주의해야 합니다.

통신은 공식 호스트만 허용하고 TLS 인증서 검증을 사용합니다. HTTP 리다이렉트는 따라가지 않습니다. 토큰 파일은 소유자만 읽도록 저장합니다. `.env` 파일은 실행자가 접근 권한을 관리해야 하며 암호화 저장소는 아닙니다. 네트워크 오류 로그에는 요청 본문·키·토큰을 출력하지 않습니다.

브로커별 토큰/키 오류가 JSON 업무 오류로 내려오는 경우에는 오류 코드를 표시하고 중단합니다. 모든 종류의 증권사 장애·약관 갱신·투자자 교육·상품별 권한 문제를 프로그램으로 해결할 수는 없습니다. 그런 문제는 [아침 체크리스트](05-morning-checklist.md)의 계좌/권한 절차로 돌아가 확인합니다.

## 전략을 나중에 교체하는 위치

현재 큐 파일이 전략 입력입니다. 향후 예측 모델은 `Intent`를 반환하는 함수 또는 큐 JSON 작성기로 연결하면 됩니다. 전략 모델이 직접 토큰을 받거나 주문 엔드포인트를 호출할 필요가 없습니다.

```python
# 예측 모델이 고른 후보도 동일한 위험검사를 통과해야 합니다.
intent = Intent(**candidate).validate()
engine.submit(intent, confirm_live=user_enabled_live)
```

고유 ID를 실행할 때마다 새로 만들면 같은 신호가 중복 주문이 될 수 있습니다. 전략명·거래일·신호 ID를 기준으로 안정적인 ID를 생성하고, 날짜/만료시간을 명확히 지정하세요.
