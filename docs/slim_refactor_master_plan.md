# Slim Refactor Master Plan

작성일: 2026-06-24
대상 저장소: `hoonoh57/infra`, `hoonoh57/rest_base`
목표 프로젝트: Kiwoom REST 기반 VB.NET 조건식 멀티차트 자동매매 실험실

---

## 1. 핵심 원칙

이번 작업은 새 자동매매앱을 처음부터 대량 생성하는 작업이 아니다.

목표는 기존에 역기저기 개발되어 비대해진 로직 중에서 실제로 성공한 로직만 발췌하고, 이를 작은 단위 실험실에서 검증한 뒤 메인 로직으로 승급시키는 것이다.

핵심 문장:

```text
기존 성공 로직은 신뢰하되, 기존의 뒤엉킨 구조는 그대로 신뢰하지 않는다.
성공한 계산식, 렌더링, 조건검색, 전략판단만 발췌하고,
API, UI, 차트, 전략, 주문이 섞인 구조는 작은 단위로 재조립한다.
```

---

## 2. 최종 방향

최종 목표는 다음 구조다.

```text
Kiwoom REST / WebSocket
        ↓
Gateway Layer
        ↓
Condition / Tick / Order EventBus
        ↓
CandleBuilder + IndicatorEngine
        ↓
StrategyLab / ScoreEngine / RiskGate
        ↓
ChartControlLite + MultiChart UI
        ↓
Main Auto Trading App
```

단, 이 전체 구조를 한 번에 만들지 않는다.

반드시 다음 순서로 진행한다.

```text
1. 기존 코드 인벤토리
2. 성공 로직 적출
3. 단위 실험실 구성
4. 단위 검증
5. Candidate 승급
6. Promoted 승급
7. Main 편입
8. 통합 검증
```

---

## 3. 고정영역과 가변영역

### 3.1 고정영역

고정영역은 한 번 검증되면 쉽게 바뀌지 않는 기반이다.

```text
- Candle 모델
- Tick 모델
- IndicatorResult 모델
- StrategySignal 모델
- ChartRenderModel
- ChartOverlay 모델
- EventBus 인터페이스
- REST Gateway 인터페이스
- Repository 인터페이스
- FormulaSpec / StrategySpec 구조
- 차트 렌더링 루프
- 차트 viewport / crosshair / marker 처리
```

고정영역에는 특정 조건식 이름, 특정 종목 코드, 특정 전략 파라미터, 특정 매매일자, 특정 화면 버튼 로직을 넣지 않는다.

### 3.2 가변영역

가변영역은 실험을 통해 계속 바뀌는 부분이다.

```text
- 조건검색식 선택
- 포착 후 쿨다운 기준
- 거래대금 기준
- Top N 기준
- 스코어링 공식
- BB / RSI / ST / JMA 파라미터
- 진입 수식
- 청산 수식
- 손절 / 트레일링 정책
- 시장국면 필터
- 종목군 필터
```

가변영역은 JSON, DB, 설정 파일, 전략 관리자 화면에서 바뀔 수 있어야 한다.

---

## 4. 금지 규칙

코드지옥을 피하기 위해 다음을 금지한다.

```text
1. Form 안에서 REST 호출 금지
2. ChartControl 안에서 전략 평가 금지
3. StrategyEngine 안에서 주문 호출 금지
4. Indicator 안에서 UI 접근 금지
5. REST 수신 이벤트에서 직접 차트 갱신 금지
6. 수식 문자열을 실시간 루프에서 매번 파싱 금지
7. 검증 안 된 로직을 Main에 직접 병합 금지
8. 기존 성공 파일에 기능을 계속 덧붙이는 방식 금지
9. API 연결 전 차트/지표/전략 단위 검증 생략 금지
10. 단위 실험 없이 전체 자동매매앱부터 구성 금지
```

---

## 5. 실험실 우선순위

### Phase 0. 인벤토리

기존 파일을 다음 기준으로 분류한다.

```text
A: 바로 적출 가치 있음
B: 일부 함수만 적출
C: 참조 후 재작성
D: 폐기
```

### Phase 1. ChartLab

목표:

```text
- API 없이 Candle List만 넣어 차트 렌더링
- IndicatorSeries 주입 시 보조지표 표시
- SignalMarker 주입 시 Buy/Sell 표시
- Crosshair / wheel / drag 정상 작동
```

적출 후보:

```text
infra/MainApp/ChartEngine/Core/FastChartControl.vb
```

분리 방향:

```text
FastChartControl
→ ChartControlLite
→ ChartRenderer
→ ChartViewportState
→ ChartOverlayModel
```

### Phase 2. IndicatorLab

1차 지표:

```text
- BollingerBands
- RSI
- SuperTrend
- TickIntensity
- VWAP 또는 VWMA
```

적출 후보:

```text
infra/MainApp/ChartEngine/Indicators/TickIntensity_Indicator.vb
```

분리 방향:

```text
TickIntensity_Indicator
→ TickIntensityCore
→ TickBucketBuffer
→ TickIntensityIndicatorAdapter
```

### Phase 3. FormulaLab

목표:

```text
키움 수식관리자 / TradingView식 수식
→ FormulaSpec
→ StrategyEvaluatorLite
```

초기 지원 문법:

```text
c, o, h, l, v
avg(x,n)
rsi(n)
bbandsup(n,d)
crossover(a,b)
crossunder(a,b)
and / or / not
>, >=, <, <=
```

원칙:

```text
수식 문자열은 최초 1회만 파싱한다.
실시간 루프에서는 AST 또는 FormulaSpec만 평가한다.
```

### Phase 4. ConditionLab

목표:

```text
- 조건식 목록 조회
- 조건식 단발 실행
- 실시간 편입/이탈 이벤트 수신
- CandidateStore 반영
- 중복/쿨다운 처리
```

초기 순서:

```text
MockConditionGateway
→ ReplayConditionGateway
→ KiwoomRestConditionGateway
```

### Phase 5. StrategyLab

목표:

```text
- Entry/Exit 수식 검증
- 차트 삽입
- 선택 구간 PnL
- A/B Compare
- Draft → Candidate → Promoted → Main 승급
```

`rest_base`의 전략 생명주기와 실험 저장 철학을 VB.NET으로 이식한다.

### Phase 6. IntegrationLab

목표:

```text
조건식 편입
→ 캔들 로드
→ 지표 계산
→ 스코어링
→ Top N 차트 표시
→ 전략 신호 표시
→ 주문 전 RiskGate
```

주문은 이 단계에서도 마지막에 붙인다.

---

## 6. 새 프로젝트 최소 구조

```text
KiwoomRestTradingLab
│
├─ Core
│   ├─ Models
│   ├─ Events
│   └─ Interfaces
│
├─ ChartLab
│   ├─ ChartControlLite.vb
│   ├─ ChartRenderModel.vb
│   ├─ ChartOverlay.vb
│   └─ ChartViewportState.vb
│
├─ IndicatorLab
│   ├─ IIndicator.vb
│   ├─ BollingerBands.vb
│   ├─ Rsi.vb
│   ├─ SuperTrend.vb
│   └─ TickIntensityCore.vb
│
├─ FormulaLab
│   ├─ FormulaParser.vb
│   ├─ FormulaSpec.vb
│   └─ FormulaEvaluator.vb
│
├─ ConditionLab
│   ├─ IConditionGateway.vb
│   ├─ MockConditionGateway.vb
│   ├─ ReplayConditionGateway.vb
│   └─ KiwoomRestConditionGateway.vb
│
├─ StrategyLab
│   ├─ StrategySpec.vb
│   ├─ StrategyEvaluatorLite.vb
│   ├─ StrategyPromotionStore.vb
│   └─ StrategyCompareResult.vb
│
├─ App
│   ├─ frmLabMain.vb
│   └─ frmChartLab.vb
│
└─ data
    ├─ strategies.json
    ├─ experiments.json
    └─ promotion_log.json
```

---

## 7. 진행 순서

바로 다음 작업 순서는 다음과 같다.

```text
1. docs/extract_inventory.md 기준으로 기존 파일 등급화
2. FastChartControl.vb에서 차트 렌더링 고정영역만 적출
3. ChartControlLite 단일 차트 실험 성공
4. TickIntensity_Indicator.vb에서 계산 코어만 적출
5. IndicatorLab 단위 결과 검증
6. StrategyEngine.vb에서 ConditionCell/CrossUp/CrossDown 구조 적출
7. FormulaLab 최소 문법 지원
8. MockConditionGateway로 조건식 편입 replay
9. 차트 + 지표 + 수식 + 마커 통합
10. Promoted 로직만 Main 후보로 등록
```

---

## 8. 판단 기준

이번 리팩터링의 성공 기준은 코드량이 아니다.

성공 기준은 다음이다.

```text
- 기존 성공 로직의 핵심 계산/렌더링 결과가 보존되는가
- 의존성이 줄었는가
- UI 없이 단위 테스트 가능한가
- API 없이 replay 가능한가
- 실시간 루프에서 불필요한 파싱/렌더링/DB호출이 제거되었는가
- 성공한 단위만 Main에 편입되는가
```

이 기준을 통과하지 못한 코드는 메인으로 편입하지 않는다.
