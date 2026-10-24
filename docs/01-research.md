# 대신증권 외 자동매매 구현 방법 10가지

조사일: 2026-09-20. 종목 추천이 아니라 실행 인프라 비교입니다. "안 막힌다"는 보장은 할 수 없고, 아래 평가는 공식 문서의 설치·인증·운영 조건과 Mac 환경과 기존 계좌를 활용하는 시나리오에 따른 기술적 판단입니다. 광고·블로그 설명보다 증권사 공식 문서와 공식 저장소를 우선했습니다.

## 기준: 대신증권도 여전히 가능

대신증권 **CYBOS Plus / CREON Plus** 계열은 시세·잔고·매수·매도·정정·취소를 프로그램에서 다루는 방식입니다. 공식 설명은 Windows COM Object 환경을 전제로 합니다. Python COM 연동으로 구현할 수 있지만 지금의 Mac에서 무인 운용하려면 별도 Windows 실행 환경, 로그인, 설치 구성 등을 관리해야 합니다. 프로그램을 처음 만드는 현재 상황에서는 REST API보다 운영 요소가 많습니다. [대신증권 공식 서비스 설명](https://money2.daishin.com/E5/WTS/Customer/GuideTrading/DW_CybosPlus_Page.aspx?m=9508&p=8812&v=8632)

요청의 "외에도 10가지"를 충족하도록 대신증권은 위 기준으로 별도 설명하고, 아래에 **다른 방법 10가지**를 정리했습니다. 같은 증권사의 REST와 COM은 설치·인증·운영 방식이 달라 별도 방식으로 셌습니다.

| 번호 | 구현 방법 | 직접 구현하는 경로 | 장점 | 중간에 막힐 수 있는 지점 | 이번 판단 |
|---|---|---|---|---|---|
| 1 | 한국투자 KIS Open API | HTTPS REST + 필요 시 WebSocket | 국내·해외 API, 공식 Python 예제, 실전/모의 구분 | 계좌·API 신청, 별도 키, 거래별 TR 코드·페이지네이션 | **선정: 검증 절차를 갖춰 시작하기 좋음** |
| 2 | 토스증권 Open API | OAuth2 + REST + 필요 시 WebSocket | 기존 토스 계좌 사용, 국내·미국, 서버 OpenAPI 명세 | 키·허용 IP, 토큰 1개 규칙, 별도 모의 서버 미확인 | **선정: 사용자에게 준비 절차가 가장 적을 가능성** |
| 3 | 키움 REST API | HTTPS REST + 필요 시 WebSocket | Mac/Linux에서 모듈 없이 요청, 공식 JSON·CLI·예제 | 계좌별 앱키, 허용 IP, 모의/실전 키 분리 | **선정: 국내주식 대안** |
| 4 | LS증권 OPEN API | REST + WebSocket | 모듈에 종속되지 않는 API, 실전/모의 키 | 공식 절차상 xingAPI 사용등록을 먼저 해야 함, 인증서 신청 절차 | 유력한 4순위 |
| 5 | DB증권 OPEN API | REST 중심 계좌·시세·주문 API | 국내·해외 주문·시세 공식 제공 | 새 계좌·API 승인·계좌별 지원 범위 검증 필요 | 예비 후보 |
| 6 | 키움 OpenAPI+ | Windows ActiveX/COM, Python 또는 C# | 오래된 국내 자동매매 생태계, 조건검색 | Windows/모듈/로그인 관리, Mac 별도 실행 환경 | 기존 Windows 코드가 있을 때 유리 |
| 7 | 신한 i indi | ActiveX 인터페이스, Python/C# 등 | 공식 시세·주문 프로그램 예제 | 사용계약·Windows 클라이언트 환경 관리 | 현재 Mac 신규 구축 우선순위 낮음 |
| 8 | Alpaca Trading API | REST 또는 공식 SDK, paper/live | 미국 주식 자동화 친화적, 별도 paper 환경 | 한국 거주자의 실계좌 개설 가능 여부·입금·상품 제공 조건 별도 확인 | 해외 계좌 조건 충족 시 후보 |
| 9 | Interactive Brokers API | TWS/IB Gateway API 또는 Web API | 해외 시장과 다양한 상품 | 계좌 개설·입금·데이터 권한·세션 인증·Gateway 운용 | 다국가 확장 단계에 적합 |
| 10 | Saxo OpenAPI | OAuth + REST + streaming | 주문·계좌·상품 검색을 포괄하는 API | 거주지별 계좌 제공, live 앱 승인, 시세 라이선스 | 한국 사용자 즉시 실행 가능성 미확정 |

각 항목의 공식 근거:

1. [KIS 서비스 개요](https://apiportal.koreainvestment.com/apiservice-summary), [한국투자 공식 Python 저장소](https://github.com/koreainvestment/open-trading-api)
2. [토스 개발자 문서](https://developers.tossinvest.com/docs), [서버 제공 개요](https://openapi.tossinvest.com/openapi-docs/overview.md), [공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)
3. [키움 REST 이용안내](https://openapi.kiwoom.com/intro/serviceInfo), [공식 API 가이드](https://openapi.kiwoom.com/guide/apiguide), [공식 저장소](https://github.com/Kiwoom-Securities/Kiwoom-REST-API)
4. [LS 공식 이용안내](https://openapi.ls-sec.co.kr/howto-use)
5. [DB 공식 포털의 국내·해외 주문 API](https://openapi.dbsec.co.kr/intro)
6. [키움 OpenAPI+ 공식 소개](https://www3.kiwoom.com/h/customer/download/VOpenApiInfoView?dummyVal=0), [공식 개발 가이드](https://download.kiwoom.com/web/openapi/kiwoom_openapi_plus_devguide_ver_1.7.pdf)
7. [신한 i indi 공식 소개·신청·예제](https://www.shinhansec.com/siw/customer-center/channel/channel_homeTrading-indi/contents.do)
8. [Alpaca 인증과 실전/모의 주소](https://docs.alpaca.markets/us/docs/authentication), [공식 SDK 안내](https://docs.alpaca.markets/us/docs/sdks-and-tools)
9. [IBKR Web API 개인 계좌 요건](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/), [API 포털](https://ibkrcampus.com/campus/ibkr-api-page/)
10. [Saxo API 개요](https://www.developer.saxo/openapi/learn), [주문 튜토리얼](https://www.developer.saxo/openapi/learn/the-tutorial), [시세 라이선스와 상품 개념](https://www.developer.saxo/openapi/learn/core-business-concepts)

## 왜 이 세 가지를 골랐나

**한국투자: 모의 검증과 공식 예제를 중시할 때.** 계좌만 마련되면 Mac에서 바로 HTTP 요청으로 구현할 수 있고, 동일한 프로그램을 모의 환경에서 먼저 점검할 수 있습니다. 기존 토스의 자금을 이용하려면 별도 한국투자 계좌에 현금이 있어야 합니다. 이번 KIS 코드에는 국내 현금주문을 연결했습니다.

**토스: 기존 토스 계좌를 연결하는 경우의 선택지.** 현재 공개 문서에는 국내·미국 주문, 현금 구매력, 판매 가능 수량, 주문 상세가 포함되어 있습니다. 모의 환경은 확인되지 않아 로컬 모의 검증과 실제 계좌 소규모 검증을 구분해야 합니다. 실제 API가 계좌에 제공되고 허용 IP 등록을 마칠 수 있다는 조건에서 준비 부담이 가장 적습니다.

**키움 REST: 국내주식 실행 경로를 하나 더 확보.** 과거 Windows OpenAPI+만 떠올릴 필요가 없습니다. REST 서비스는 별도의 Windows 모듈 없이 연결할 수 있고, 공식 명세·샘플도 있습니다. 공식 서비스에 미국주식 항목도 확인했지만 이번 구현은 국내 KRX 현금주문으로 제한했습니다. 기존 종목 선정 모델이 없는 지금은 기능을 넓히기보다 주문 수명주기를 먼저 확인하기에 적합합니다.

**세 개를 동시에 운용할 필요는 없습니다.** 토스 API 신청이 가능하면 기존 계좌 연결부터, 모의 서버 검증을 먼저 하고 싶다면 KIS부터 선택하세요. 한 증권사가 응답하지 않는다고 다른 증권사에서 같은 주문을 자동 실행하는 기능은 넣지 않았습니다. 첫 주문이 이미 접수됐는지 확인되지 않은 상태에서는 중복 보유가 생길 수 있기 때문입니다.

## 운영 환경 선택

초기 검증은 현재 Mac에서 충분합니다. 밤에도 계속 돌리려면 컴퓨터가 켜져 있고 네트워크·프로세스가 살아 있어야 합니다. 노트북 절전·닫힌 덮개·유동 IP는 무인 운영의 실패 원인이 됩니다. 안정적으로 운용할 단계에는 고정 공인 IP가 있는 Linux 서버 한 대가 관리하기 쉽다는 판단입니다. 서버 계약·비용 지출·키 업로드는 이번에 진행하지 않았습니다.

UI 버튼을 클릭하는 RPA, 비공개 앱 내부 API를 추측하는 방식은 상위 후보에서 제외했습니다. 공식 API로 주문 가능한 경로가 확인되어, 화면 변경과 비공개 인증에 의존할 필요가 적습니다.
