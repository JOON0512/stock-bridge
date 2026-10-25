# Tests

```sh
python3 -m unittest discover -s tests -v
python3 -m trader demo
```

테스트는 모의 응답과 로컬 SQLite 원장을 사용합니다. 증권사 인증, 모의투자 서버, 실제 주문·체결은 아직 검증하지 않았습니다.

## Coverage

- 주문·취소 응답 유실 시 UNKNOWN 기록과 재전송 차단
- 재시작·동시 실행 시 같은 계좌의 중복 ID 차단
- 현금·수수료 여유·매도 가능 수량·허용 종목·금액·시각 검사
- NaN, Infinity, 음수·소수 수량, 시간대 없는 입력 거부
- 원화·달러 예산 분리와 계좌번호 정규화
- 페이지 이어받기, 키움 계좌 대조, 부호 붙은 시세 처리
- 토큰 캐시, 만료 갱신, 조회 재시도와 주문 비재시도
- 주문 목록에서 사라진 항목을 임의로 체결 처리하지 않음

## Contract checks

`scripts/check_contracts.py`는 별도로 준비한 공식 자료가 필요합니다.

- `docs/reference/toss-openapi.json`
- `docs/reference/kiwoom-spec.json`
- `docs/reference/kis-domestic.py`

참조한 버전과 해시는 [source-manifest.json](source-manifest.json)에 있습니다. 이 파일들 없이도 단위 테스트와 `demo`는 실행됩니다.

## Limits

로컬 모의 체결은 고정 가격을 사용하며 백테스트가 아닙니다. KIS·키움은 시세 체결시각과 공휴일 캘린더를 토스와 같은 방식으로 확인하지 않습니다. 키움의 취소·부분 체결 상태를 API로 확정할 수 없으면 수동 대조가 필요합니다.

잠금은 같은 `runtime`을 쓰는 로컬 프로세스에만 적용됩니다. 여러 서버의 분산 중복 방지, 평가손실 한도와 손절 모델은 없습니다. 실제 세금·수수료·체결가는 증권사 내역과 대조해야 합니다.
