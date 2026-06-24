# Extract Inventory

작성일: 2026-06-24
대상: `hoonoh57/infra`, `hoonoh57/rest_base`
목적: 기존 성공 로직을 파일 단위가 아니라 기능 단위로 적출하기 위한 인벤토리

---

## 1. 등급 기준

```text
A: 바로 적출 가치 있음. 단위 실험실로 우선 이식.
B: 일부 함수/아이디어만 적출. 의존성 제거 후 이식.
C: 참조 후 재작성. 구조 또는 개념만 사용.
D: 폐기. 새 프로젝트로 가져오지 않음.
```

---

## 2. 적출 판단 항목

각 파일은 다음 항목으로 평가한다.

```text
- 파일 경로
- 현재 역할
- 성공 로직 후보
- 고정영역 후보
- 가변영역 후보
- 제거해야 할 의존성
- 단위 실험 가능 여부
- 이식 난이도
- 편입 우선순위
- 판정 등급
```

---

## 3. 1차 인벤토리

| 등급 | 저장소 | 파일 | 현재 역할 | 살릴 후보 | 제거/분리 대상 | 우선순위 |
|---|---|---|---|---|---|---|
| A | infra | `MainApp/ChartEngine/Core/FastChartControl.vb` | SkiaSharp 기반 실시간 차트 컨트롤 | 렌더링 루프, 캔들/거래량 패널, crosshair, marker, Y축 autoscale, paint 재사용 | IndicatorEngine 직접 보유, StrategyEngine 직접 보유, MessageBus 과다 구독, API/보조데이터 요청 상태 | 1 |
| A | infra | `MainApp/ChartEngine/Indicators/TickIntensity_Indicator.vb` | TickIntensity 지표 | bar align, realtime tick bucket, completed/current bar 관리, MA5/MA20 | IIndicator adapter, chart result 변환, RuntimeChartSettings 직접 참조 | 2 |
| A | infra | `MainApp/Services/StrategyEngine.vb` | 통합 전략 관리/평가 | ConditionCell 평가, LogicGate, CrossUp/CrossDown, AI/Hardcoded 전략 등록 구조 | UI/Logger 의존, 전체 Historical 평가와 실시간 평가 혼재 | 3 |
| B | infra | `MainApp/Services/StrategyEvaluator.vb` | 전략 수식 평가/마커 생성 후보 | Historical 평가, marker 생성, PnL 평가 후보 | 기존 모델 강결합, 과도한 범용성 | 4 |
| B | infra | `MainApp/Services/ZeroLossChartStrategy.vb` | 특정 성공 전략 후보 | 청산/보호 로직 아이디어 | 특정 조건/종목/상황 의존 가능성 | 5 |
| B | infra | `MainApp/Services/ChartProfileService.vb` | 차트 프로필 저장/로드 | indicator/profile 설정 저장 구조 | 현재 UI/모델 종속 | 6 |
| B | infra | `MainApp/Models/ChartProfileModels.vb` | 차트 프로필 모델 | ChartPanel/Indicator 설정 모델 | 이름/필드 재정리 필요 | 7 |
| B | infra | `StrategyLabApp/StrategyLabForm.vb` | 전략 실험 UI | 전략 선택, 실험 흐름, 사용자 검증 UX | Form 내부 비즈니스 로직 | 8 |
| C | infra | `MainApp/Forms/ChartForm.vb` | 기존 차트 화면 | 사용 흐름 참고 | 폼 내부 결합, 직접 호출 구조 | 9 |
| C | infra | `MainApp/SimTrade/SimTradeEngine.vb` | 시뮬레이션 엔진 | replay/시뮬레이션 개념 | 기존 모델 종속 가능성 | 10 |
| A | rest_base | `README.md` | WISI Chart Simulation Lab 설계 문서 | 전략 생명주기, Clone/Validate/Insert/PnL/A-B/Promote 절차 | Python 구현 자체는 직접 이식하지 않음 | 1 |
| B | rest_base | `chart_simulation.py` | 웹 기반 차트/전략 실험실 | 전략 관리자 UX, 실험 흐름, batch search 개념 | Python 웹서버 구조 | 6 |
| B | rest_base | `core.py` | REST/WS, 지표, 전략/백테스트 핵심 후보 | REST/WS 연동 개념, 계산 검증 기준 | Python 구현 직접 의존 금지 | 5 |
| B | rest_base | `strategies.json` | 전략 저장소 | StrategySpec 저장 형식 참고 | VB.NET용 스키마 재설계 필요 | 4 |
| B | rest_base | `experiments.json` | 실험 결과 저장소 | 실험 로그 형식 참고 | VB.NET용 스키마 재설계 필요 | 4 |

---

## 4. FastChartControl 적출 계획

### 4.1 고정영역으로 남길 부분

```text
- SKControl 초기화
- 16ms frame timer / repaint throttle
- Paint 객체 재사용
- candle body / wick 렌더링
- volume 렌더링
- grid / axis / crosshair
- current price line
- buy/sell marker
- viewport drag / wheel
- auto scale y
```

### 4.2 가변/외부영역으로 빼야 할 부분

```text
- IndicatorEngine 직접 생성
- StrategyEngine 직접 생성
- MessageBus 직접 구독
- CANDLE_LOADED, TICK, PROGRAM_TRADE, SECTOR_STOCKS_RESULT 처리
- 실시간 보조데이터 요청 상태
- 차트 타입별 API 요청 로직
- 전략 삽입/평가 로직
```

### 4.3 목표 산출물

```text
ChartControlLite.vb
ChartRenderModel.vb
ChartOverlay.vb
ChartViewportState.vb
SignalMarker.vb
IndicatorSeries.vb
```

### 4.4 성공 기준

```text
- List(Of Candle)만 주입해 차트 표시
- List(Of IndicatorSeries)만 주입해 보조지표 표시
- List(Of SignalMarker)만 주입해 매수/매도 마커 표시
- API/DB/StrategyEngine 없이 실행 가능
```

---

## 5. TickIntensity 적출 계획

### 5.1 살릴 부분

```text
- timeframe minute align
- realtime tick bucket
- completed realtime bar dictionary
- current realtime bar 관리
- candle direction 기반 signed tick sum
- MA5 / MA20 계산
- UpdateLast 개념
```

### 5.2 분리할 부분

```text
- IIndicator 구현부
- IndicatorResult 생성부
- RuntimeChartSettings 직접 참조
- Chart panel index 고정값
```

### 5.3 목표 산출물

```text
TickIntensityCore.vb
TickBucketBuffer.vb
TickIntensityParams.vb
TickIntensityResult.vb
TickIntensityIndicatorAdapter.vb
```

### 5.4 성공 기준

```text
- 동일 candles/ticks 입력 시 FullCalculate와 UpdateLast 결과 일관
- tick 없는 경우 fallback 정책 명확
- MA5/MA20 NaN 처리 일관
- UI 없이 단위 테스트 가능
```

---

## 6. StrategyEngine 적출 계획

### 6.1 살릴 부분

```text
- StrategyDefinition 등록 구조
- IStrategy hardcoded 전략 등록 구조
- ConditionCell 평가
- LogicGate AND/OR/XOR
- ComparisonOperator Greater/Less/Equal/CrossUp/CrossDown
- 이전 봉 비교 기반 crossover/crossunder
```

### 6.2 분리할 부분

```text
- AppLogger 의존
- MainApp.Models 직접 의존
- Historical 전체 평가와 실시간 평가 혼재
- Marker 생성 책임
- PnL 계산 책임
```

### 6.3 목표 산출물

```text
FormulaSpec.vb
ConditionCellSpec.vb
LogicGateSpec.vb
FormulaEvaluator.vb
StrategyEvaluatorLite.vb
StrategyRuntimeState.vb
```

### 6.4 성공 기준

```text
- CrossUp/CrossDown 단위 테스트 통과
- AND/OR gate 단위 테스트 통과
- 키움 수식관리자 변환 결과를 FormulaSpec으로 평가 가능
- 실시간 루프에서는 문자열 파싱 없이 Evaluate만 수행
```

---

## 7. rest_base 이식 항목

`rest_base`에서 직접 이식할 것은 Python 코드 전체가 아니다.

이식 대상은 철학과 저장 흐름이다.

```text
- draft / candidate / promoted / archived 전략 생명주기
- 원본 보존 + Clone New Version
- Validate
- Insert To Chart
- Precise PnL
- A/B Compare
- Promote
- experiments.json 실험 누적
- strategies.json 전략 저장
- Universe Builder 개념
- Batch Search / Lab Worker 개념
```

초기 VB.NET에서는 UI보다 스키마와 저장 규칙을 먼저 만든다.

---

## 8. 다음 실제 작업 체크리스트

```text
[ ] infra/FastChartControl.vb 전체 의존성 맵 작성
[ ] FastChartControl에서 API/전략/지표 직접 의존 라인 표시
[ ] ChartControlLite 최소 버전 설계
[ ] Candle/Signal/IndicatorSeries 최소 모델 정의
[ ] TickIntensity_Indicator.vb 계산부 함수 단위 추출 후보 표시
[ ] StrategyEngine.vb CrossUp/CrossDown 단위 테스트 케이스 작성
[ ] rest_base strategies.json 스키마 확인
[ ] VB.NET용 StrategySpec v0.1 작성
[ ] PromotionRules 기준으로 첫 Candidate 선정
```

---

## 9. 편입 보류 항목

아래는 초기 편입하지 않는다.

```text
- 주문 전송
- 잔고/체결 실시간 반영
- 다중 계좌
- 실전 자동주문
- 복잡한 ML/딥러닝
- 대량 Batch Worker
- 전체 자동승급
```

초기에는 차트, 지표, 수식, 조건편입 replay, A/B 비교만 성공시키는 것이 우선이다.
