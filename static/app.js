// 서버에서 /api/bootstrap 으로 주입되는 값 (window.onload 직전에 채워짐)
var activeParams = {};
var conditionRowSample = [];

var LWC = LightweightCharts;
var chart = null;
var cs = null, vs = null;
var maS = [];
var obvs = null, obvsigs = null;
var macds = null, macdsigs = null, macdhs = null;
var jmaS = null, supertrendS = null, vwmaS = null;
var zzConfirmedS = null, zzUnconfirmedS = null;
var fractUpS = null, fractDnS = null;
var lrCenterS = null, lrUpperS = null, lrLowerS = null;
var markerHandle = null;

var simCandles = [];
var simTotal = 0;
var simCurrentIdx = 60;
var simRangeStart = null;
var simRangeEnd = null;
var rangeSelectMode = false;
var rangeClickStage = 0;
var rangeMarkerHandle = null;
var isPlaying = false;
var playTimer = null;

var balance = 10000000;
var initialBalance = 10000000;
var positionQty = 0;
var avgEntryPrice = 0;
var tradesLog = [];
var savedStrategies = [];
var savedConditions = [];
var experimentRows = [];
var latestUniverse = null;
var latestRecommendations = null;
var latestConditionValidation = null;
var latestTopRiserStudy = null;
var currentStrategy = null;
var currentStrategyResult = null;
var pendingStrategyStatus = false;
var managerTab = "strategy";
var managerHelpTopic = "overview";
var managerHelpTopics = {
  overview: {
    title: "이 화면의 목적",
    body: "이 화면은 3개 축으로 분리됩니다.\n\n전략작성: 진입식, 청산식, 파라미터를 저장하고 버전별 성능을 비교합니다.\n후보발굴: 오늘 시장에서 강했던 종목군을 자동 수집하고, 현재 전략들이 어떤 종목에 잘 맞는지 우선순위를 만듭니다.\n자동탐색: 종목, 타임프레임, 구간을 반복 백테스트하여 성과가 좋은 후보 전략을 누적합니다.\n\n권장 사용 순서\n1. 후보발굴 탭에서 Build Universe\n2. 후보발굴 탭에서 Build Recommendations\n3. 추천 상위 종목을 차트에 넣어 플레이로 진입 타이밍 확인\n4. 전략작성 탭에서 Precise PnL과 A/B Compare로 버전 비교\n5. 자동탐색 탭에서 장시간 반복 실행 후 candidate만 승급 검토"
  },
  strategy_definition: {
    title: "Strategy Definition",
    body: "무엇을 하나요\n진입식, 청산식, 수량, 손절/익절, 파라미터를 하나의 버전으로 저장합니다. 원본은 남기고 Clone New Ver로 개선판을 만듭니다.\n\n해석 방법\nEntry와 Exit은 실제 백테스트 수식입니다. zigzag_trend, zigzag_turn_up, supertrend, obv, macd, vwma, jma 등 현재 계산된 값을 그대로 사용할 수 있습니다.\n\n활용 팁\n가급적 한 번에 많은 조건을 넣지 말고, 핵심 게이트 1개를 추가한 새 버전으로 저장한 뒤 비교하십시오. 예: v0.2.0 -> v0.2.1(+ zigzag gate)."
  },
  validation: {
    title: "Validation",
    body: "무엇을 검증하나요\n수식 문법 오류, 사용 가능한 변수명, 전략 구조를 먼저 확인합니다.\n\n통과 후 의미\n검증 통과는 '실행 가능한 수식'이라는 뜻입니다. 아직 수익 전략이라는 뜻은 아닙니다. 통과 후 Insert To Chart와 Precise PnL로 실제 성능을 확인해야 합니다.\n\n권장 절차\nValidate -> Save -> Insert To Chart -> 플레이로 신호 시점 확인 -> Precise PnL 확인"
  },
  universe: {
    title: "Universe Builder",
    body: "무엇을 분석했나요\n등락률 상위, 거래대금 상위에서 오늘 시장 주도 후보를 자동으로 모읍니다. 같은 종목이 여러 조건에 동시에 잡히면 더 높은 점수를 받습니다. 필요하면 일봉 기반 분석도 함께 붙습니다.\n\n화면 해석\nCandidates: 모인 전체 후보 수\nTV / CHG / Dual: 거래대금 상위, 등락률 상위, 두 조건 동시 충족 수\nScore: 주도주 후보 점수\nTags: tv_top, chg_top, dual_top 같은 출처 태그\n\n활용 방법\n이 표는 '오늘 무엇을 더 깊게 볼지'를 정하는 1차 필터입니다. 점수와 태그가 강한 종목을 추천 생성이나 워커 탐색 대상으로 넘기십시오."
  },
  recommendation: {
    title: "Recommendation Builder",
    body: "무엇을 분석했나요\nUniverse 후보 종목마다 여러 전략, 타임프레임, 윈도우 구간을 돌려 현재 데이터에서 가장 잘 맞는 조합을 찾습니다.\n\n화면 해석\nReco: 종합 추천 점수. 주도주 점수와 전략 성과 점수를 합친 값입니다.\nLeader: 종목 자체의 시장 주도 점수입니다.\nStrategy: 그 종목에서 가장 잘 맞았던 전략 버전입니다.\nWin: 선택된 조합의 승률입니다.\n전략수익률: 선택된 전략 조합의 누적 백테스트 수익률입니다.\n\n활용 방법\nReco 상위 종목부터 차트에 넣고, 실제 플레이로 진입 타이밍을 검증하십시오. 단순히 Win만 보지 말고 어떤 전략 버전이 반복적으로 상위에 뜨는지, 전략수익률이 실제로 플러스인지도 같이 보십시오."
  },
  condition_builder: {
    title: "Condition Search",
    body: "무엇을 하나요\n키움 조건검색처럼 종목을 뽑는 검색식을 저장하는 영역입니다. 아직 1차 구현이므로 우측 조건행 대신 Rows JSON으로 조건 줄을 입력합니다.\n\n핵심 원칙\n여기서는 매수/매도 판단을 하지 않습니다. 오직 특정 시점까지의 데이터로 후보 종목을 뽑는 조건만 정의합니다.\n\nRows JSON 예시\nA: box_range_pct\nB: base_candle\nC: zigzag_turn_up\nExpr: A and B and C"
  },
  llm_workspace: {
    title: "LLM Workspace",
    body: "Purpose\nA simplified workspace for users who should not edit raw JSON directly.\n\nFlow\n1. Describe the job in natural language.\n2. Generate a prompt for an external LLM.\n3. Ask the LLM to return JSON only.\n4. Paste the JSON back here and validate it.\n5. Apply or run it through the existing advanced engines."
  },
  llm_manual_flow: {
    title: "Manual LLM Flow",
    body: "Why manual\nThis avoids direct API cost and lets a human confirm each step.\n\nSequence\n1. Choose a task in the simple workspace.\n2. Use a preset or write your own request.\n3. Generate the external prompt.\n4. Paste it into an external LLM.\n5. Ask for JSON only.\n6. Paste the JSON back here.\n7. Validate, apply, and run."
  },
  condition_validation: {
    title: "Condition Validation",
    body: "무엇을 검증하나요\n지정한 날짜/시각 이전까지의 데이터만 사용해 조건검색을 실행하고, 선택된 종목들의 이후 성과를 측정합니다.\n\n현재 1차 지표\n최고수익률: 검색 시점 이후 당일 고가 기준 최대 상승폭\n전략수익률: 같은 구간에 선택 전략을 적용했을 때의 누적 수익률\n\n중요\n이 검증기는 현재 OHLCV 재현형 조건식 기준입니다. 과거 거래대금 상위/프로그램/섹터 시점 재현은 별도 스냅샷 축적 기능이 추가되어야 완전해집니다."
  },
  top_riser_study: {
    title: "Top Riser Study",
    body: "무엇을 하나요\n특정 일자의 급등 상위 종목들에서 공통 상승요인을 추출하고, 다른 날짜 후보군이 그 요인과 얼마나 비슷한지 점수화합니다.\n\n주의\n현재 1차는 일봉 종가 확정 기준 유사도 분석입니다. 즉 '같은 날 종가 기준 구조가 반복되는가'를 보는 연구용 분석기이며, 장 시작 전 예측기로 쓰려면 추가로 시점 고정 intraday 스냅샷이 필요합니다.\n\n해석\nSimilarity Score가 높고 실제 등락률도 높은 종목이 반복되면, 그 급등 구조가 재현 가능성이 있다는 뜻입니다."
  },
  evaluation: {
    title: "Evaluation Summary",
    body: "무엇을 보여주나요\n현재 전략을 현재 종목/구간/타임프레임에 적용했을 때의 거래 목록과 총 성능입니다.\n\n화면 해석\nTrades: 총 거래 수\nWin: 승률\nTotal: 전체 누적 수익률\nAvg: 1회 평균 수익률\nPF: Profit Factor, 총 이익 / 총 손실\n\n활용 방법\n승률만 보지 말고 Total, PF, 거래 수를 같이 보십시오. 승률이 높아도 거래 수가 너무 적거나 PF가 낮으면 실전성이 약합니다."
  },
  compare: {
    title: "Version Compare",
    body: "무엇을 하나요\n기준 전략과 개선 전략을 같은 데이터 구간에서 직접 비교합니다.\n\n활용 방법\n원본 전략을 baseline으로 두고, 조건 1개만 추가한 버전을 candidate로 두십시오. 개선 여부는 Total, Win, PF, Max 손실 방어 관점에서 함께 판단해야 합니다.\n\n권장 원칙\n원본은 보존하고, 개선판은 새 버전으로 저장합니다. 좋아진 버전만 candidate 또는 promoted로 올리십시오."
  },
  worker: {
    title: "Lab Worker",
    body: "무엇을 하나요\n선택된 종목, 타임프레임, 구간 조합을 반복 실행하여 좋은 결과를 자동 적재합니다. 수동 검증 전에 후보를 많이 모으는 엔진입니다.\n\n버튼 의미\nRun Once: 현재 설정으로 1회만 실행\nStart Loop: interval 초마다 반복 실행\nAuto Candidate: 성과가 좋은 결과를 candidate 전략으로 자동 저장\n\n활용 방법\nUniverse/Recommendation 결과를 Use In Worker로 넘긴 뒤 루프를 돌리면, 주도 종목군 중심으로 전략 적합도를 계속 누적할 수 있습니다."
  },
  snapshot: {
    title: "Lab Snapshot",
    body: "무엇을 보여주나요\n현재까지 누적된 전략 수, 실험 수, 그리고 상위 성과 실험 목록입니다.\n\n화면 해석\nStage: draft / candidate / promoted 상태\nScore: 내부 종합 점수\nTotal: 누적 수익률\nWin: 승률\n\n활용 방법\n반복적으로 상위에 남는 전략만 골라 Strategy 탭에서 상세 검증하십시오. snapshot은 자동탐색의 결과판이며, 최종 의사결정은 차트 삽입과 플레이 검증까지 끝난 뒤에 하십시오."
  }
};
var llmTask = "condition_search";
var llmWorkspaceDefs = {
  condition_search: {
    title: "Condition Search",
    target: "Condition Search form",
    subtle: "Describe a candidate-discovery idea and generate JSON for the condition builder.",
    guide: "Goal\nCreate a point-in-time candidate filter.\n\nUse it for\nBox breakout, base candle, zigzag turn-up, OBV or MACD confirmation ideas.\n\nResult\nThe payload is applied to Condition Search so you can refine rows in advanced mode if needed.",
    presets: [
      { label: "Daily Box Only", text: "Create a condition search JSON for a 20-day daily box breakout using daily timeframe only." },
      { label: "Daily + 5m", text: "Create a condition search JSON for a 20-day daily box breakout with a same-day 5-minute base candle and early upper break." },
      { label: "Zigzag Turn", text: "Create a condition search JSON for symbols that turn up on zigzag after 09:00 and stay above supertrend." },
      { label: "Trade Value Lead", text: "Create a condition search JSON for leader stocks with strong trade value, OBV support, and volume expansion." }
    ]
  },
  performance_validation: {
    title: "Performance Validation",
    target: "Condition Validation and strategy test",
    subtle: "Generate both the condition and the validation setup so the idea can be measured immediately.",
    guide: "Goal\nTest whether a condition really finds useful candidates.\n\nUse it for\nFixing a date and time, searching only with prior data, then measuring max run-up and strategy return.\n\nResult\nCondition Search and Condition Validation will both be filled and can run in one step.",
    presets: [
      { label: "Prev Day Test", text: "Create a validation JSON for a box breakout idea at 2026-06-11 09:10 and evaluate max run-up plus strategy return." },
      { label: "Zigzag Gate Test", text: "Create a validation JSON for a zigzag turn-up plus supertrend support idea at 2026-06-11 09:20." },
      { label: "Top 15 Candidates", text: "Create a validation JSON that selects the top 15 trade-value leader candidates and measures strategy return." }
    ]
  },
  top_riser_study: {
    title: "Top Riser Study",
    target: "Top Riser Study",
    subtle: "Generate a study that extracts common rally factors from one date and checks them against another date.",
    guide: "Goal\nStudy repeated rally structures.\n\nUse it for\nComparing source-date top risers against another date's candidate list.\n\nResult\nThe Top Riser Study panel will be filled and can run immediately.",
    presets: [
      { label: "Yesterday Top10", text: "Create a top riser study JSON using 2026-06-11 top 10 risers as source and 2026-06-10 as target." },
      { label: "20-Day Pattern", text: "Create a top riser study JSON that looks for repeated rally structures across recent strong dates." },
      { label: "20 Candidates", text: "Create a top riser study JSON that compares the source profile against 20 target candidates." }
    ]
  },
  stock_recommendation: {
    title: "Stock Recommendation",
    target: "Universe Builder and Recommendation Builder",
    subtle: "Generate a universe setup and a recommendation setup for tomorrow-watchlist ranking.",
    guide: "Goal\nNarrow the market first, then rank the best symbols with strategy fit.\n\nUse it for\nTomorrow watchlist building, leader-stock filtering, and strategy-fit ranking.\n\nResult\nUniverse Builder and Recommendation Builder will be filled and can run as one workflow.",
    presets: [
      { label: "Tomorrow Watchlist", text: "Create a recommendation JSON that builds a universe from top trade value and top change-rate stocks, then ranks the top 10 for tomorrow using t360 and t720." },
      { label: "Leader Focus", text: "Create a recommendation JSON focused on sector leaders with strong trade value and recent profitable strategy fit." },
      { label: "Conservative Top5", text: "Create a recommendation JSON that keeps a broad universe but recommends only the top 5 symbols with both good win rate and strategy return." }
    ]
  }
};
var strategyParamDefs = [];

function buildStrategyParamDefs() {
  strategyParamDefs = [
    { key: "ma_periods_0", label: "MA Fast", type: "int", value: (activeParams.ma_periods && activeParams.ma_periods[0]) || 5 },
    { key: "ma_periods_1", label: "MA Mid", type: "int", value: (activeParams.ma_periods && activeParams.ma_periods[1]) || 20 },
    { key: "ma_periods_2", label: "MA Slow", type: "int", value: (activeParams.ma_periods && activeParams.ma_periods[2]) || 60 },
    { key: "obv_signal_period", label: "OBV Signal", type: "int", value: activeParams.obv_signal_period },
    { key: "macd_fast", label: "MACD Fast", type: "int", value: activeParams.macd_fast },
    { key: "macd_slow", label: "MACD Slow", type: "int", value: activeParams.macd_slow },
    { key: "macd_signal", label: "MACD Signal", type: "int", value: activeParams.macd_signal },
    { key: "supertrend_period", label: "Supertrend Period", type: "int", value: activeParams.supertrend_period },
    { key: "supertrend_multiplier", label: "Supertrend Mult", type: "float", value: activeParams.supertrend_multiplier },
    { key: "jma_length", label: "JMA Length", type: "int", value: activeParams.jma_length },
    { key: "jma_phase", label: "JMA Phase", type: "float", value: activeParams.jma_phase },
    { key: "jma_power", label: "JMA Power", type: "int", value: activeParams.jma_power },
    { key: "vwma_length", label: "VWMA Length", type: "int", value: activeParams.vwma_length },
    { key: "fee_bp", label: "Fee bp", type: "float", value: activeParams.fee_bp },
    { key: "slippage_bp", label: "Slippage bp", type: "float", value: activeParams.slippage_bp }
  ];
}

function fmtStrategyTime(value) {
  if (typeof value === "number") {
    var d = new Date(value * 1000);
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mi = String(d.getUTCMinutes()).padStart(2, "0");
    return hh + ":" + mi;
  }
  return value || "-";
}

function setStrategySignalChip(signal, text) {
  var chip = document.getElementById("strategySignalChip");
  if (!chip) return;
  chip.className = "chip " + (signal || "neutral");
  chip.innerText = text || "Idle";
}

function renderManagerHelp(topic) {
  managerHelpTopic = topic || managerHelpTopic || "overview";
  var body = document.getElementById("strategyHelpBody");
  var info = managerHelpTopics[managerHelpTopic] || managerHelpTopics.overview;
  if (!body || !info) return;
  body.innerHTML = "<div style='font-size:14px;font-weight:700;color:#fff;margin-bottom:8px;'>" + info.title + "</div>"
    + "<div style='white-space:pre-line;line-height:1.65;'>" + info.body + "</div>";
}

function setLLMTask(task) {
  llmTask = llmWorkspaceDefs[task] ? task : "condition_search";
  Object.keys(llmWorkspaceDefs).forEach(function(name) {
    var btn = document.getElementById("llmTaskBtn_" + name);
    if (btn) btn.classList.toggle("active", name === llmTask);
  });
  var def = llmWorkspaceDefs[llmTask];
  if (!def) return;
  var title = document.getElementById("llmTaskTitle");
  var subtle = document.getElementById("llmTaskSubtle");
  var guide = document.getElementById("llmTaskGuide");
  var current = document.getElementById("llmTaskCurrent");
  var target = document.getElementById("llmTaskTarget");
  var result = document.getElementById("llmTaskResult");
  var menuHint = document.getElementById("llmMenuHint");
  var promptBox = document.getElementById("llmUserPrompt");
  if (title) title.innerText = def.title;
  if (subtle) subtle.innerText = def.subtle;
  if (guide) guide.innerText = def.guide;
  if (current) current.innerText = def.title;
  if (target) target.innerText = def.target;
  if (result) result.innerText = "JSON 검증 후 " + def.target + "에 반영됩니다.";
  if (menuHint) menuHint.innerText = "현재 작업: " + def.title + "\n프리셋으로 예시 문장을 넣은 뒤 그대로 다듬어 사용하면 됩니다.";
  if (promptBox) promptBox.placeholder = (def.presets && def.presets[0] ? def.presets[0].text : "");
  renderLLMPresetButtons();
}

function renderLLMPresetButtons() {
  var wrap = document.getElementById("llmPresetButtons");
  if (!wrap) return;
  var def = llmWorkspaceDefs[llmTask] || llmWorkspaceDefs.condition_search;
  wrap.innerHTML = "";
  (def.presets || []).forEach(function(preset, idx) {
    var btn = document.createElement("button");
    btn.className = "preset-btn";
    btn.type = "button";
    btn.innerText = preset.label || ("Preset " + (idx + 1));
    btn.onclick = function() { applyLLMPreset(idx); };
    wrap.appendChild(btn);
  });
}

function applyLLMPreset(idx) {
  var def = llmWorkspaceDefs[llmTask] || llmWorkspaceDefs.condition_search;
  var preset = (def.presets || [])[idx];
  if (!preset) return;
  var box = document.getElementById("llmUserPrompt");
  if (box) box.value = preset.text || "";
}

function clearLLMUserPrompt() {
  var box = document.getElementById("llmUserPrompt");
  if (box) box.value = "";
}

function buildLLMCapabilitySpec(task) {
  var lines = [];
  lines.push("Supported timeframes:");
  lines.push("- minute: m1, m3, m5, m10, m15, m30, m60");
  lines.push("- tick: t60, t120, t180, t360, t720");
  lines.push("- higher: d1, w1, mo1");
  lines.push("");
  lines.push("Supported condition indicators:");
  lines.push("- price_change_rate");
  lines.push("- trade_value");
  lines.push("- volume_ratio");
  lines.push("- price_above_ma");
  lines.push("- ma_cross_up");
  lines.push("- box_range_pct");
  lines.push("- breakout_high");
  lines.push("- base_candle");
  lines.push("- zigzag_trend");
  lines.push("- zigzag_turn_up");
  lines.push("- supertrend_state");
  lines.push("- vwma_position");
  lines.push("- jma_trend");
  lines.push("- obv_cross_up");
  lines.push("- macd_cross_up");
  lines.push("");
  lines.push("Supported operators:");
  lines.push("- is_true, >, >=, <, <=, ==, between");
  if (task === "performance_validation") {
    lines.push("");
    lines.push("Validation notes:");
    lines.push("- validation.timeframe is the point-in-time execution timeframe");
    lines.push("- condition.rows may use mixed timeframes if the user explicitly requests it");
  }
  if (task === "stock_recommendation") {
    lines.push("");
    lines.push("Recommendation notes:");
    lines.push("- universe narrows the market");
    lines.push("- recommendation.timeframes is a comma-separated timeframe list");
  }
  lines.push("");
  lines.push("Common omission risks to check before returning JSON:");
  lines.push("- If the user says box or 박스권, include a box definition such as box_range_pct, not only breakout_high.");
  lines.push("- If the user says base candle or 기준봉, include base_candle explicitly.");
  lines.push("- If the user says zigzag, include zigzag_turn_up or zigzag_trend.");
  lines.push("- If the user says supertrend, include supertrend_state or an explicit supertrend expression.");
  lines.push("- Preserve timeframe wording exactly. Do not add minute or tick timeframes that the user did not request.");
  lines.push("- If the user mentions clock time such as 09:00 after-open timing, reflect it in validation config or note the limitation.");
  return lines.join("\n");
}

function buildLLMExternalValidationChecklist(task) {
  var lines = [];
  lines.push("Internal validation gate before final JSON:");
  lines.push("1. Check that the user's key intent words are represented in JSON, not silently dropped.");
  lines.push("2. Check that every timeframe requested by the user is preserved exactly.");
  lines.push("3. Check that no extra timeframe was added unless the user explicitly requested it.");
  lines.push("4. Check that expression labels match the row labels exactly.");
  lines.push("5. Check that all indicators used are in the supported indicator list.");
  lines.push("6. Check that unsupported ideas were not invented as fake indicators or fake fields.");
  lines.push("7. If the user says box or 박스권, confirm the JSON contains a box-definition indicator such as box_range_pct and not only breakout_high.");
  lines.push("8. If the user says base candle or 기준봉, confirm base_candle is explicitly present.");
  lines.push("9. If the user says zigzag, confirm zigzag_turn_up or zigzag_trend is present.");
  lines.push("10. If the user says supertrend, confirm supertrend_state or an explicit supertrend expression is present.");
  lines.push("11. If the user mentions clock time such as 09:00 이후, ensure it is reflected in validation/config when the schema supports it.");
  lines.push("12. If a required part cannot be represented exactly, revise the JSON to the closest valid structure and preserve that limitation in description.");
  lines.push("13. Only after all checks pass, output one JSON object and nothing else.");
  if (task === "condition_search") {
    lines.push("14. For condition_search, confirm rows are enough to represent the described setup and not oversimplified to a single weak condition.");
  }
  if (task === "performance_validation") {
    lines.push("14. For performance_validation, confirm both condition and validation blocks are present and the validation timeframe/date/time are populated when mentioned by the user.");
  }
  if (task === "stock_recommendation") {
    lines.push("14. For stock_recommendation, confirm both universe and recommendation blocks are present.");
  }
  return lines.join("\n");
}

function extractRequestedTimeframesFromText(text) {
  var raw = String(text || "").toLowerCase();
  var tfs = [];
  function push(tf) {
    if (tf && tfs.indexOf(tf) < 0) tfs.push(tf);
  }
  if (raw.indexOf("일봉".toLowerCase()) >= 0 || raw.indexOf("daily") >= 0) push("d1");
  if (raw.indexOf("주봉".toLowerCase()) >= 0 || raw.indexOf("weekly") >= 0) push("w1");
  if (raw.indexOf("월봉".toLowerCase()) >= 0 || raw.indexOf("monthly") >= 0) push("mo1");
  raw.replace(/(\d+)\s*(?:분봉|분|min(?:ute)?s?)/gi, function(_, num) { push("m" + num); return _; });
  raw.replace(/(\d+)\s*(?:틱봉|틱|tick(?:s)?)/gi, function(_, num) { push("t" + num); return _; });
  return tfs;
}

function getPayloadTimeframes(payload) {
  var found = [];
  function push(tf) {
    if (tf && found.indexOf(tf) < 0) found.push(tf);
  }
  if (!payload) return found;
  if (payload.condition) {
    push(payload.condition.search_timeframe);
    (payload.condition.rows || []).forEach(function(row) { push(row.timeframe); });
  }
  if (payload.validation) push(payload.validation.timeframe);
  if (payload.recommendation && payload.recommendation.timeframes) {
    String(payload.recommendation.timeframes).split(",").map(function(item) { return item.trim(); }).filter(Boolean).forEach(push);
  }
  return found;
}

function renderLLMWarnings(lines) {
  var box = document.getElementById("llmJsonWarnings");
  if (!box) return;
  if (!lines || !lines.length) {
    box.innerText = "의미 검증 경고 없음";
    box.style.color = "#53dfd0";
    return;
  }
  box.innerText = lines.map(function(line) { return "- " + line; }).join("\n");
  box.style.color = "#ffb74d";
}

function collectLLMSemanticWarnings(payload) {
  var lines = [];
  var requestText = String((document.getElementById("llmUserPrompt") || {}).value || "");
  var requestLower = requestText.toLowerCase();
  var cond = payload.condition || null;
  var rows = cond ? (cond.rows || []) : [];
  var indicators = rows.map(function(row) { return String(row.indicator || ""); });
  var jsonTfs = getPayloadTimeframes(payload);
  var reqTfs = extractRequestedTimeframesFromText(requestText);
  var reqTfSet = reqTfs.slice();

  function hasAnyIndicator(names) {
    return names.some(function(name) { return indicators.indexOf(name) >= 0; });
  }
  function mentionsAny(words) {
    return words.some(function(word) { return requestLower.indexOf(word) >= 0; });
  }
  function hasTimeframe(tf) {
    return jsonTfs.indexOf(tf) >= 0;
  }

  if (payload.task === "condition_search" || payload.task === "performance_validation") {
    if (mentionsAny(["box", "박스권"]) && indicators.indexOf("box_range_pct") < 0) {
      lines.push("사용자 요청에 박스권/box가 있지만 rows에 box_range_pct가 없습니다. 현재 JSON은 박스권 정의 없이 단순 돌파일 수 있습니다.");
    }
    if (mentionsAny(["base candle", "기준봉"]) && indicators.indexOf("base_candle") < 0) {
      lines.push("사용자 요청에 기준봉/base candle이 있지만 rows에 base_candle이 없습니다.");
    }
    if (mentionsAny(["zigzag"]) && !hasAnyIndicator(["zigzag_turn_up", "zigzag_trend"])) {
      lines.push("사용자 요청에 zigzag가 있지만 rows에 zigzag_turn_up 또는 zigzag_trend가 없습니다.");
    }
    if (mentionsAny(["supertrend"]) && !(hasAnyIndicator(["supertrend_state"]) || String((cond || {}).expression || "").toLowerCase().indexOf("supertrend") >= 0)) {
      lines.push("사용자 요청에 supertrend가 있지만 JSON에 supertrend 관련 조건이 보이지 않습니다.");
    }
    if (mentionsAny(["09:00", "09:10", "09:20", "오전 09", "이후", "after 09"]) && payload.task === "condition_search") {
      lines.push("시간 제약이 자연어에 있으나 condition_search 스키마에는 시각 필드가 없습니다. 성과검증이나 별도 시간 row/notes 보완이 필요할 수 있습니다.");
    }
  }

  if ((requestLower.indexOf("daily only") >= 0 || requestLower.indexOf("일봉") >= 0 && requestLower.indexOf("only") >= 0 || requestLower.indexOf("만") >= 0) && jsonTfs.some(function(tf) { return tf && tf !== "d1"; })) {
    lines.push("사용자 요청은 일봉 only에 가깝지만 JSON에는 d1 외 timeframe이 포함되어 있습니다.");
  }

  if (reqTfSet.length === 1) {
    var only = reqTfSet[0];
    var unexpected = jsonTfs.filter(function(tf) { return tf && tf !== only; });
    if (unexpected.length) {
      lines.push("사용자 요청 timeframe은 " + only + " 하나로 보이는데 JSON에 추가 timeframe이 포함되어 있습니다: " + unexpected.join(", "));
    }
  }

  if ((payload.task === "condition_search" || payload.task === "performance_validation") && cond && rows.length === 1 && mentionsAny(["box", "박스권", "and", "그리고"])) {
    lines.push("자연어 요청 대비 row 수가 매우 적습니다. 핵심 조건 일부가 누락되었는지 확인이 필요합니다.");
  }
  return lines;
}

function buildLLMJsonSchema(task) {
  if (task === "performance_validation") {
    return '{\n'
      + '  "task": "performance_validation",\n'
      + '  "condition": {\n'
      + '    "name": "condition_name",\n'
      + '    "version": "v0.1.0",\n'
      + '    "stage": "draft",\n'
      + '    "search_timeframe": "<requested_main_tf>",\n'
      + '    "description": "description",\n'
      + '    "expression": "A and B and C",\n'
      + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "<requested_or_row_tf>", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\n'
      + '  },\n'
      + '  "validation": {\n'
      + '    "search_date": "YYYY-MM-DD",\n'
      + '    "search_time": "09:10",\n'
      + '    "timeframe": "<execution_tf>",\n'
      + '    "bars": 500,\n'
      + '    "top_n": 15,\n'
      + '    "symbols": "",\n'
      + '    "strategy_id": ""\n'
      + '  }\n'
      + '}';
  }
  if (task === "top_riser_study") {
    return '{\n'
      + '  "task": "top_riser_study",\n'
      + '  "config": {\n'
      + '    "source_date": "YYYY-MM-DD",\n'
      + '    "top_n": 10,\n'
      + '    "target_date": "YYYY-MM-DD",\n'
      + '    "candidate_limit": 10,\n'
      + '    "symbols": ""\n'
      + '  }\n'
      + '}';
  }
  if (task === "stock_recommendation") {
    return '{\n'
      + '  "task": "stock_recommendation",\n'
      + '  "universe": {\n'
      + '    "limit_each": 30,\n'
      + '    "top_n": 20,\n'
      + '    "include_trade_value": true,\n'
      + '    "include_change_rate": true,\n'
      + '    "analyze_daily": true\n'
      + '  },\n'
      + '  "recommendation": {\n'
      + '    "timeframes": "<requested_tf_list>",\n'
      + '    "top_n": 10,\n'
      + '    "universe_limit": 20,\n'
      + '    "bars": 1000,\n'
      + '    "window_1": 120,\n'
      + '    "window_2": 240,\n'
      + '    "window_3": 480\n'
      + '  }\n'
      + '}';
  }
  return '{\n'
    + '  "task": "condition_search",\n'
    + '  "condition": {\n'
    + '    "name": "condition_name",\n'
    + '    "version": "v0.1.0",\n'
    + '    "stage": "draft",\n'
    + '    "search_timeframe": "<requested_main_tf>",\n'
    + '    "description": "description",\n'
    + '    "expression": "A and B and C",\n'
    + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "<requested_or_row_tf>", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\n'
    + '  }\n'
    + '}';
}

function generateLLMPrompt() {
  var def = llmWorkspaceDefs[llmTask] || llmWorkspaceDefs.condition_search;
  var userText = (document.getElementById("llmUserPrompt").value || "").trim();
  if (!userText) {
    document.getElementById("llmJsonSummary").innerText = "작업 설명을 먼저 입력하거나 프리셋을 눌러주세요.";
    document.getElementById("llmJsonSummary").style.color = "#ffb74d";
    return;
  }
  var prompt = ""
    + "You are assisting a Korean trading lab user.\n"
    + "Convert the user's request into JSON only.\n"
    + "Do not explain. Do not wrap in markdown. Return a single valid JSON object only.\n"
    + "Do not translate, summarize, simplify, or reinterpret the user's request.\n"
    + "Treat the user's original text as the source of truth.\n"
    + "Use the capability list and schema only as constraints and formatting rules.\n"
    + "Current task: " + llmTask + " (" + def.title + ").\n\n"
    + "Capabilities:\n"
    + buildLLMCapabilitySpec(llmTask) + "\n\n"
    + "Validation checklist:\n"
    + buildLLMExternalValidationChecklist(llmTask) + "\n\n"
    + "Output schema:\n"
    + buildLLMJsonSchema(llmTask) + "\n\n"
    + "Rules:\n"
    + "- Use only fields from the schema.\n"
    + "- Keep expression labels consistent with rows labels.\n"
    + "- Preserve the user's timeframe request exactly.\n"
    + "- If the user requests mixed timeframes, preserve mixed timeframes in rows or config where needed.\n"
    + "- If the user mentions validation date or time, put it into validation/config.\n"
    + "- If the request exceeds supported capability, stay within the schema and choose the closest valid structure without inventing unsupported indicators.\n"
    + "- If the user asks for stock recommendation, return both universe and recommendation blocks.\n"
    + "- If unsure, keep symbols as an empty string.\n\n"
    + "Process requirement:\n"
    + "- Perform the internal validation checklist silently.\n"
    + "- Revise the JSON until the checklist passes.\n"
    + "- After it passes, output JSON only.\n\n"
    + "Original user request below. Preserve it semantically and do not rewrite it before converting to JSON.\n"
    + "BEGIN_USER_REQUEST\n"
    + userText + "\n"
    + "END_USER_REQUEST";
  document.getElementById("llmGeneratedPrompt").value = prompt;
  document.getElementById("llmJsonSummary").innerText = "프롬프트가 생성되었습니다. 외부 LLM에 붙여 넣고 JSON만 다시 가져오세요.";
  document.getElementById("llmJsonSummary").style.color = "#53dfd0";
}

function copyLLMGeneratedPrompt() {
  var text = document.getElementById("llmGeneratedPrompt").value || "";
  if (!text.trim()) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() {
      document.getElementById("llmJsonSummary").innerText = "프롬프트를 클립보드에 복사했습니다.";
      document.getElementById("llmJsonSummary").style.color = "#53dfd0";
    }).catch(function() {});
  }
}

function parseLLMJsonInput() {
  var raw = (document.getElementById("llmJsonInput").value || "").trim();
  if (!raw) throw new Error("JSON 입력이 비어 있습니다.");
  return JSON.parse(raw);
}

function normalizeLLMPayload(payload) {
  payload = payload || {};
  var task = String(payload.task || llmTask || "").trim() || "condition_search";
  if (task === "condition_validation") task = "performance_validation";
  if (task === "recommendation_builder") task = "stock_recommendation";
  if (task === "universe_builder") task = "stock_recommendation";
  if (task === "performance_validation") {
    if (!payload.condition || !payload.validation) throw new Error("condition 과 validation 이 모두 필요합니다.");
    if (!Array.isArray(payload.condition.rows) || !payload.condition.rows.length) throw new Error("condition.rows 가 비어 있습니다.");
  } else if (task === "condition_search") {
    var cond = payload.condition || payload;
    if (!Array.isArray(cond.rows) || !cond.rows.length) throw new Error("condition.rows 가 비어 있습니다.");
    payload.condition = cond;
  } else if (task === "top_riser_study") {
    if (!payload.config) throw new Error("config 가 필요합니다.");
    if (!payload.config.source_date || !payload.config.target_date) throw new Error("source_date 와 target_date 가 필요합니다.");
  } else if (task === "stock_recommendation") {
    if (!payload.recommendation) throw new Error("recommendation 블록이 필요합니다.");
    if (!payload.universe) payload.universe = readUniverseConfig();
  } else {
    throw new Error("지원하지 않는 task 입니다: " + task);
  }
  payload.task = task;
  return payload;
}

function renderLLMValidationSummary(payload) {
  var box = document.getElementById("llmJsonSummary");
  var result = document.getElementById("llmTaskResult");
  if (!box) return;
  if (payload.task === "condition_search") {
    var cond = payload.condition || payload;
    box.innerText = "조건검색 JSON 확인됨 | rows " + (cond.rows || []).length + " | expr " + (cond.expression || "-");
    if (result) result.innerText = "Condition Search 폼으로 반영할 준비가 되었습니다.";
  } else if (payload.task === "performance_validation") {
    box.innerText = "성과검증 JSON 확인됨 | rows " + ((payload.condition.rows || []).length) + " | date " + (payload.validation.search_date || "-") + " " + (payload.validation.search_time || "-");
    if (result) result.innerText = "Condition Validation 으로 바로 실행할 준비가 되었습니다.";
  } else if (payload.task === "top_riser_study") {
    box.innerText = "상승요인분석 JSON 확인됨 | source " + (payload.config.source_date || "-") + " | target " + (payload.config.target_date || "-");
    if (result) result.innerText = "Top Riser Study 로 바로 실행할 준비가 되었습니다.";
  } else if (payload.task === "stock_recommendation") {
    box.innerText = "종목추천 JSON 확인됨 | tfs " + (payload.recommendation.timeframes || "-") + " | topN " + (payload.recommendation.top_n || 0);
    if (result) result.innerText = "Universe + Recommendation Builder 로 바로 실행할 준비가 되었습니다.";
  }
  box.style.color = "#53dfd0";
  renderLLMWarnings(collectLLMSemanticWarnings(payload));
}

function validateLLMJson() {
  try {
    var payload = normalizeLLMPayload(parseLLMJsonInput());
    setLLMTask(payload.task);
    renderLLMValidationSummary(payload);
  } catch (err) {
    document.getElementById("llmJsonSummary").innerText = "JSON 검증 실패: " + err.message;
    document.getElementById("llmJsonSummary").style.color = "#ff8e8a";
    renderLLMWarnings(["JSON syntax or required-field validation failed. Semantic checks were not run."]);
  }
}

function applyConditionPayload(condition, validation) {
  var current = {};
  try {
    current = readConditionForm ? (readConditionForm() || {}) : {};
  } catch (err) {
    current = {};
  }
  var item = Object.assign({}, current, condition || {});
  item.rows = (condition && condition.rows) || current.rows || conditionRowSample;
  writeConditionForm(item);
  if (validation) {
    if (document.getElementById("condSearchDate")) document.getElementById("condSearchDate").value = validation.search_date || "";
    if (document.getElementById("condSearchTime")) document.getElementById("condSearchTime").value = validation.search_time || "09:10";
    if (document.getElementById("condSearchTF")) document.getElementById("condSearchTF").value = validation.timeframe || item.search_timeframe || "m5";
    if (document.getElementById("condBars")) document.getElementById("condBars").value = validation.bars || 500;
    if (document.getElementById("condTopN")) document.getElementById("condTopN").value = validation.top_n || 15;
    if (document.getElementById("condSymbols")) document.getElementById("condSymbols").value = validation.symbols || "";
    if (document.getElementById("condStrategyId") && validation.strategy_id) document.getElementById("condStrategyId").value = validation.strategy_id;
  }
}

function applyTopRiserPayload(config) {
  config = config || {};
  if (document.getElementById("trsSourceDate")) document.getElementById("trsSourceDate").value = config.source_date || "";
  if (document.getElementById("trsTopN")) document.getElementById("trsTopN").value = config.top_n || 10;
  if (document.getElementById("trsTargetDate")) document.getElementById("trsTargetDate").value = config.target_date || "";
  if (document.getElementById("trsCandidateLimit")) document.getElementById("trsCandidateLimit").value = config.candidate_limit || 10;
  if (document.getElementById("trsSymbols")) document.getElementById("trsSymbols").value = config.symbols || "";
}

function applyRecommendationPayload(universe, recommendation) {
  universe = universe || {};
  recommendation = recommendation || {};
  if (document.getElementById("universeLimitEach")) document.getElementById("universeLimitEach").value = universe.limit_each || 30;
  if (document.getElementById("universeTopN")) document.getElementById("universeTopN").value = universe.top_n || 20;
  if (document.getElementById("universeUseTV")) document.getElementById("universeUseTV").checked = universe.include_trade_value !== false;
  if (document.getElementById("universeUseCR")) document.getElementById("universeUseCR").checked = universe.include_change_rate !== false;
  if (document.getElementById("universeAnalyzeDaily")) document.getElementById("universeAnalyzeDaily").checked = universe.analyze_daily !== false;
  if (document.getElementById("recoTFs")) document.getElementById("recoTFs").value = recommendation.timeframes || "t360,t720";
  if (document.getElementById("recoTopN")) document.getElementById("recoTopN").value = recommendation.top_n || 10;
  if (document.getElementById("recoUniverseLimit")) document.getElementById("recoUniverseLimit").value = recommendation.universe_limit || 20;
  if (document.getElementById("recoBars")) document.getElementById("recoBars").value = recommendation.bars || 1000;
  if (document.getElementById("recoWindow1")) document.getElementById("recoWindow1").value = recommendation.window_1 || 120;
  if (document.getElementById("recoWindow2")) document.getElementById("recoWindow2").value = recommendation.window_2 || 240;
  if (document.getElementById("recoWindow3")) document.getElementById("recoWindow3").value = recommendation.window_3 || 480;
}

function openAdvancedFromLLMTask() {
  var topicMap = {
    condition_search: "condition_builder",
    performance_validation: "condition_validation",
    top_riser_study: "top_riser_study",
    stock_recommendation: "recommendation"
  };
  managerHelpTopic = topicMap[llmTask] || "overview";
  setManagerTab(llmTask === "stock_recommendation" ? "discovery" : "discovery", true);
}

function applyLLMJson(runNow) {
  var payload;
  try {
    payload = normalizeLLMPayload(parseLLMJsonInput());
    setLLMTask(payload.task);
  } catch (err) {
    document.getElementById("llmJsonSummary").innerText = "JSON 검증 실패: " + err.message;
    document.getElementById("llmJsonSummary").style.color = "#ff8e8a";
    renderLLMWarnings(["JSON syntax or required-field validation failed. Semantic checks were not run."]);
    return Promise.resolve(null);
  }
  renderLLMValidationSummary(payload);
  var action = Promise.resolve(payload);
  if (payload.task === "condition_search") {
    applyConditionPayload(payload.condition || payload, payload.validation || null);
    if (!runNow) action = Promise.resolve(payload);
    else action = validateConditionForm().then(function() {
      document.getElementById("llmJsonSummary").innerText = "Condition Search 폼 반영 및 검증 완료. 필요하면 후보발굴 탭에서 세부 조정하세요.";
      document.getElementById("llmJsonSummary").style.color = "#53dfd0";
      return payload;
    });
  }
  if (payload.task === "performance_validation") {
    applyConditionPayload(payload.condition, payload.validation);
    if (!runNow) action = Promise.resolve(payload);
    else action = validateConditionForm()
      .then(function() { return runConditionValidation(); })
      .then(function() {
        document.getElementById("llmJsonSummary").innerText = "성과검증 실행 완료. Condition Validation 결과를 확인하세요.";
        document.getElementById("llmJsonSummary").style.color = "#53dfd0";
        return payload;
      });
  }
  if (payload.task === "top_riser_study") {
    applyTopRiserPayload(payload.config);
    if (!runNow) action = Promise.resolve(payload);
    else action = runTopRiserStudy().then(function() {
      document.getElementById("llmJsonSummary").innerText = "상승요인분석 실행 완료. Top Riser Study 결과를 확인하세요.";
      document.getElementById("llmJsonSummary").style.color = "#53dfd0";
      return payload;
    });
  }
  if (payload.task === "stock_recommendation") {
    applyRecommendationPayload(payload.universe, payload.recommendation);
    if (!runNow) action = Promise.resolve(payload);
    else action = runUniverseBuilder()
      .then(function() { return runRecommendationBuilder(); })
      .then(function() {
        document.getElementById("llmJsonSummary").innerText = "종목추천 실행 완료. Universe 와 Recommendation 결과를 확인하세요.";
        document.getElementById("llmJsonSummary").style.color = "#53dfd0";
        return payload;
      });
  }
  return action.catch(function(err) {
    document.getElementById("llmJsonSummary").innerText = "실행 실패: " + err.message;
    document.getElementById("llmJsonSummary").style.color = "#ff8e8a";
    renderLLMWarnings(["Execution failed after JSON validation. Review the warning list and advanced form values."]);
    return null;
  });
}

function setManagerTab(tab, keepHelpTopic) {
  managerTab = tab || "strategy";
  ["strategy", "workspace", "discovery", "automation", "help"].forEach(function(name) {
    var btn = document.getElementById("tabBtn_" + name);
    if (btn) btn.classList.toggle("active", name === managerTab);
  });
  document.querySelectorAll("#strategyDlg [data-tab-group]").forEach(function(card) {
    var groups = (card.getAttribute("data-tab-group") || "").split(",");
    card.style.display = groups.indexOf(managerTab) >= 0 ? "" : "none";
  });
  var rightCol = document.getElementById("managerRightCol");
  if (rightCol) {
    if (managerTab === "help" || managerTab === "workspace") {
      rightCol.style.display = "none";
      rightCol.style.gridColumn = "";
    } else {
      rightCol.style.display = "flex";
      rightCol.style.gridColumn = managerTab === "strategy" ? "" : "1 / span 2";
    }
  }
  document.querySelectorAll("#strategyDlg [data-footer-group]").forEach(function(btn) {
    var group = btn.getAttribute("data-footer-group") || "strategy";
    btn.style.display = group === managerTab ? "" : "none";
  });
  var foot = document.getElementById("strategyDlgFoot");
  if (foot) foot.style.display = (managerTab === "help" || managerTab === "workspace") ? "none" : "flex";
  if (managerTab === "help") {
    renderManagerHelp(keepHelpTopic ? managerHelpTopic : "overview");
  } else {
    renderManagerHelp(managerHelpTopic);
  }
}

function showHelp(topic) {
  renderManagerHelp(topic || "overview");
  setManagerTab("help", true);
}

function buildStrategyParamFields(values) {
  var box = document.getElementById("strategyParamFields");
  if (!box) return;
  values = values || {};
  box.innerHTML = "";
  strategyParamDefs.forEach(function(def) {
    var val = values[def.key];
    if (val == null) val = def.value;
    var item = document.createElement("div");
    item.className = "param-item";
    item.innerHTML = "<label>" + def.label + "</label><input id='param_" + def.key + "' type='number' step='" + (def.type === "float" ? "any" : "1") + "' value='" + val + "'>";
    box.appendChild(item);
  });
}

function collectStrategyParams() {
  var params = {};
  params.ma_periods = [
    parseInt(document.getElementById("param_ma_periods_0").value, 10) || 5,
    parseInt(document.getElementById("param_ma_periods_1").value, 10) || 20,
    parseInt(document.getElementById("param_ma_periods_2").value, 10) || 60
  ];
  strategyParamDefs.forEach(function(def) {
    if (def.key.indexOf("ma_periods_") === 0) return;
    var el = document.getElementById("param_" + def.key);
    if (!el) return;
    params[def.key] = def.type === "float" ? parseFloat(el.value || "0") : parseInt(el.value || "0", 10);
  });
  return params;
}

function bumpVersion(version) {
  var match = String(version || "v0.1.0").match(/^v?(\d+)\.(\d+)\.(\d+)$/i);
  if (!match) return "v0.1.0";
  return "v" + match[1] + "." + match[2] + "." + (parseInt(match[3], 10) + 1);
}

function readStrategyForm() {
  return {
    id: document.getElementById("smId").value.trim(),
    parent_id: document.getElementById("smParentId").value.trim(),
    parent_version: document.getElementById("smParentVersion").value.trim(),
    name: document.getElementById("smName").value.trim(),
    version: document.getElementById("smVersion").value.trim(),
    stage: document.getElementById("smStage").value,
    benchmark_id: document.getElementById("smBenchmarkId").value.trim(),
    code: document.getElementById("smCode").value.trim() || document.getElementById("simCode").value.trim(),
    entry_expr: document.getElementById("smEntry").value.trim(),
    exit_expr: document.getElementById("smExit").value.trim(),
    qty: parseInt(document.getElementById("smQty").value || "0", 10) || 0,
    stop_pct: parseFloat(document.getElementById("smStop").value || "0") || 0,
    take_pct: parseFloat(document.getElementById("smTake").value || "0") || 0,
    notes: document.getElementById("smNotes").value.trim(),
    params: collectStrategyParams()
  };
}

function writeStrategyForm(strategy) {
  strategy = strategy || {};
  document.getElementById("smId").value = strategy.id || "";
  document.getElementById("smParentId").value = strategy.parent_id || "";
  document.getElementById("smParentVersion").value = strategy.parent_version || "";
  document.getElementById("smName").value = strategy.name || "WISI_Base";
  document.getElementById("smVersion").value = strategy.version || "v0.2.0";
  document.getElementById("smStage").value = strategy.stage || "draft";
  document.getElementById("smBenchmarkId").value = strategy.benchmark_id || "";
  document.getElementById("smCode").value = strategy.code || document.getElementById("simCode").value.trim() || "000660";
  document.getElementById("smEntry").value = strategy.entry_expr || "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend";
  document.getElementById("smExit").value = strategy.exit_expr || "zigzag_turn_down or zigzag_trend < 0 or close < supertrend";
  document.getElementById("smQty").value = strategy.qty || 100;
  document.getElementById("smStop").value = strategy.stop_pct || 0;
  document.getElementById("smTake").value = strategy.take_pct || 0;
  document.getElementById("smNotes").value = strategy.notes || "";
  if (strategy.code && document.getElementById("workerSymbols")) document.getElementById("workerSymbols").value = strategy.code;
  if (strategy.preferred_tf && document.getElementById("workerTFs")) document.getElementById("workerTFs").value = strategy.preferred_tf;
  var params = strategy.params || {};
  params.ma_periods_0 = (params.ma_periods && params.ma_periods[0]) || activeParams.ma_periods[0];
  params.ma_periods_1 = (params.ma_periods && params.ma_periods[1]) || activeParams.ma_periods[1];
  params.ma_periods_2 = (params.ma_periods && params.ma_periods[2]) || activeParams.ma_periods[2];
  buildStrategyParamFields(params);
}

function newStrategyForm() {
  writeStrategyForm(null);
  document.getElementById("strategyValidateResult").innerText = "No validation yet.";
  document.getElementById("strategyEvalSummary").innerText = "No evaluation yet.";
  document.getElementById("strategyEvalTrades").innerHTML = "";
  document.getElementById("strategyCompareSummary").innerText = "No comparison yet.";
  document.getElementById("strategyLabSummary").innerText = "No lab snapshot yet.";
  document.getElementById("strategyExperimentBody").innerHTML = "";
  if (document.getElementById("workerSymbols")) document.getElementById("workerSymbols").value = document.getElementById("simCode").value.trim() || "000660";
  if (document.getElementById("workerTFs")) document.getElementById("workerTFs").value = document.getElementById("simTF").value || "t360";
  if (document.getElementById("smList")) document.getElementById("smList").value = "new";
}

function cloneStrategyForm() {
  var payload = readStrategyForm();
  payload.parent_id = payload.id || payload.parent_id || "";
  payload.parent_version = payload.version || payload.parent_version || "";
  payload.id = "";
  payload.version = bumpVersion(payload.version);
  payload.stage = "candidate";
  payload.benchmark_id = payload.parent_id || payload.benchmark_id || "";
  if (payload.notes) {
    payload.notes = payload.notes + " | cloned from " + (payload.parent_version || "previous");
  } else {
    payload.notes = "cloned from " + (payload.parent_version || "previous");
  }
  writeStrategyForm(payload);
  if (document.getElementById("smList")) document.getElementById("smList").value = "new";
  document.getElementById("strategyValidateResult").innerText = "Cloned as new version draft.";
  document.getElementById("strategyValidateResult").style.color = "#53dfd0";
}

function initLLMWorkspace() {
  if (document.getElementById("llmTaskCurrent")) setLLMTask(llmTask || "condition_search");
}

function openStrategyManager(initialTab) {
  document.getElementById("strategyDlg").classList.add("show");
  loadStrategies().then(function() {
    loadConditions();
    initLLMWorkspace();
    setManagerTab(initialTab || "strategy");
    loadLatestUniverse();
    loadLatestRecommendations();
    loadLatestConditionValidation();
    loadLabSnapshot();
    loadWorkerStatus();
    if (!document.getElementById("smId").value) newStrategyForm();
    if (!document.getElementById("cvId").value) newConditionForm();
  });
}

function closeStrategyManager() {
  document.getElementById("strategyDlg").classList.remove("show");
}

function loadStrategies() {
  return fetch("/api/strategies")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      savedStrategies = d.items || [];
      var sel = document.getElementById("smList");
      var cmpBase = document.getElementById("cmpBase");
      var cmpCand = document.getElementById("cmpCand");
      var condStrategy = document.getElementById("condStrategyId");
      if (!sel) return;
      sel.innerHTML = "";
      if (cmpBase) cmpBase.innerHTML = "";
      if (cmpCand) cmpCand.innerHTML = "";
      if (condStrategy) condStrategy.innerHTML = "";
      var opt = document.createElement("option");
      opt.value = "new";
      opt.text = "-- new strategy --";
      sel.appendChild(opt);
      if (condStrategy) {
        var empty = document.createElement("option");
        empty.value = "";
        empty.text = "-- no strategy --";
        condStrategy.appendChild(empty);
      }
      savedStrategies.forEach(function(item) {
        var o = document.createElement("option");
        o.value = item.id;
        o.text = "[" + (item.stage || "draft") + "] " + (item.name || "Unnamed") + " " + (item.version || "");
        sel.appendChild(o);
        if (cmpBase) {
          var b = document.createElement("option");
          b.value = item.id;
          b.text = o.text;
          cmpBase.appendChild(b);
        }
        if (cmpCand) {
          var c = document.createElement("option");
          c.value = item.id;
          c.text = o.text;
          cmpCand.appendChild(c);
        }
        if (condStrategy) {
          var s = document.createElement("option");
          s.value = item.id;
          s.text = o.text;
          condStrategy.appendChild(s);
        }
      });
      if (cmpBase && savedStrategies.length > 0) cmpBase.value = savedStrategies[0].id;
      if (cmpCand && savedStrategies.length > 0) cmpCand.value = savedStrategies[Math.min(1, savedStrategies.length - 1)].id;
    });
}

function writeConditionForm(item) {
  item = item || {};
  document.getElementById("cvId").value = item.id || "";
  document.getElementById("cvParentId").value = item.parent_id || "";
  document.getElementById("cvParentVersion").value = item.parent_version || "";
  document.getElementById("cvName").value = item.name || "박스권_기준봉_상승전환";
  document.getElementById("cvVersion").value = item.version || "v0.1.0";
  document.getElementById("cvStage").value = item.stage || "draft";
  document.getElementById("cvTF").value = item.search_timeframe || "m5";
  document.getElementById("cvExpr").value = item.expression || "A and B and C";
  document.getElementById("cvDesc").value = item.description || "";
  document.getElementById("cvRows").value = JSON.stringify(item.rows || conditionRowSample, null, 2);
}

function newConditionForm() {
  writeConditionForm({
    stage: "draft",
    rows: conditionRowSample,
    expression: "A and B and C"
  });
  if (document.getElementById("condSearchDate") && !document.getElementById("condSearchDate").value && document.getElementById("simDate")) {
    document.getElementById("condSearchDate").value = document.getElementById("simDate").value;
  }
  if (document.getElementById("condSearchTime") && document.getElementById("simTime") && document.getElementById("simTime").value) {
    document.getElementById("condSearchTime").value = document.getElementById("simTime").value;
  }
  var box = document.getElementById("conditionValidateResult");
  if (box) {
    box.innerText = "No condition validation yet.";
    box.style.color = "#c7d0db";
  }
}

function cloneConditionForm() {
  var payload = readConditionForm();
  payload.parent_id = payload.id || payload.parent_id || "";
  payload.parent_version = payload.version || payload.parent_version || "";
  payload.id = "";
  payload.version = bumpVersion(payload.version);
  payload.stage = "candidate";
  writeConditionForm(payload);
  if (document.getElementById("cvList")) document.getElementById("cvList").value = "new";
  document.getElementById("conditionValidateResult").innerText = "Cloned as new version draft.";
  document.getElementById("conditionValidateResult").style.color = "#53dfd0";
}

function readConditionForm() {
  var rowsText = document.getElementById("cvRows").value.trim();
  var rows = [];
  if (rowsText) rows = JSON.parse(rowsText);
  return {
    id: document.getElementById("cvId").value.trim(),
    parent_id: document.getElementById("cvParentId").value.trim(),
    parent_version: document.getElementById("cvParentVersion").value.trim(),
    name: document.getElementById("cvName").value.trim(),
    version: document.getElementById("cvVersion").value.trim(),
    stage: document.getElementById("cvStage").value,
    description: document.getElementById("cvDesc").value.trim(),
    search_timeframe: document.getElementById("cvTF").value.trim(),
    expression: document.getElementById("cvExpr").value.trim(),
    rows: rows
  };
}

function loadConditions() {
  return fetch("/api/conditions")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      savedConditions = d.items || [];
      var sel = document.getElementById("cvList");
      if (!sel) return d;
      sel.innerHTML = "";
      var opt = document.createElement("option");
      opt.value = "new";
      opt.text = "-- new condition --";
      sel.appendChild(opt);
      savedConditions.forEach(function(item) {
        var o = document.createElement("option");
        o.value = item.id;
        o.text = "[" + (item.stage || "draft") + "] " + (item.name || "Unnamed") + " " + (item.version || "");
        sel.appendChild(o);
      });
      return d;
    });
}

function selectCondition(id) {
  if (!id || id === "new") {
    newConditionForm();
    return;
  }
  var found = savedConditions.find(function(item) { return String(item.id) === String(id); });
  if (found) writeConditionForm(found);
}

function validateConditionForm() {
  var payload;
  try {
    payload = readConditionForm();
  } catch (err) {
    document.getElementById("conditionValidateResult").innerText = "Rows JSON parse failed: " + err.message;
    document.getElementById("conditionValidateResult").style.color = "#ff8e8a";
    return Promise.resolve(null);
  }
  return fetch("/api/conditions/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    var box = document.getElementById("conditionValidateResult");
    if (d.valid) {
      box.innerText = "Condition validation passed.";
      box.style.color = "#53dfd0";
    } else {
      box.innerText = "Condition validation failed: " + (d.error || "unknown");
      box.style.color = "#ff8e8a";
    }
  });
}

function saveConditionForm() {
  var payload;
  try {
    payload = readConditionForm();
  } catch (err) {
    document.getElementById("conditionValidateResult").innerText = "Rows JSON parse failed: " + err.message;
    document.getElementById("conditionValidateResult").style.color = "#ff8e8a";
    return;
  }
  fetch("/api/conditions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    writeConditionForm(d);
    return loadConditions().then(function() {
      document.getElementById("cvList").value = d.id;
      document.getElementById("conditionValidateResult").innerText = "Saved: " + d.name + " " + (d.version || "");
      document.getElementById("conditionValidateResult").style.color = "#53dfd0";
    });
  })
  .catch(function(err) {
    document.getElementById("conditionValidateResult").innerText = "Save failed: " + err.message;
    document.getElementById("conditionValidateResult").style.color = "#ff8e8a";
  });
}

function deleteConditionForm() {
  var id = document.getElementById("cvId").value.trim();
  if (!id) return;
  fetch("/api/conditions/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id })
  })
  .then(function(r) { return r.json(); })
  .then(function() {
    newConditionForm();
    return loadConditions();
  });
}

function readConditionValidationConfig() {
  return {
    search_date: document.getElementById("condSearchDate").value,
    search_time: document.getElementById("condSearchTime").value || "09:10",
    timeframe: document.getElementById("condSearchTF").value.trim() || "m5",
    bars: parseInt(document.getElementById("condBars").value || "500", 10) || 500,
    top_n: parseInt(document.getElementById("condTopN").value || "15", 10) || 15,
    symbols: document.getElementById("condSymbols").value.trim(),
    strategy_id: document.getElementById("condStrategyId").value
  };
}

function renderConditionValidation(data) {
  latestConditionValidation = data || null;
  var box = document.getElementById("conditionValidationSummary");
  var body = document.getElementById("conditionValidationBody");
  if (!box || !body) return;
  if (!data || !(data.rows || []).length) {
    box.innerText = "No condition validation yet.";
    body.innerHTML = "";
    return;
  }
  var s = data.summary || {};
  box.innerText =
    "Built " + (data.built_at || "-")
    + " | candidates " + (s.candidate_count || 0)
    + " | avg 최고수익률 " + Number(s.avg_max_runup_pct || 0).toFixed(2) + "%"
    + " | avg 전략수익률 " + Number(s.avg_strategy_return_pct || 0).toFixed(2) + "%"
    + " | 전략 양수비율 " + Number(s.strategy_positive_rate || 0).toFixed(1) + "%";
  box.style.color = (s.avg_strategy_return_pct || 0) >= 0 ? "#53dfd0" : "#ff8e8a";
  body.innerHTML = "";
  (data.rows || []).forEach(function(row) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (row.code || "") + "</td>"
      + "<td>" + (row.name || "") + "</td>"
      + "<td>" + Number(row.score || 0).toFixed(2) + "</td>"
      + "<td class='" + ((row.max_runup_pct || 0) >= 0 ? "up" : "dn") + "'>" + Number(row.max_runup_pct || 0).toFixed(2) + "%</td>"
      + "<td class='" + ((row.strategy_return_pct || 0) >= 0 ? "up" : "dn") + "'>" + Number(row.strategy_return_pct || 0).toFixed(2) + "%</td>"
      + "<td>" + Number(row.strategy_win_rate || 0).toFixed(1) + "%</td>"
      + "<td>" + (row.strategy_trades || 0) + "</td>";
    body.appendChild(tr);
  });
}

function loadLatestConditionValidation() {
  return fetch("/api/condition_validations/latest")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      renderConditionValidation(d.snapshot || null);
      return d;
    })
    .catch(function() {});
}

function runConditionValidation() {
  var payload;
  try {
    payload = {
      condition: readConditionForm(),
      config: readConditionValidationConfig()
    };
  } catch (err) {
    document.getElementById("conditionValidationSummary").innerText = "Condition form parse failed: " + err.message;
    document.getElementById("conditionValidationSummary").style.color = "#ff8e8a";
    return Promise.resolve(null);
  }
  var box = document.getElementById("conditionValidationSummary");
  box.innerText = "Running point-in-time validation...";
  return fetch("/api/condition_validations/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    renderConditionValidation(d.snapshot || null);
  })
  .catch(function(err) {
    box.innerText = "Condition validation failed: " + err.message;
    box.style.color = "#ff8e8a";
  });
}

function applyConditionValidationToWorker() {
  if (!latestConditionValidation || !(latestConditionValidation.rows || []).length) return;
  var codes = latestConditionValidation.rows.map(function(row) { return row.code; }).filter(Boolean);
  if (document.getElementById("workerSymbols")) document.getElementById("workerSymbols").value = codes.join(",");
  var box = document.getElementById("workerStatusSummary");
  if (box) {
    box.innerText = "Condition validation codes applied to worker: " + codes.length;
    box.style.color = "#53dfd0";
  }
}

function readTopRiserStudyConfig() {
  return {
    source_date: document.getElementById("trsSourceDate").value,
    top_n: parseInt(document.getElementById("trsTopN").value || "10", 10) || 10,
    target_date: document.getElementById("trsTargetDate").value,
    candidate_limit: parseInt(document.getElementById("trsCandidateLimit").value || "10", 10) || 10,
    symbols: document.getElementById("trsSymbols").value.trim()
  };
}

function renderTopRiserStudy(data) {
  latestTopRiserStudy = data || null;
  var box = document.getElementById("topRiserStudySummary");
  var body = document.getElementById("topRiserStudyBody");
  if (!box || !body) return;
  if (!data || !(data.rows || []).length) {
    box.innerText = "No top riser study yet.";
    body.innerHTML = "";
    return;
  }
  var s = data.summary || {};
  var p = (data.profile || {}).summary || {};
  box.innerText =
    "Source " + (data.source_date || "-")
    + " avg " + Number(s.source_avg_chg_rate || 0).toFixed(2) + "%"
    + " | Target " + (data.target_date || "-")
    + " avg " + Number(s.selected_avg_chg_rate || 0).toFixed(2) + "%"
    + " | candidates " + (s.candidate_count || 0)
    + " | selected " + (s.selected_count || 0)
    + " | source OBV up " + Number(p.obv_up_rate || 0).toFixed(1) + "%"
    + " | source MACD " + (p.macd_majority || "-");
  box.style.color = (s.selected_avg_chg_rate || 0) >= (s.source_avg_chg_rate || 0) * 0.7 ? "#53dfd0" : "#ffb74d";
  body.innerHTML = "";
  (data.rows || []).forEach(function(row) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (row.code || "") + "</td>"
      + "<td>" + (row.name || "") + "</td>"
      + "<td>" + Number(row.similarity_score || 0).toFixed(2) + "</td>"
      + "<td class='" + ((row.chg_rate || 0) >= 0 ? "up" : "dn") + "'>" + Number(row.chg_rate || 0).toFixed(2) + "%</td>"
      + "<td>" + (row.obv_trend || "-") + "</td>"
      + "<td>" + (row.macd_array || "-") + "</td>"
      + "<td>" + Number(row.volume_ratio || 0).toFixed(2) + "</td>"
      + "<td>" + Number(row.box_range_pct || 0).toFixed(2) + "</td>";
    body.appendChild(tr);
  });
}

function runTopRiserStudy() {
  var cfg = readTopRiserStudyConfig();
  var box = document.getElementById("topRiserStudySummary");
  box.innerText = "Analyzing top riser factors...";
  return fetch("/api/top_riser_study/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config: cfg })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    renderTopRiserStudy(d.study || null);
  })
  .catch(function(err) {
    box.innerText = "Top riser study failed: " + err.message;
    box.style.color = "#ff8e8a";
  });
}

function applyTopRiserStudyToWorker() {
  if (!latestTopRiserStudy || !(latestTopRiserStudy.rows || []).length) return;
  var codes = latestTopRiserStudy.rows.map(function(row) { return row.code; }).filter(Boolean);
  if (document.getElementById("workerSymbols")) document.getElementById("workerSymbols").value = codes.join(",");
  var box = document.getElementById("workerStatusSummary");
  if (box) {
    box.innerText = "Top riser study codes applied to worker: " + codes.length;
    box.style.color = "#53dfd0";
  }
}

function renderLabSnapshot(data) {
  var box = document.getElementById("strategyLabSummary");
  var body = document.getElementById("strategyExperimentBody");
  if (!box || !body) return;
  if (!data) {
    box.innerText = "No lab snapshot yet.";
    body.innerHTML = "";
    return;
  }
  var sc = data.stage_counts || {};
  box.innerText = "Strategies " + (data.strategy_count || 0)
    + " | Experiments " + (data.experiment_count || 0)
    + " | draft " + (sc.draft || 0)
    + " | candidate " + (sc.candidate || 0)
    + " | promoted " + (sc.promoted || 0);
  body.innerHTML = "";
  (data.top_experiments || []).forEach(function(row) {
    var tr = document.createElement("tr");
    var s = row.summary || {};
    var st = row.strategy || {};
    tr.innerHTML = "<td>" + (st.stage || "-") + "</td>"
      + "<td>" + ((st.name || "Unnamed") + " " + (st.version || "")) + "</td>"
      + "<td>" + Number(s.score || 0).toFixed(2) + "</td>"
      + "<td class='" + ((s.total_return || 0) >= 0 ? "up" : "dn") + "'>" + Number(s.total_return || 0).toFixed(2) + "%</td>"
      + "<td>" + Number(s.win_rate || 0).toFixed(1) + "%</td>";
    body.appendChild(tr);
  });
}

function readUniverseConfig() {
  return {
    limit_each: parseInt(document.getElementById("universeLimitEach").value || "30", 10) || 30,
    top_n: parseInt(document.getElementById("universeTopN").value || "20", 10) || 20,
    include_trade_value: !!document.getElementById("universeUseTV").checked,
    include_change_rate: !!document.getElementById("universeUseCR").checked,
    analyze_daily: !!document.getElementById("universeAnalyzeDaily").checked
  };
}

function renderUniverseSnapshot(data) {
  latestUniverse = data || null;
  var box = document.getElementById("universeSummary");
  var body = document.getElementById("universeBody");
  if (!box || !body) return;
  if (!data || !(data.rows || []).length) {
    box.innerText = "No universe snapshot yet.";
    body.innerHTML = "";
    return;
  }
  var builtDate = String(data.built_at || "").slice(0, 10);
  if (builtDate) {
    if (document.getElementById("trsSourceDate") && !document.getElementById("trsSourceDate").value) document.getElementById("trsSourceDate").value = builtDate;
    if (document.getElementById("trsTargetDate") && !document.getElementById("trsTargetDate").value) document.getElementById("trsTargetDate").value = builtDate;
  }
  var s = data.summary || {};
  var tags = s.tag_counts || {};
  box.innerText =
    "Candidates " + (s.candidate_count || 0)
    + " | topN " + (s.top_n || 0)
    + " | tv " + (tags.tv_top || 0)
    + " | chg " + (tags.chg_top || 0)
    + " | dual " + (tags.dual_top || 0)
    + " | built " + (data.built_at || "-");
  body.innerHTML = "";
  (data.rows || []).forEach(function(row) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (row.code || "") + "</td>"
      + "<td>" + (row.name || "") + "</td>"
      + "<td>" + ((row.tags || []).join(",") || "-") + "</td>"
      + "<td>" + Number(row.leader_score || 0).toFixed(2) + "</td>"
      + "<td class='" + ((row.chg_rate || 0) >= 0 ? "up" : "dn") + "'>" + Number(row.chg_rate || 0).toFixed(2) + "%</td>";
    body.appendChild(tr);
  });
}

function readRecommendationConfig() {
  return {
    timeframes: document.getElementById("recoTFs").value.trim(),
    top_n: parseInt(document.getElementById("recoTopN").value || "10", 10) || 10,
    universe_limit: parseInt(document.getElementById("recoUniverseLimit").value || "20", 10) || 20,
    bars: parseInt(document.getElementById("recoBars").value || "1000", 10) || 1000,
    window_1: parseInt(document.getElementById("recoWindow1").value || "120", 10) || 120,
    window_2: parseInt(document.getElementById("recoWindow2").value || "240", 10) || 240,
    window_3: parseInt(document.getElementById("recoWindow3").value || "480", 10) || 480
  };
}

function renderRecommendationSnapshot(data) {
  latestRecommendations = data || null;
  var box = document.getElementById("recommendationSummary");
  var body = document.getElementById("recommendationBody");
  if (!box || !body) return;
  if (!data || !(data.rows || []).length) {
    box.innerText = "No recommendation snapshot yet.";
    body.innerHTML = "";
    return;
  }
  box.innerText =
    "Built " + (data.built_at || "-")
    + " | basis " + (data.strategy_basis || "-")
    + " | strategies " + (data.strategy_count || 0)
    + " | rows " + ((data.rows || []).length);
  body.innerHTML = "";
  (data.rows || []).forEach(function(row) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (row.code || "") + "</td>"
      + "<td>" + Number(row.recommendation_score || 0).toFixed(2) + "</td>"
      + "<td>" + Number(row.leader_score || 0).toFixed(2) + "</td>"
      + "<td>" + ((row.strategy_name || "") + " " + (row.strategy_version || "")) + "</td>"
      + "<td>" + Number(row.win_rate || 0).toFixed(1) + "%</td>"
      + "<td class='" + ((row.total_return || 0) >= 0 ? "up" : "dn") + "'>" + Number(row.total_return || 0).toFixed(2) + "%</td>";
    body.appendChild(tr);
  });
}

function loadLatestRecommendations() {
  return fetch("/api/recommendations/latest")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      renderRecommendationSnapshot(d.snapshot || null);
      return d;
    })
    .catch(function() {});
}

function applyRecommendationsToWorker() {
  if (!latestRecommendations || !(latestRecommendations.rows || []).length) return;
  var codes = latestRecommendations.rows.map(function(row) { return row.code; }).filter(Boolean);
  if (document.getElementById("workerSymbols")) {
    document.getElementById("workerSymbols").value = codes.join(",");
  }
  var box = document.getElementById("workerStatusSummary");
  if (box) {
    box.innerText = "Recommendation codes applied to worker: " + codes.length;
    box.style.color = "#53dfd0";
  }
}

function runRecommendationBuilder() {
  var box = document.getElementById("recommendationSummary");
  box.innerText = "Building recommendations...";
  return fetch("/api/recommendations/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config: readRecommendationConfig() })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    renderRecommendationSnapshot(d.snapshot || null);
    applyRecommendationsToWorker();
  })
  .catch(function(err) {
    box.innerText = "Recommendation build failed: " + err.message;
    box.style.color = "#ff8e8a";
  });
}

function loadLatestUniverse() {
  return fetch("/api/universe/latest")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      renderUniverseSnapshot(d.snapshot || null);
      return d;
    })
    .catch(function() {});
}

function applyUniverseToWorker() {
  if (!latestUniverse || !(latestUniverse.rows || []).length) return;
  var codes = latestUniverse.rows.map(function(row) { return row.code; }).filter(Boolean);
  if (document.getElementById("workerSymbols")) {
    document.getElementById("workerSymbols").value = codes.join(",");
  }
  var box = document.getElementById("workerStatusSummary");
  if (box) {
    box.innerText = "Universe codes applied to worker: " + codes.length;
    box.style.color = "#53dfd0";
  }
}

function runUniverseBuilder() {
  var box = document.getElementById("universeSummary");
  box.innerText = "Building universe...";
  return fetch("/api/universe/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config: readUniverseConfig() })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    renderUniverseSnapshot(d.snapshot || null);
    applyUniverseToWorker();
  })
  .catch(function(err) {
    box.innerText = "Universe build failed: " + err.message;
    box.style.color = "#ff8e8a";
  });
}

function loadLabSnapshot() {
  return fetch("/api/lab")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      experimentRows = d.top_experiments || [];
      renderLabSnapshot(d);
      return d;
    });
}

function readWorkerConfig(runOnce) {
  return {
    symbols: document.getElementById("workerSymbols").value.trim(),
    timeframes: document.getElementById("workerTFs").value.trim(),
    windows: document.getElementById("workerWindows").value.trim(),
    bars: parseInt(document.getElementById("workerBars").value || "1000", 10) || 1000,
    interval_sec: parseInt(document.getElementById("workerIntervalSec").value || "300", 10) || 300,
    save_top_n: parseInt(document.getElementById("workerTopN").value || "3", 10) || 3,
    limit: 12,
    auto_candidate: !!document.getElementById("workerAutoCandidate").checked,
    run_once: !!runOnce
  };
}

function renderWorkerStatus(data) {
  var box = document.getElementById("workerStatusSummary");
  if (!box || !data) return;
  var cfg = data.config || {};
  var job = data.current_job || {};
  var line = (data.running ? "RUNNING" : "IDLE")
    + " | cycles " + (data.completed_cycles || 0)
    + " | jobs " + (data.completed_jobs || 0);
  if (cfg.symbols && cfg.symbols.length) {
    line += " | symbols " + cfg.symbols.join(",");
  }
  if (cfg.timeframes && cfg.timeframes.length) {
    line += " | tfs " + cfg.timeframes.join(",");
  }
  if (job.code) {
    line += " | current " + job.code + "/" + (job.tf || "-") + "/" + (job.window || "-") + " [" + (job.step || "-") + "]";
  }
  if (data.last_error) {
    line += " | error " + data.last_error;
    box.style.color = "#ff8e8a";
  } else {
    box.style.color = data.running ? "#53dfd0" : "#c7d0db";
  }
  if ((data.last_results || []).length > 0) {
    var top = data.last_results[0];
    line += " | top " + (top.code || "") + "/" + (top.tf || "") + " score=" + Number((((top.summary || {}).score) || 0)).toFixed(2);
  }
  box.innerText = line;
}

function loadWorkerStatus() {
  return fetch("/api/worker_status")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      renderWorkerStatus(d);
      return d;
    })
    .catch(function() {});
}

function selectStrategy(id) {
  if (!id || id === "new") {
    newStrategyForm();
    return;
  }
  var found = savedStrategies.find(function(item) { return String(item.id) === String(id); });
  if (found) writeStrategyForm(found);
}

function validateStrategyPayload(payload) {
  return fetch("/api/strategies/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    var box = document.getElementById("strategyValidateResult");
    if (d.valid) {
      box.innerText = "Validation passed.";
      box.style.color = "#53dfd0";
      return d;
    }
    box.innerText = "Validation failed: " + (d.error || "unknown");
    box.style.color = "#ff8e8a";
    throw new Error(d.error || "validation failed");
  });
}

function saveStrategyForm() {
  var payload = readStrategyForm();
  validateStrategyPayload(payload)
    .then(function() {
      return fetch("/api/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) throw new Error(d.error);
      writeStrategyForm(d);
      return loadStrategies().then(function() {
        document.getElementById("smList").value = d.id;
        document.getElementById("strategyValidateResult").innerText = "Saved: " + d.name + " " + (d.version || "");
        return loadLabSnapshot();
      });
    })
    .catch(function(err) {
      document.getElementById("strategyValidateResult").innerText = "Save failed: " + err.message;
      document.getElementById("strategyValidateResult").style.color = "#ff8e8a";
    });
}

function promoteStrategyForm() {
  var payload = readStrategyForm();
  if (!payload.id) {
    document.getElementById("strategyValidateResult").innerText = "Save the strategy before promotion.";
    document.getElementById("strategyValidateResult").style.color = "#ff8e8a";
    return;
  }
  payload.stage = "promoted";
  validateStrategyPayload(payload)
  .then(function() {
    return fetch("/api/strategies/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    writeStrategyForm(d);
    document.getElementById("strategyValidateResult").innerText = "Promoted: " + d.name + " " + (d.version || "");
    document.getElementById("strategyValidateResult").style.color = "#53dfd0";
    return loadStrategies().then(function() { return loadLabSnapshot(); });
  })
  .catch(function(err) {
    document.getElementById("strategyValidateResult").innerText = "Promotion failed: " + err.message;
    document.getElementById("strategyValidateResult").style.color = "#ff8e8a";
  });
}

function deleteStrategyForm() {
  var id = document.getElementById("smId").value.trim();
  if (!id) return;
  fetch("/api/strategies/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id })
  })
  .then(function(r) { return r.json(); })
  .then(function() {
    newStrategyForm();
    return loadStrategies().then(function() { return loadLabSnapshot(); });
  });
}

function validateStrategyForm() {
  validateStrategyPayload(readStrategyForm()).catch(function() {});
}

function renderStrategyTrades(targetId, trades) {
  var body = document.getElementById(targetId);
  if (!body) return;
  body.innerHTML = "";
  (trades || []).forEach(function(t) {
    var tr = document.createElement("tr");
    var ret = Number(t.ret || 0);
    tr.innerHTML = "<td>" + fmtStrategyTime(t.entry_time) + "</td>"
      + "<td>" + fmtStrategyTime(t.exit_time) + "</td>"
      + "<td>" + (t.bars || 0) + "</td>"
      + "<td>" + (t.reason || "-") + "</td>"
      + "<td class='" + (ret >= 0 ? "up" : "dn") + "'>" + (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%</td>";
    body.appendChild(tr);
  });
}

function renderStrategyResult(result, summaryId, tradesId) {
  var s = result.summary || {};
  var summaryText = "Trades " + (s.trades || 0)
    + " | Win " + (s.win_rate || 0) + "%"
    + " | Total " + (s.total_return || 0) + "%"
    + " | Avg " + (s.avg_ret || 0) + "%"
    + " | PF " + (s.profit_factor || 0);
  var box = document.getElementById(summaryId);
  if (box) {
    box.innerText = summaryText;
    box.style.color = (s.total_return || 0) >= 0 ? "#53dfd0" : "#ff8e8a";
  }
  renderStrategyTrades(tradesId, result.trades || []);
}

function fetchBacktest(strategy, applyToChart) {
  return fetch("/api/backtest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code: (strategy.code || document.getElementById("simCode").value.trim()),
      tf: document.getElementById("simTF").value,
      strategy: strategy,
      range_start: simRangeStart,
      range_end: simRangeEnd,
      apply_view_params: !!applyToChart
    })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    return d;
  });
}

function requestBacktest(strategy, applyToChart) {
  return fetchBacktest(strategy, applyToChart).then(function(d) {
    renderStrategyResult(d, "btSummary", "strategyTradesBody");
    renderStrategyResult(d, "strategyPanelSummary", "strategyTradesBody");
    renderStrategyResult(d, "strategyEvalSummary", "strategyEvalTrades");
    if (chart && d.markers) {
      if (markerHandle) markerHandle.setMarkers(d.markers);
      else markerHandle = LWC.createSeriesMarkers(cs, d.markers);
    }
    currentStrategyResult = d;
    if (applyToChart) {
      currentStrategy = strategy;
      document.getElementById("activeStrategyName").innerText = (strategy.name || "Unnamed") + " " + (strategy.version || "");
      renderStep();
    }
    return d;
  });
}

function insertStrategyToChart() {
  var strategy = readStrategyForm();
  validateStrategyPayload(strategy)
    .then(function() { return requestBacktest(strategy, true); })
    .catch(function(err) {
      document.getElementById("strategyEvalSummary").innerText = "Insert failed: " + err.message;
    });
}

function insertCurrentStrategy() {
  if (currentStrategy) {
    requestBacktest(currentStrategy, true).catch(function(err) {
      document.getElementById("btSummary").innerText = "Insert failed: " + err.message;
    });
  } else {
    openStrategyManager();
  }
}

function runPreciseEvaluation() {
  var strategy = document.getElementById("strategyDlg").classList.contains("show") ? readStrategyForm() : currentStrategy;
  if (!strategy) {
    openStrategyManager();
    return;
  }
  validateStrategyPayload(strategy)
    .then(function() { return requestBacktest(strategy, false); })
    .catch(function(err) {
      document.getElementById("strategyEvalSummary").innerText = "Evaluation failed: " + err.message;
    });
}

function compareSelectedStrategies() {
  var baseId = document.getElementById("cmpBase").value;
  var candId = document.getElementById("cmpCand").value;
  var base = savedStrategies.find(function(item) { return String(item.id) === String(baseId); });
  var cand = savedStrategies.find(function(item) { return String(item.id) === String(candId); });
  if (!base || !cand) {
    document.getElementById("strategyCompareSummary").innerText = "Select two saved strategies first.";
    return;
  }
  var summaryEl = document.getElementById("strategyCompareSummary");
  summaryEl.innerText = "Comparing...";
  Promise.all([
    fetchBacktest(base, false),
    fetchBacktest(cand, false)
  ])
  .then(function(results) {
    var r1 = results[0].summary || {};
    var r2 = results[1].summary || {};
    var deltaTotal = Number((r2.total_return || 0) - (r1.total_return || 0)).toFixed(2);
    var deltaWin = Number((r2.win_rate || 0) - (r1.win_rate || 0)).toFixed(1);
    var deltaTrades = Number((r2.trades || 0) - (r1.trades || 0)).toFixed(0);
    summaryEl.innerText =
      (base.name || "Base") + " " + (base.version || "") + ": Total " + (r1.total_return || 0) + "%, Win " + (r1.win_rate || 0) + "%, Trades " + (r1.trades || 0)
      + " || "
      + (cand.name || "Cand") + " " + (cand.version || "") + ": Total " + (r2.total_return || 0) + "%, Win " + (r2.win_rate || 0) + "%, Trades " + (r2.trades || 0)
      + " || \u0394Total " + (deltaTotal >= 0 ? "+" : "") + deltaTotal + "%, \u0394Win " + (deltaWin >= 0 ? "+" : "") + deltaWin + "%p, \u0394Trades " + (deltaTrades >= 0 ? "+" : "") + deltaTrades;
    summaryEl.style.color = Number(deltaTotal) >= 0 ? "#53dfd0" : "#ff8e8a";
  })
  .catch(function(err) {
    summaryEl.innerText = "Compare failed: " + err.message;
    summaryEl.style.color = "#ff8e8a";
  });
}

function startLabWorker(runOnce) {
  var strategy = document.getElementById("strategyDlg").classList.contains("show") ? readStrategyForm() : currentStrategy;
  if (!strategy) {
    openStrategyManager();
    return;
  }
  validateStrategyPayload(strategy)
    .then(function() {
      return fetch("/api/worker/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy: strategy,
          config: readWorkerConfig(runOnce)
        })
      });
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) throw new Error(d.error);
      renderWorkerStatus(d);
      document.getElementById("strategyValidateResult").innerText = runOnce ? "Worker started for one cycle." : "Worker loop started.";
      document.getElementById("strategyValidateResult").style.color = "#53dfd0";
    })
    .catch(function(err) {
      document.getElementById("workerStatusSummary").innerText = "Worker start failed: " + err.message;
      document.getElementById("workerStatusSummary").style.color = "#ff8e8a";
    });
}

function stopLabWorker() {
  fetch("/api/worker/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    renderWorkerStatus(d);
    return loadLabSnapshot();
  })
  .catch(function(err) {
    document.getElementById("workerStatusSummary").innerText = "Worker stop failed: " + err.message;
    document.getElementById("workerStatusSummary").style.color = "#ff8e8a";
  });
}

function runBatchSearch() {
  var strategy = document.getElementById("strategyDlg").classList.contains("show") ? readStrategyForm() : currentStrategy;
  if (!strategy) {
    openStrategyManager();
    return;
  }
  var summaryEl = document.getElementById("strategyLabSummary");
  summaryEl.innerText = "Running batch search...";
  validateStrategyPayload(strategy)
  .then(function() {
    return fetch("/api/experiments/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy: strategy,
        range_start: simRangeStart,
        range_end: simRangeEnd,
        limit: 12
      })
    });
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) throw new Error(d.error);
    renderLabSnapshot(d.snapshot || {});
    if ((d.results || []).length > 0) {
      var winner = d.results[0];
      document.getElementById("strategyValidateResult").innerText =
        "Batch winner: " + ((winner.strategy || {}).version || "") + " | score " + Number(((winner.summary || {}).score || 0)).toFixed(2);
      document.getElementById("strategyValidateResult").style.color = "#53dfd0";
    }
    return loadLabSnapshot();
  })
  .catch(function(err) {
    summaryEl.innerText = "Batch search failed: " + err.message;
    summaryEl.style.color = "#ff8e8a";
  });
}

function resetStrategyRuntime() {
  currentStrategy = null;
  currentStrategyResult = null;
  document.getElementById("activeStrategyName").innerText = "None";
  document.getElementById("btSummary").innerText = "No strategy result yet.";
  document.getElementById("strategyPanelSummary").innerText = "No strategy result yet.";
  document.getElementById("strategyTradesBody").innerHTML = "";
  document.getElementById("strategyStatusText").innerText = "Open the manager, validate a strategy, insert it into the chart, then use range play to confirm live entry timing.";
  setStrategySignalChip("neutral", "Idle");
  try {
    if (markerHandle) markerHandle.setMarkers([]);
  } catch (e) {}
}

function requestStrategyStatus() {
  if (!currentStrategy || pendingStrategyStatus || simTotal === 0) return;
  pendingStrategyStatus = true;
  fetch("/api/strategy_status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idx: simCurrentIdx, strategy: currentStrategy })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    pendingStrategyStatus = false;
    if (!d.ready) {
      setStrategySignalChip("neutral", d.message || "Idle");
      document.getElementById("strategyStatusText").innerText = d.message || "Waiting";
      return;
    }
    setStrategySignalChip(d.signal || "neutral", (d.signal || "neutral").toUpperCase());
    document.getElementById("strategyStatusText").innerText =
      "close=" + d.ctx.close + " | macd=" + d.ctx.macd + " | signal=" + d.ctx.macd_signal
      + " | obv=" + d.ctx.obv + " | obvSig=" + d.ctx.obv_signal
      + " | stTrend=" + d.ctx.supertrend_trend
      + " | zzTrend=" + d.ctx.zigzag_trend
      + " | zzTurnUp=" + d.ctx.zigzag_turn_up;
  })
  .catch(function() {
    pendingStrategyStatus = false;
  });
}

function initChart() {
  markerHandle = null;
  var container = document.getElementById("chart");
  container.innerHTML = "";

  chart = LWC.createChart(container, {
    layout: { background: { type: LWC.ColorType.Solid, color: "#0c0d14" }, textColor: "#d1d4dc" },
    grid: { vertLines: { color: "rgba(255, 255, 255, 0.03)" }, horzLines: { color: "rgba(255, 255, 255, 0.03)" } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.25 } },
    timeScale: {
      borderVisible: false,
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: function(time, tickMarkType, locale) {
        if (typeof time !== "number") return time;
        var d = new Date(time * 1000);
        var hh = String(d.getUTCHours()).padStart(2, "0");
        var mi = String(d.getUTCMinutes()).padStart(2, "0");
        var mo = d.getUTCMonth() + 1;
        var da = d.getUTCDate();
        if ((hh === "00" && Number(mi) < 10) || tickMarkType === 2 || tickMarkType === 1) {
          return mo + "/" + da + " " + hh + ":" + mi;
        }
        return hh + ":" + mi;
      }
    },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    localization: {
      timeFormatter: function(time) {
        if (typeof time !== "number") return time;
        var d = new Date(time * 1000);
        var mo = d.getUTCMonth() + 1;
        var da = d.getUTCDate();
        var hh = String(d.getUTCHours()).padStart(2, "0");
        var mi = String(d.getUTCMinutes()).padStart(2, "0");
        return mo + "/" + da + " " + hh + ":" + mi;
      }
    }
  });

  cs = chart.addSeries(LWC.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350"
  });

  vs = chart.addSeries(LWC.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
    lastValueVisible: false,
    priceLineVisible: false
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

  maS = [
    chart.addSeries(LWC.LineSeries, { color: "#ffeb3b", lineWidth: 1, title: "MA5" }),
    chart.addSeries(LWC.LineSeries, { color: "#2196f3", lineWidth: 1, title: "MA20" }),
    chart.addSeries(LWC.LineSeries, { color: "#e040fb", lineWidth: 1, title: "MA60" })
  ];

  obvs = chart.addSeries(LWC.LineSeries, { color: "#00e676", lineWidth: 1.5, title: "OBV", lastValueVisible: false, priceLineVisible: false }, 1);
  obvsigs = chart.addSeries(LWC.LineSeries, { color: "#ef5350", lineWidth: 1, title: "OBV Sig", lastValueVisible: false, priceLineVisible: false }, 1);

  macds = chart.addSeries(LWC.LineSeries, { color: "#2962ff", lineWidth: 1, title: "MACD", lastValueVisible: false, priceLineVisible: false }, 2);
  macdsigs = chart.addSeries(LWC.LineSeries, { color: "#ff9100", lineWidth: 1, title: "Signal", lastValueVisible: false, priceLineVisible: false }, 2);
  macdhs = chart.addSeries(LWC.HistogramSeries, { lastValueVisible: false, priceLineVisible: false }, 2);

  jmaS = chart.addSeries(LWC.LineSeries, { color: "#00e676", lineWidth: 2, title: "JMA" });
  supertrendS = chart.addSeries(LWC.LineSeries, { color: "#ff7a5c", lineWidth: 2, title: "Supertrend" });
  vwmaS = chart.addSeries(LWC.LineSeries, { color: "#ffffff", lineWidth: 1.5, title: "VWMA" });

  zzConfirmedS = chart.addSeries(LWC.LineSeries, { color: "#2962ff", lineWidth: 2.5, title: "ZigZag(Conf)" });
  zzUnconfirmedS = chart.addSeries(LWC.LineSeries, { color: "#ef5350", lineWidth: 2, lineStyle: LWC.LineStyle.Dashed, title: "ZigZag(Unconf)" });

  fractUpS = chart.addSeries(LWC.LineSeries, { color: "#ff9100", lineWidth: 2, title: "Fractal Resist" });
  fractDnS = chart.addSeries(LWC.LineSeries, { color: "#ab47bc", lineWidth: 2, title: "Fractal Support" });

  lrCenterS = chart.addSeries(LWC.LineSeries, { color: "#90bec5", lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, title: "LR Center" });
  lrUpperS = chart.addSeries(LWC.LineSeries, { color: "#26a69a", lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, title: "LR Upper" });
  lrLowerS = chart.addSeries(LWC.LineSeries, { color: "#ef5350", lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, title: "LR Lower" });

  applyIndicatorsVisibility();

  try { chart.subscribeClick(onChartClick); } catch (e) {}

  try {
    var _panes = chart.panes();
    for (var _pi = 1; _pi < _panes.length; _pi++) { _panes[_pi].setHeight(120); }
  } catch (e) { console.warn("pane height", e); }

  window.addEventListener("resize", function() {
    chart.resize(container.clientWidth, container.clientHeight);
  });
}

function applyIndicatorsVisibility() {
  var maVisible = document.getElementById("show_ma").checked;
  maS.forEach(function(s) { s.applyOptions({ visible: maVisible }); });

  obvs.applyOptions({ visible: document.getElementById("show_obv").checked });
  obvsigs.applyOptions({ visible: document.getElementById("show_obv").checked });

  var macdVisible = document.getElementById("show_macd").checked;
  macds.applyOptions({ visible: macdVisible });
  macdsigs.applyOptions({ visible: macdVisible });
  macdhs.applyOptions({ visible: macdVisible });

  jmaS.applyOptions({ visible: document.getElementById("show_jma").checked });
  supertrendS.applyOptions({ visible: document.getElementById("show_supertrend").checked });
  vwmaS.applyOptions({ visible: document.getElementById("show_vwma").checked });

  var zzVisible = document.getElementById("show_zigzag").checked;
  zzConfirmedS.applyOptions({ visible: zzVisible });
  zzUnconfirmedS.applyOptions({ visible: zzVisible });

  var fractVisible = document.getElementById("show_fractals").checked;
  fractUpS.applyOptions({ visible: fractVisible });
  fractDnS.applyOptions({ visible: fractVisible });

  var lrVisible = document.getElementById("show_lr").checked;
  lrCenterS.applyOptions({ visible: lrVisible });
  lrUpperS.applyOptions({ visible: lrVisible });
  lrLowerS.applyOptions({ visible: lrVisible });
}

function toggleIndicator(id) {
  applyIndicatorsVisibility();
}

function timeToIndex(t) {
  if (typeof t !== "number") return null;
  var best = null, bestDiff = Infinity;
  if (!window._lastStepCandles) return null;
  var arr = window._lastStepCandles;
  for (var i = 0; i < arr.length; i++) {
    var d = Math.abs(arr[i].time - t);
    if (d < bestDiff) { bestDiff = d; best = i; }
  }
  return best;
}

function toggleRangeSelect() {
  if (simTotal === 0) { alert("먼저 데이터를 다운로드하세요."); return; }
  rangeSelectMode = !rangeSelectMode;
  rangeClickStage = 0;
  var btn = document.getElementById("rangeBtn");
  if (rangeSelectMode) {
    btn.classList.add("active");
    btn.innerText = "구간선택중";
    document.getElementById("chartSymbol").innerText = "구간 선택: 시작 봉을 클릭하세요";
  } else {
    btn.classList.remove("active");
    btn.innerText = "구간선택";
  }
}

function viewAllCandles() {
  simPause();
  if (simTotal === 0) { alert("먼저 데이터를 다운로드하세요."); return; }
  simCurrentIdx = simTotal - 1;
  renderStep();
  setTimeout(setRangeMarkers, 150);
}

function clearRange() {
  simRangeStart = null;
  simRangeEnd = null;
  rangeClickStage = 0;
  rangeSelectMode = false;
  var btn = document.getElementById("rangeBtn");
  if (btn) { btn.classList.remove("active"); btn.innerText = "구간선택"; }
  try {
    if (rangeMarkerHandle) { rangeMarkerHandle.setMarkers([]); }
  } catch (e) {}
  document.getElementById("chartSymbol").innerText = (document.getElementById("simCode").value || "") + " (전체 구간)";
}

function setRangeMarkers() {
  if (!cs || !window._lastStepCandles) return;
  var arr = window._lastStepCandles;
  var marks = [];
  if (simRangeStart != null && arr[simRangeStart]) {
    marks.push({ time: arr[simRangeStart].time, position: "belowBar", color: "#42a5f5", shape: "arrowUp", text: "시작" });
  }
  if (simRangeEnd != null && arr[simRangeEnd]) {
    marks.push({ time: arr[simRangeEnd].time, position: "aboveBar", color: "#ff9100", shape: "arrowDown", text: "끝" });
  }
  try {
    if (rangeMarkerHandle) { rangeMarkerHandle.setMarkers(marks); }
    else { rangeMarkerHandle = LWC.createSeriesMarkers(cs, marks); }
  } catch (e) { console.warn("range marker", e); }
}

function onChartClick(param) {
  if (!rangeSelectMode || !param || param.time == null) return;
  var idx = timeToIndex(param.time);
  if (idx == null) return;

  if (rangeClickStage === 0) {
    simRangeStart = idx;
    rangeClickStage = 1;
    document.getElementById("chartSymbol").innerText = "시작=" + idx + "봉 / 끝 봉을 클릭하세요";
  } else {
    simRangeEnd = idx;
    if (simRangeStart > simRangeEnd) {
      var tmp = simRangeStart; simRangeStart = simRangeEnd; simRangeEnd = tmp;
    }
    if (simRangeStart < 60) simRangeStart = 60;
    rangeSelectMode = false;
    rangeClickStage = 0;
    var btn = document.getElementById("rangeBtn");
    if (btn) { btn.classList.remove("active"); btn.innerText = "구간선택"; }
    document.getElementById("chartSymbol").innerText = "구간 " + simRangeStart + " ~ " + simRangeEnd + "봉 선택됨";
    simCurrentIdx = simRangeStart;
    renderStep();
    setTimeout(setRangeMarkers, 150);
  }
}

function loadSimulationData() {
  var code = document.getElementById("simCode").value.trim();
  var tf = document.getElementById("simTF").value;
  var date = document.getElementById("simDate").value;
  var time = document.getElementById("simTime").value;

  if (!code) {
    alert("종목코드를 입력하세요.");
    return;
  }

  document.getElementById("chartSymbol").innerText = "다운로드 중...";
  simStop();

  fetch("/api/init_simulation?code=" + code + "&tf=" + tf + "&date=" + date + "&time=" + time)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) {
        alert("데이터 수신 오류: " + d.error);
        document.getElementById("chartSymbol").innerText = "수신 오류";
        return;
      }

      simTotal = d.total_bars;
      simCurrentIdx = d.start_idx;

      document.getElementById("chartSymbol").innerText = code + " (" + simTotal + "봉 로드)";
      initChart();
      resetTradingAccount();
      resetStrategyRuntime();
      if (document.getElementById("smCode")) document.getElementById("smCode").value = code;
      renderStep();
    })
    .catch(function(e) {
      alert("네트워크 오류: " + e);
      document.getElementById("chartSymbol").innerText = "연결 실패";
    });
}

var _rendering = false;
function renderStep() {
  if (simTotal === 0 || _rendering) return;
  _rendering = true;

  fetch("/api/simulation_step?idx=" + simCurrentIdx)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _rendering = false;
      if (d.error) return;

      window._lastStepCandles = d.candles;
      cs.setData(d.candles);
      try { if (isPlaying && markerHandle) markerHandle.setMarkers(d.markers || []); } catch (e) {}
      vs.setData(d.volumes);

      if (d.ma) {
        d.ma.forEach(function(m, idx) {
          if (maS[idx] && m.data) maS[idx].setData(m.data);
        });
      }

      if (obvs && d.obv) obvs.setData(d.obv);
      if (obvsigs && d.obv_signal) obvsigs.setData(d.obv_signal);
      if (macds && d.macd) macds.setData(d.macd);
      if (macdsigs && d.macd_signal) macdsigs.setData(d.macd_signal);
      if (macdhs && d.macd_hist) macdhs.setData(d.macd_hist);

      if (jmaS && d.jma) jmaS.setData(d.jma);
      if (supertrendS && d.supertrend) supertrendS.setData(d.supertrend);
      if (vwmaS && d.vwma) vwmaS.setData(d.vwma);

      if (zzConfirmedS && d.zigzag_confirmed) zzConfirmedS.setData(d.zigzag_confirmed);
      if (zzUnconfirmedS && d.zigzag_unconfirmed) zzUnconfirmedS.setData(d.zigzag_unconfirmed);

      if (fractUpS && d.fractals) fractUpS.setData(d.fractals.up_line || []);
      if (fractDnS && d.fractals) fractDnS.setData(d.fractals.dn_line || []);

      if (lrCenterS && d.lr_channel) lrCenterS.setData(d.lr_channel.center || []);
      if (lrUpperS && d.lr_channel) lrUpperS.setData(d.lr_channel.upper || []);
      if (lrLowerS && d.lr_channel) lrLowerS.setData(d.lr_channel.lower || []);

      updateAccountUI(d.candles[d.candles.length - 1].close);
      requestStrategyStatus();
    })
    .catch(function() { _rendering = false; });
}

function simTogglePlay() {
  if (isPlaying) { simPause(); } else { simPlay(); }
}

function simPlay() {
  if (simTotal === 0) return;
  isPlaying = true;
  document.getElementById("playBtn").innerText = "일시정지";
  document.getElementById("playBtn").classList.add("active");

  function runLoop() {
    if (!isPlaying) return;
    var _end = (simRangeEnd != null) ? simRangeEnd : (simTotal - 1);
    if (simCurrentIdx >= _end) {
      simPause();
      return;
    }
    simCurrentIdx++;
    renderStep();
    var speed = parseInt(document.getElementById("simSpeed").value) || 200;
    playTimer = setTimeout(runLoop, speed);
  }
  runLoop();
}

function simPause() {
  isPlaying = false;
  if (playTimer) {
    clearTimeout(playTimer);
    playTimer = null;
  }
  document.getElementById("playBtn").innerText = "플레이";
  document.getElementById("playBtn").classList.remove("active");
}

function simStop() { simPause(); }

function simGoStart() {
  simPause();
  simCurrentIdx = (simRangeStart != null) ? simRangeStart : 60;
  renderStep();
  setTimeout(setRangeMarkers, 150);
}

function simPrev() {
  simPause();
  var _s = (simRangeStart != null) ? simRangeStart : 60;
  if (simCurrentIdx > _s) {
    simCurrentIdx--;
    renderStep();
  }
}

function simNext() {
  simPause();
  var _e = (simRangeEnd != null) ? simRangeEnd : (simTotal - 1);
  if (simCurrentIdx < _e) {
    simCurrentIdx++;
    renderStep();
  }
}

function simGoEnd() {
  simPause();
  simCurrentIdx = (simRangeEnd != null) ? simRangeEnd : (simTotal - 1);
  renderStep();
  setTimeout(setRangeMarkers, 150);
}

function resetTradingAccount() {
  balance = initialBalance;
  positionQty = 0;
  avgEntryPrice = 0;
  tradesLog = [];
  document.getElementById("tradeLogBody").innerHTML = "";
  updateAccountUI(0);
}

function updateAccountUI(currentPrice) {
  var pnl = 0;
  var pnlPct = 0;
  if (positionQty > 0 && currentPrice > 0) {
    pnl = (currentPrice - avgEntryPrice) * positionQty;
    pnlPct = (pnl / (avgEntryPrice * positionQty)) * 100;
  }

  document.getElementById("trBalance").innerText = balance.toLocaleString() + " 원";
  var pnlEl = document.getElementById("trPnL");
  pnlEl.innerText = pnl.toLocaleString() + " 원 (" + pnlPct.toFixed(2) + "%)";
  if (pnl > 0) { pnlEl.className = "val up"; } else if (pnl < 0) { pnlEl.className = "val dn"; } else { pnlEl.className = "val"; }

  document.getElementById("trQty").innerText = positionQty + " 주";
  document.getElementById("trAvgPrice").innerText = Math.round(avgEntryPrice).toLocaleString() + " 원";
}

function executeTrade(side) {
  if (simTotal === 0) {
    alert("시뮬레이션 데이터를 먼저 로드해 주세요.");
    return;
  }

  fetch("/api/simulation_step?idx=" + simCurrentIdx)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.candles || d.candles.length === 0) return;
      var lastCandle = d.candles[d.candles.length - 1];
      var price = lastCandle.close;
      var timeStr = lastCandle.time;

      if (typeof timeStr === "number") {
        timeStr = new Date(timeStr * 1000).toLocaleTimeString("ko-KR", {hour12: false});
      }

      if (side === "buy") {
        var buyQty = Math.floor(balance / price);
        if (buyQty <= 0) {
          alert("예수금이 부족합니다.");
          return;
        }
        var cost = buyQty * price;
        avgEntryPrice = ((avgEntryPrice * positionQty) + cost) / (positionQty + buyQty);
        positionQty += buyQty;
        balance -= cost;
        logTrade(timeStr, "매수", price, buyQty, 0);
      } else if (side === "sell") {
        if (positionQty <= 0) {
          alert("보유 주식이 없습니다.");
          return;
        }
        var revenue = positionQty * price;
        var pnl = (price - avgEntryPrice) * positionQty;
        balance += revenue;
        logTrade(timeStr, "매도", price, positionQty, pnl);
        positionQty = 0;
        avgEntryPrice = 0;
      } else if (side === "exit") {
        if (positionQty <= 0) return;
        var revenue = positionQty * price;
        var pnl = (price - avgEntryPrice) * positionQty;
        balance += revenue;
        logTrade(timeStr, "청산", price, positionQty, pnl);
        positionQty = 0;
        avgEntryPrice = 0;
      }
      updateAccountUI(price);
    });
}

function logTrade(time, side, price, qty, pnl) {
  var tbody = document.getElementById("tradeLogBody");
  var tr = document.createElement("tr");

  var clrClass = side === "매수" ? "up" : "dn";
  var pnlStr = pnl !== 0 ? pnl.toLocaleString() + " 원" : "-";
  var pnlClass = pnl > 0 ? "up" : (pnl < 0 ? "dn" : "");

  tr.innerHTML = "<td>" + time + "</td><td class='" + clrClass + "'>" + side + "</td><td>" + price.toLocaleString() + "</td><td>" + qty + "</td><td class='" + pnlClass + "'>" + pnlStr + "</td>";
  tbody.insertBefore(tr, tbody.firstChild);
}

function runBacktest() {
  var code = document.getElementById("simCode").value.trim();
  var tf = document.getElementById("simTF").value;
  var entry = document.getElementById("sEntry").value.trim();
  var exit = document.getElementById("sExit").value.trim();
  var qty = parseInt(document.getElementById("sQty").value) || 100;

  if (simTotal === 0) {
    alert("시뮬레이션 데이터를 먼저 다운로드하세요.");
    return;
  }

  fetch("/api/backtest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code: code,
      tf: tf,
      strategy: { entry_expr: entry, exit_expr: exit, qty: qty }
    })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) {
      alert("백테스트 에러: " + d.error);
      return;
    }
    if (cs && d.markers) {
      try {
        if (markerHandle) { markerHandle.setMarkers(d.markers); }
        else { markerHandle = LWC.createSeriesMarkers(cs, d.markers); }
      } catch (e) { console.warn("marker", e); }
    }
    var summaryText = "수익률: " + d.total_return + "% | 거래수: " + d.n_trades + "회 | 승률: " + d.win_rate + "%";
    document.getElementById("btSummary").innerText = summaryText;
  })
  .catch(function(e) { alert("통신 오류: " + e); });
}

function startApp() {
  var now = new Date();
  var yyyy = now.getFullYear();
  var mm = String(now.getMonth() + 1).padStart(2, '0');
  var dd = String(now.getDate()).padStart(2, '0');
  document.getElementById("simDate").value = yyyy + "-" + mm + "-" + dd;
  buildStrategyParamDefs();
  buildStrategyParamFields();
  loadStrategies().then(function() {
    newStrategyForm();
    return loadLabSnapshot();
  });
  loadLatestUniverse();
  loadLatestRecommendations();
  loadWorkerStatus();
  setInterval(function() {
    loadWorkerStatus();
  }, 5000);
  var legacyBox = document.getElementById("sEntry") ? document.getElementById("sEntry").closest(".panel-box") : null;
  if (legacyBox) {
    var legacyTitle = legacyBox.previousElementSibling;
    legacyBox.style.display = "none";
    if (legacyTitle && legacyTitle.classList.contains("section-title")) legacyTitle.style.display = "none";
  }
  initChart();
}

window.onload = function() {
  fetch("/api/bootstrap")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      activeParams = d.default_params || {};
      conditionRowSample = d.condition_row_sample || [];
      startApp();
    })
    .catch(function() {
      activeParams = { ma_periods: [5, 20, 60] };
      conditionRowSample = [];
      startApp();
    });
};
