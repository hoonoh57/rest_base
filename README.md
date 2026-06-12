# WISI Chart Simulation Lab

`chart_simulation.py` 기반의 전략 검증/버전관리/백테스트/배치탐색 실험 환경입니다.  
목표는 다음 2축을 함께 운영하는 것입니다.

- 사람 축: 차트를 보면서 전략을 검증하고, 플레이 기능으로 진입 타이밍을 확인하고, 승급 여부를 판단
- 컴퓨터 축: 전략 후보를 대량 생성/테스트하고, 성과가 좋은 후보를 계속 축적

## 1. 구성 개요

- `chart_simulation.py`
  - 메인 웹 서버
  - 포트 `5000`
  - 차트 시뮬레이터, 전략 관리자, 백테스트, 배치 탐색, 백그라운드 워커 포함
- `core.py`
  - 키움 REST/WS 연동
  - 지표 계산
  - 전략 엔진/백테스트 엔진
- `settings.py`
  - 기본 파라미터
  - 전략 저장소
  - 실험 저장소
- `strategies.json`
  - 저장된 전략 목록
- `experiments.json`
  - 배치 탐색 및 워커 실행 결과 누적 저장

## 2. 핵심 개념

### 2.1 전략 생명주기

전략은 다음 상태로 관리합니다.

- `draft`
  - 초안
  - 아직 검증 전이거나 저장만 된 상태
- `candidate`
  - 검증 후보
  - 원본 전략을 개선한 버전
  - 배치 탐색/워커가 자동 생성하는 후보도 여기에 저장 가능
- `promoted`
  - 승급 전략
  - 비교 검증을 통과해 실전 검토 대상으로 올린 전략
- `archived`
  - 보존만 하는 전략

### 2.2 원본 보존 + 개선 버전 비교

운영 원칙은 항상 같습니다.

1. 원본 전략은 보존
2. 원본을 복제해 새 버전 생성
3. 개선 버전을 동일 구간에서 비교
4. 성과가 좋고 논리가 납득되면 승급

### 2.3 사람 축과 컴퓨터 축

- 사람 축
  - 차트 삽입
  - 플레이 기능으로 리페인팅/타이밍 검증
  - Precise PnL
  - A/B Compare
- 컴퓨터 축
  - 지표 조합 후보 자동 생성
  - 최근 구간 다중 백테스트
  - 실험 결과 저장
  - 후보 전략 자동 축적

## 3. 사전 준비

### 3.1 Python

Python 3.10+ 권장

필요 패키지 예시:

```powershell
pip install numpy requests websocket-client python-dotenv
```

### 3.2 `.env`

실행 전 `.env`에 키움 API 설정이 필요합니다.

필수 항목:

```env
KIWOOM_MOCK=true
KIWOOM_MOCK_APP_KEY=...
KIWOOM_MOCK_SECRET_KEY=...

# 또는 실전
KIWOOM_MOCK=false
KIWOOM_REAL_APP_KEY=...
KIWOOM_REAL_SECRET_KEY=...

KIWOOM_EXCHANGE=KRX
KIWOOM_ADJUST_PRICE=1
DEFAULT_SYMBOL=000660
```

공통 키를 쓰는 경우:

```env
KIWOOM_APP_KEY=...
KIWOOM_SECRET_KEY=...
```

주의:

- 실제 키/시크릿은 저장소에 커밋하지 않는 것이 맞습니다.
- `chart_simulation.py`는 포트 `5000`을 사용합니다.
- `app.py`의 `PORT` 값과 별개입니다.

## 4. 실행 방법

프로젝트 루트에서 실행합니다.

```powershell
python chart_simulation.py
```

성공하면 브라우저에서 아래 주소가 열립니다.

```text
http://localhost:5000
```

만약 브라우저가 자동으로 열리지 않으면 직접 접속하면 됩니다.

### 4.1 현재 환경에서 자주 발생하는 문제

만약 아래와 같은 메시지가 나오면:

```text
No Python at '...python.exe'
```

의미:

- 현재 시스템의 Python 경로 또는 가상환경 참조가 깨져 있는 상태입니다.

조치:

1. 시스템 Python 재설치 또는 경로 복구
2. 새 가상환경 생성
3. 필요한 패키지 재설치

예시:

```powershell
py -3.11 -m venv venv
venv\Scripts\activate
pip install numpy requests websocket-client python-dotenv
python chart_simulation.py
```

## 5. 기본 사용 순서

### 5.1 차트 데이터 로드

왼쪽 패널에서:

- 종목코드
- 타임프레임
- 시작일자
- 시작시간

을 입력한 후 `차트 데이터 다운로드`를 누릅니다.

### 5.2 구간 선택

차트 상단에서:

- `구간선택`
- `구간해제`
- `처음`
- `이전`
- `플레이`
- `다음`
- `마지막`

을 사용해 검증 구간을 정합니다.

### 5.3 전략 관리자 열기

오른쪽 상단 `Open Manager` 클릭

전략 관리자는 다음 역할을 합니다.

- 전략 생성
- 전략 저장
- 복제 후 버전업
- 수식 검증
- 차트 삽입
- 정밀 손익 평가
- 전략 비교
- 배치 탐색
- 백그라운드 워커 제어

## 6. 전략 관리자 기능 설명

## 6.1 Strategy Definition

입력 항목:

- `Saved`
  - 저장된 전략 선택
- `Name`
  - 전략 이름
- `Version`
  - 전략 버전 예: `v0.2.0`
- `Stage`
  - `draft`, `candidate`, `promoted`, `archived`
- `Code`
  - 기본 종목 코드
- `Benchmark`
  - 비교 기준 전략 ID
- `Entry`
  - 진입 수식
- `Exit`
  - 청산 수식
- `Qty`
  - 주문 수량
- `Stop %`
  - 손절 %
- `Take %`
  - 익절 %
- `Notes`
  - 메모

### 6.2 Parameter Overrides

전략별 지표 파라미터를 덮어쓸 수 있습니다.

예:

- MA 기간
- OBV Signal 기간
- MACD fast/slow/signal
- Supertrend period/multiplier
- JMA length/phase/power
- VWMA length
- fee/slippage

즉, 같은 전략식이라도 지표 파라미터를 다르게 해 별도 버전으로 실험할 수 있습니다.

### 6.3 Validation

`Validate` 버튼으로 수식 오류를 먼저 확인합니다.

검증 대상:

- Python 표현식 문법
- 허용 변수/함수 사용 여부

주요 사용 가능 변수 예:

- `close`
- `ma5`, `ma20`, `ma60`
- `obv`, `obv_signal`
- `macd`, `macd_signal`, `macd_hist`
- `supertrend`, `supertrend_trend`
- `jma`
- `vwma`
- `zigzag_trend`
- `zigzag_turn_up`
- `zigzag_turn_down`

이전 값 비교용 변수 예:

- `prev_close`
- `prev_macd`
- `prev_macd_signal`
- `prev_supertrend_trend`

사용 가능 함수 예:

- `crossover(a, b)`
- `crossunder(a, b)`
- `min(...)`
- `max(...)`
- `abs(...)`

### 6.4 Save / Clone / Promote

- `Save`
  - 현재 전략 저장
- `Clone New Ver`
  - 현재 전략을 복제해서 새 버전 초안 생성
- `Promote`
  - 검증 통과 후 `promoted` 상태로 승급

권장 흐름:

1. 원본 선택
2. `Clone New Ver`
3. 전략식/파라미터 수정
4. `Validate`
5. `Insert To Chart`
6. `Precise PnL`
7. `A/B Compare`
8. 좋으면 `Promote`

### 6.5 Insert To Chart

현재 전략을 차트에 삽입합니다.

효과:

- 매매 마커 표시
- 현재 실시간 신호 상태 표시
- 선택 구간 기준 전략 결과 표시

### 6.6 Precise PnL

선택 구간 기준으로 전략 손익을 계산합니다.

주요 지표:

- 거래 수
- 승률
- 총 수익률
- 평균 수익률
- PF
- 거래별 수익률

### 6.7 A/B Compare

저장된 두 전략을 동일 구간에서 비교합니다.

비교 항목:

- 총 수익률
- 승률
- 거래 수
- 상대 차이

사용 목적:

- 원본 대비 개선 효과 확인
- 단기 성과 착시 제거
- 특정 구간 편향 여부 확인

## 7. WISI 전략 작성 원칙 예시

현재 운영 원칙 예시는 다음과 같습니다.

1. `09:00` 이후 가장 빠른 추세 전환 포착
2. ZigZag 기반 전환 감지
3. 리페인팅 한계를 실전 플레이로 검증
4. 확증 신호를 하나씩 추가

확증 신호 예:

- `OBV > OBV Signal`
- `MACD Oscillator 0선 돌파`
- `close > VWMA`
- `supertrend_trend > 0`
- `JMA 상승`

예시 수식:

```python
(zigzag_turn_up or zigzag_trend > 0) and close > supertrend
```

예시 청산:

```python
zigzag_turn_down or zigzag_trend < 0 or close < supertrend
```

## 8. Lab Snapshot

전략 관리자 내부의 `Lab Snapshot`은 전체 랩 상태를 요약합니다.

표시 내용:

- 저장 전략 수
- 실험 누적 수
- `draft/candidate/promoted` 개수
- 최근 상위 실험 결과

이 패널은 “현재 어떤 전략들이 쌓이고 있는가”를 보는 곳입니다.

## 9. Batch Search

`Batch Search`는 현재 전략을 기준으로 후보 조합을 한 번에 탐색합니다.

현재 기본적으로 다음 축을 조합합니다.

- ZigZag 게이트
- Supertrend
- MACD
- OBV
- VWMA
- JMA
- 손절/익절 값 일부 조합

결과:

- 상위 후보 요약 표시
- `experiments.json` 저장
- 필요 시 `candidate` 전략으로 승격 가능한 기반 확보

적합한 용도:

- 한 전략의 개선 후보를 빠르게 다수 확인
- 특정 구간에서 어떤 확증 신호가 유효한지 탐색

## 10. Lab Worker

`Lab Worker`는 백그라운드 탐색축입니다.

설정 항목:

- `Symbols`
  - 예: `000660,005930,085620`
- `Timeframes`
  - 예: `t360,t720,m15`
- `Windows`
  - 예: `120,240,480`
- `Bars`
  - 다운로드 바 수
- `Interval Sec`
  - 반복 실행 주기
- `Save Top N`
  - 각 작업에서 저장할 상위 후보 수
- `Auto Candidate`
  - 좋은 후보를 자동으로 `candidate` 전략으로 저장할지 여부

버튼:

- `Run Once`
  - 한 사이클만 실행
- `Start Loop`
  - 계속 반복 실행
- `Stop`
  - 워커 중지

동작 방식:

1. 심볼 다운로드
2. 타임프레임별 데이터 준비
3. 최근 `window` 구간별 배치 탐색
4. 상위 결과를 `experiments.json`에 저장
5. 옵션에 따라 `candidate` 전략으로 저장

## 11. 저장 파일 설명

### 11.1 `strategies.json`

저장 항목 예:

- `id`
- `parent_id`
- `parent_version`
- `name`
- `version`
- `stage`
- `benchmark_id`
- `entry_expr`
- `exit_expr`
- `qty`
- `stop_pct`
- `take_pct`
- `notes`
- `params`

### 11.2 `experiments.json`

누적 저장 항목 예:

- `type`
  - `batch_search`
  - `worker_scan`
- `code`
- `tf`
- `window_bars`
- `rank`
- `base_strategy`
- `strategy`
- `summary`
- `ts`

이 파일은 “어떤 후보가 언제 어떤 조건에서 좋았는지”를 쌓는 실험 로그입니다.

## 12. 권장 운영 절차

### 12.1 수동 검증 절차

1. 차트 데이터 로드
2. 검증 구간 선택
3. 원본 전략 선택
4. `Clone New Ver`
5. 확증 신호 1개만 추가
6. `Validate`
7. `Insert To Chart`
8. `플레이`로 실시간성 검증
9. `Precise PnL`
10. `A/B Compare`
11. 개선이면 저장 후 `candidate` 또는 `promoted`

### 12.2 자동 탐색 절차

1. 기준 전략 선택
2. `Batch Search` 또는 `Lab Worker`
3. 상위 후보 축적
4. 사람이 후보를 선택
5. 차트 삽입 후 실전형 검증
6. 승급 여부 판단

## 13. 주의 사항

- ZigZag 계열은 개념적으로 빠른 추세 전환 포착에는 좋지만, 실전 적용 여부는 반드시 플레이 기능으로 확인해야 합니다.
- 짧은 구간에서 좋아 보이는 전략이 긴 구간에서는 나빠질 수 있습니다.
- 항상 동일 구간 비교와 장구간 비교를 함께 해야 합니다.
- 승률만 볼 것이 아니라 `총수익`, `PF`, `거래 수`, `하락/횡보 방어력`을 같이 봐야 합니다.

## 14. 다음 확장 방향

현재 구조 위에서 다음을 붙이면 됩니다.

- 자동 승급 규칙
- 종목군 스케줄러
- 기간별 성과 랭킹
- 시장 국면 분류
- 테마/지수/시장 분위기 필터
- 실전 추천 종목 산출 로직

이 문서 기준으로 운영하면, 원본 전략을 훼손하지 않으면서 개선 버전을 누적하고, 사람이 검증하는 축과 컴퓨터가 발굴하는 축을 동시에 운용할 수 있습니다.

## 15. Universe Builder

`Universe Builder`는 오늘 시장에서 주도성이 강했던 종목군을 자동으로 추려서 이후 전략 검증의 입력으로 쓰는 기능입니다.

현재 기본 소스:

- 거래대금상위
- 등락률상위

입력 항목:

- `Limit Each`
- `Top N`
- `Trade Value`
- `Change Rate`
- `Analyze Daily`

버튼:

- `Build Universe`
- `Use In Worker`

동작:

1. 거래대금/등락률 상위 종목 수집
2. 종목 중복 제거
3. 일봉 기반 추가 분석 점수 반영
4. 리더 점수 기준 정렬
5. `universes.json` 저장
6. 상위 종목 코드를 `Lab Worker` 입력으로 전달 가능

저장 파일:

- `universes.json`

이 기능의 목적은 추천 종목을 바로 확정하는 것이 아니라, `오늘 강했던 종목군`을 구조적으로 모아 그 안에서 수익형 특징을 찾는 데 있습니다.

## 16. Recommendation Builder

`Recommendation Builder`는 최신 후보군과 저장된 전략을 합쳐서 `우선 검토 종목`을 계산하는 기능입니다.

전략 선택 우선순위:

- `promoted`
- `candidate`
- `draft`

입력 항목:

- `TFs`
- `Top N`
- `Universe Limit`
- `Bars`
- `Window 1`
- `Window 2`
- `Window 3`

동작:

1. 최신 후보군 스냅샷 로드
2. 우선순위 전략 선택
3. 각 종목을 여러 타임프레임/최근 구간 기준으로 평가
4. `leader_score`와 전략 성과 점수를 합쳐 `recommendation_score` 계산
5. 상위 결과 저장

저장 파일:

- `recommendations.json`
