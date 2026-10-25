# Broker API notes

2026-09-20에 검토한 증권사 API의 실행 환경과 인증 방식. 현재 이용 조건은 각 공식 문서를 확인해야 합니다.

| API | 인터페이스 | 환경·연결 조건 |
| --- | --- | --- |
| 대신 CYBOS / CREON Plus | COM | Windows, 클라이언트 로그인 |
| 한국투자 KIS | REST, WebSocket | 실전·모의 키 분리, TR 코드 |
| 토스증권 | OAuth2, REST, WebSocket | 허용 IP, 계좌 식별자, 토큰 관리 |
| 키움 REST | REST, WebSocket | 계좌별 앱키, 허용 IP, 실전·모의 구분 |
| LS OPEN API | REST, WebSocket | xingAPI 사용등록, 실전·모의 키 |
| DB OPEN API | REST | 계좌·API 승인, 상품별 권한 |
| 키움 OpenAPI+ | ActiveX/COM | Windows, 클라이언트 로그인 |
| 신한 i indi | ActiveX | Windows, 사용계약 |
| Alpaca | REST, SDK | paper/live 구분, 거주지별 계좌 자격 |
| Interactive Brokers | TWS/IB Gateway, Web API | 계좌·데이터 권한, 세션 관리 |
| Saxo | OAuth, REST, streaming | live 앱 승인, 시세 라이선스 |

이 저장소에는 Toss, KIS, Kiwoom REST 어댑터를 구현했습니다. Toss는 국내·미국 정수 수량 지정가, KIS와 Kiwoom은 국내 KRX 현금 지정가를 지원하도록 작성되어 있습니다. 실제 API 연동은 별도 검증이 필요합니다.

하나의 계좌를 한 실행기에서 관리합니다. 응답을 받지 못한 주문을 다른 증권사로 자동 재전송하지 않습니다.

## References

[대신증권 CYBOS Plus](https://money2.daishin.com/E5/WTS/Customer/GuideTrading/DW_CybosPlus_Page.aspx?m=9508&p=8812&v=8632)

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
