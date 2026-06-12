#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — 진입점 + 웹 대시보드 (lightweight-charts v5.1)
================================================================================
core + settings 조립. 차트 상태 보존(줌/스크롤), 타임프레임/지표/일자/표시개수,
속성 다이얼로그, 전략 작성/백테스트/모의매매, 매매신호 검증(마커+거래표).
실시간 차트는 증분 업데이트(series.update) 방식 — 전체 재그리기 없음.
"""

import time
import threading
import webbrowser
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[경고] python-dotenv 미설치")

from core import (Config, KiwoomREST, ConditionWS, RankingEngine,
                  StrategyEngine, jdump)

from settings import (Settings, StrategyStore, TIMEFRAMES,
                      INDICATOR_TOGGLES, DEFAULT_PARAMS)


class WebApp:
    def __init__(self, cfg, settings, top_n=10):
        self.cfg = cfg
        self.settings = settings
        self.top_n = top_n
        self.default_symbol = cfg.default_symbol
        self.rest = KiwoomREST(cfg)
        self.engine = RankingEngine(self.rest, settings)
        self.strategy_engine = StrategyEngine(self.rest, settings)
        self.store = StrategyStore()
        self.ws = None
        self.captured = set()
        self.metrics = {}
        self.cond_items = []
        self.last_log = ""
        self._analyzing = False
        # ... 기존 필드 뒤에 추가 ...
        self.rt = None
        self.rt_code = None
        self.rt_tf = "m1"
        self.latest_bar = None          # 최신 진행봉
        self.bar_seq = 0                # 변경 감지용 시퀀스
        self._bar_lock = threading.Lock()
        self._metrics_lock = threading.Lock()

    def start(self):
        self.rest.issue_token()
        print("[OK] 토큰 발급 완료 (" + self.cfg.mode + ")")
        self.ws = ConditionWS(self.cfg, self.rest.token,
                              on_list=self._on_list, on_event=self._on_event,
                              on_log=self._log, on_bar=self._on_bar)
        self.ws.start()
        self._analyzing = True
        threading.Thread(target=self._analyze_loop, daemon=True).start()

    def _on_bar(self, code, bar):
        with self._bar_lock:
            self.latest_bar = {"code": code, "bar": bar}
            self.bar_seq += 1

    def subscribe_rt(self, code, tf):
        self.rt_code, self.rt_tf = code, tf
        with self._bar_lock:
            self.latest_bar = None
        if self.ws:
            self.ws.subscribe_real(code, tf)

    def latest_bar_payload(self):
        with self._bar_lock:
            return {"seq": self.bar_seq, "data": self.latest_bar}

    def _log(self, msg):
        self.last_log = msg
        print("[WS]", msg)

    def _on_list(self, items):
        self.cond_items = items

    def _on_event(self, code, typ):
        with self._metrics_lock:
            if typ == "I":
                self.captured.add(code)
            elif typ == "D":
                self.captured.discard(code)
                self.metrics.pop(code, None)

    def _analyze_loop(self):
        while self._analyzing:
            try:
                self.engine.refresh_ranks()
                with self._metrics_lock:
                    codes = list(self.captured)
                self.engine.enrich_captured(codes)   # ★ ka10095 일괄 보충
                for code in codes:
                    if not self._analyzing:
                        break
                    m = self.engine.analyze(code)
                    if m:
                        with self._metrics_lock:
                            self.metrics[code] = m
            except Exception as e:
                print("분석 오류:", e)
            interval = self.settings.params["analyze_interval_sec"]
            for _ in range(max(5, interval)):
                if not self._analyzing:
                    break
                time.sleep(1)

    def ranking_payload(self, n):
        with self._metrics_lock:
            rows = [m for m in self.metrics.values() if self.engine.passes_filter(m)]
            captured_cnt = len(self.captured)
            analyzed_cnt = len(self.metrics)
        rows.sort(key=lambda x: x["score"], reverse=True)
        return {"captured": captured_cnt, "analyzed": analyzed_cnt,
                "filtered": len(rows), "mode": self.cfg.mode,
                "updated": datetime.now().strftime("%H:%M:%S"), "rows": rows[:n]}

    def chart_data(self, code, tf, max_bars):
        try:
            p = self.engine.chart_payload(code, tf, max_bars)
            return p if p else {"error": "데이터 없음"}
        except Exception as e:
            return {"error": str(e)}

    def stop(self):
        self._analyzing = False
        if self.ws:
            self.ws.stop()


def validate_expression(expr):
    if not expr:
        return "표현식이 빈칸입니다."
    try:
        import ast
        from core import SafeEval
        # Parse expression to ensure syntax is valid Python AST
        _ast_tree = ast.parse(expr.strip(), mode="eval")
        
        # Dry-run validation using SafeEval with dummy variables and funcs
        dummy_vars = {
            "close": 1000.0,
            "ma5": 1000.0,
            "ma20": 1000.0,
            "ma60": 1000.0,
            "obv": 50000.0,
            "obv_signal": 48000.0,
            "macd": 10.0,
            "macd_signal": 8.0,
            "macd_hist": 2.0,
            "disp20": 100.0,
            "supertrend": 1000.0,
            "supertrend_trend": 1,
            "jma": 1000.0,
            "vwma": 1000.0
        }
        dummy_funcs = {
            "crossover": lambda a, b: True,
            "crossunder": lambda a, b: False,
            "abs": abs,
            "min": min,
            "max": max
        }
        evaluator = SafeEval(dummy_vars, dummy_funcs)
        evaluator.eval(expr)
        return None  # None means valid
    except SyntaxError as se:
        return f"구문 오류 (SyntaxError): {se.msg} (위치: {se.offset})"
    except ValueError as ve:
        return f"허용되지 않은 구문/변수/함수: {str(ve)}"
    except Exception as e:
        return f"검증 오류: {str(e)}"


# ═══════════════════════════════════════════════════════════════
#  HTML
# ═══════════════════════════════════════════════════════════════
def build_html(app):
    cfg, st = app.cfg, app.settings
    h = []
    h.append('<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">')
    h.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    h.append('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    h.append('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">')
    h.append('<title>키움 조건검색 실시간 추천</title>')
    h.append('''<style>
* { margin:0; padding:0; box-sizing:border-box; }
/* Webkit Scrollbars Customization */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.1); }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.12); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.22); }

body { background:#0a0c10; color:#d1d4dc; font-family:'Inter', 'Segoe UI', -apple-system, sans-serif;
       display:flex; height:100vh; overflow:hidden; }
#left { width:580px; border-right:1px solid rgba(255, 255, 255, 0.08); display:flex; flex-direction:column; background: #0f1118; }
#right { flex:1; display:flex; flex-direction:column; min-width:0; background: #0f1118; }
.bar { padding:8px 12px; background:rgba(20, 24, 35, 0.75); backdrop-filter:blur(8px); border-bottom:1px solid rgba(255, 255, 255, 0.08);
       display:flex; align-items:center; gap:8px; font-size:12px; flex-wrap:wrap; }
.bar select, .bar input { padding:4px 8px; background:rgba(255, 255, 255, 0.05); border:1px solid rgba(255, 255, 255, 0.12);
       color:#e1e4ea; border-radius:6px; font-size:12px; outline:none; transition: all 0.2s ease; }
.bar select:focus, .bar input:focus { border-color: #2962ff; box-shadow: 0 0 0 3px rgba(41, 98, 255, 0.2); }

.bar button { padding:5px 12px; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:12px; font-weight:600; 
             transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
.bar button:hover { filter: brightness(1.15); transform: translateY(-1px); }
.bar button:active { transform: translateY(0); filter: brightness(0.95); }

.tfbtn { padding:4px 10px; background:rgba(255, 255, 255, 0.04); border:1px solid rgba(255, 255, 255, 0.08); color:#8a8d98; border-radius:6px; cursor:pointer; transition: all 0.2s ease; }
.tfbtn:hover { background:rgba(255, 255, 255, 0.08); color:#fff; }
.tfbtn.on { background:linear-gradient(135deg, #2962ff, #1565c0); color:#fff; border-color:transparent; box-shadow: 0 2px 6px rgba(41, 98, 255, 0.3); }

#status { padding:6px 12px; font-size:11px; color:#90a4ae; background:rgba(20, 24, 35, 0.4); border-bottom:1px solid rgba(255, 255, 255, 0.05); }
.modeReal { color:#ff5252; font-weight:bold; } .modeMock { color:#00e676; font-weight:bold; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { background:rgba(20, 24, 35, 0.85); color:#90a4ae; padding:8px 4px; position:sticky; top:0; border-bottom:1px solid rgba(255, 255, 255, 0.08); font-weight:600; text-transform:uppercase; font-size:10px; letter-spacing:0.5px; }
td { padding:7px 4px; text-align:center; border-bottom:1px solid rgba(255, 255, 255, 0.03); }
tr.row { transition: background 0.15s ease; }
tr.row:hover { background:rgba(255, 255, 255, 0.03); cursor:pointer; }
tr.sel { background:rgba(41, 98, 255, 0.12) !important; border-left: 3px solid #2962ff; }
.up { color:#26a69a; font-weight: 500; } .dn { color:#ef5350; font-weight: 500; }
.tbl-wrap { flex:1; overflow-y:auto; }

.badge { padding:2px 8px; border-radius:50px; font-size:10px; font-weight:700; border: 1px solid transparent; letter-spacing:0.3px; }
.b-buy { background:rgba(38, 166, 154, 0.12); color:#26a69a; border-color:rgba(38, 166, 154, 0.2); }
.b-watch { background:rgba(255, 145, 0, 0.12); color:#ff9100; border-color:rgba(255, 145, 0, 0.2); }
.b-hot { background:rgba(41, 98, 255, 0.12); color:#42a5f5; border-color:rgba(41, 98, 255, 0.2); }
.b-down { background:rgba(239, 83, 80, 0.12); color:#ef5350; border-color:rgba(239, 83, 80, 0.2); }
.b-wait { background:rgba(255, 255, 255, 0.04); color:#90a4ae; border-color:rgba(255, 255, 255, 0.08); }

#chart { flex:1; min-height:200px; }
.tag { font-size:11px; color:#90a4ae; cursor:pointer; display:flex; align-items:center; gap:4px; }
.tag input { cursor:pointer; }
.dlg { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.65); z-index:99; align-items:center; justify-content:center; backdrop-filter: blur(4px); }
.dlgbox { background:#151821; border:1px solid rgba(255, 255, 255, 0.08); border-radius:12px; padding:24px; max-height:88vh; overflow-y:auto; box-shadow:0 12px 40px rgba(0,0,0,0.5); }
.dlgbox h3 { margin-bottom:16px; font-size:15px; font-weight:700; border-bottom:1px solid rgba(255, 255, 255, 0.08); padding-bottom:8px; }
.frow { display:flex; align-items:center; margin:8px 0; font-size:12px; }
.frow label { flex:1; color:#90a4ae; }
.frow input { width:140px; padding:4px 8px; background:rgba(255, 255, 255, 0.05); border:1px solid rgba(255, 255, 255, 0.12); color:#e1e4ea; border-radius:6px; outline:none; transition:all 0.2s ease; }
.frow input:focus { border-color:#2962ff; box-shadow:0 0 0 3px rgba(41, 98, 255, 0.2); }
.frow input[type=checkbox]{ width:auto; cursor:pointer; }
.dlgbtns { margin-top:20px; text-align:right; }
.dlgbtns button { padding:6px 16px; margin-left:8px; border:none; border-radius:6px; cursor:pointer; font-weight:600; font-size:12px; transition: all 0.2s ease; }
.dlgbtns button:hover { filter: brightness(1.15); transform: translateY(-1px); }
.dlgbtns button:active { transform: translateY(0); }
.bsave { background:linear-gradient(135deg, #26a69a, #00897b); color:#fff; box-shadow: 0 2px 6px rgba(38, 166, 154, 0.2); }
.bcancel { background:rgba(255, 255, 255, 0.06); color:#b0bec5; border: 1px solid rgba(255, 255, 255, 0.08); }
.bcancel:hover { background:rgba(255, 255, 255, 0.1); color:#fff; }
.vinput { width:60px; padding:4px; background:rgba(255, 255, 255, 0.05); border:1px solid rgba(255, 255, 255, 0.12); color:#e1e4ea; border-radius:6px; outline:none; text-align:center; }
</style></head><body>''')

    # 좌측
    h.append('<div id="left">')
    h.append('<div class="bar">')
    h.append('<span class="' + ("modeMock" if cfg.is_mock else "modeReal") + '">['
             + cfg.mode + ' / ' + cfg.exchange + ']</span>')
    h.append('<span>조건식</span><select id="condSel"></select>')
    h.append('<button style="background:linear-gradient(135deg, #2962ff, #1565c0); box-shadow: 0 2px 6px rgba(41, 98, 255, 0.3);" onclick="registerCond()">실시간등록</button>')
    h.append('<span>TopN</span><select id="topnSel">')
    for n in (5, 10, 15, 20, 30):
        sel = " selected" if n == app.top_n else ""
        h.append('<option value="' + str(n) + '"' + sel + '>' + str(n) + '</option>')
    h.append('</select>')
    h.append('<button style="background:linear-gradient(135deg, #ff9100, #ff6d00); box-shadow: 0 2px 6px rgba(255, 109, 0, 0.3);" onclick="openDlg()">⚙ 속성</button>')
    h.append('<button style="background:linear-gradient(135deg, #ab47bc, #8e24aa); box-shadow: 0 2px 6px rgba(171, 71, 188, 0.3);" onclick="openStrat()">전략</button>')
    h.append('</div>')
    h.append('<div id="status">대기 중</div>')
    h.append('<div class="tbl-wrap"><table><thead><tr>')
    for col in ("#", "코드", "종목명", "현재가", "등락%", "대금R", "상승R",
                "OBV", "MACD", "이격20", "상태", "점수"):
        h.append('<th>' + col + '</th>')
    h.append('</tr></thead><tbody id="rankBody"></tbody></table></div>')
    h.append('</div>')

    # 우측
    h.append('<div id="right">')
    h.append('<div class="bar">')
    h.append('<input type="text" id="codeInput" value="' + app.default_symbol + '" style="width:90px" placeholder="종목코드">')
    for label, val in TIMEFRAMES:
        on = " on" if val == st.chart["timeframe"] else ""
        h.append('<span class="tfbtn' + on + '" data-tf="' + val + '" onclick="setTF(this)">' + label + '</span>')
    h.append('<span>일자</span><input type="date" id="dateInput">')
    h.append('<span>표시</span><input type="number" id="barsInput" value="' + str(st.chart["visible_bars"]) + '" style="width:60px" min="20" max="600">')
    h.append('<button style="background:linear-gradient(135deg, #2962ff, #1565c0); box-shadow: 0 2px 6px rgba(41, 98, 255, 0.3);" onclick="reloadChart()">적용</button>')
    for key, label in INDICATOR_TOGGLES:
        chk = " checked" if st.chart[key] else ""
        h.append('<label class="tag"><input type="checkbox" id="' + key + '"' + chk + ' onchange="reloadChart()"> ' + label + '</label>')
    h.append('</div>')
    h.append('<div id="chartTitle" class="bar" style="font-weight:bold">종목을 선택하세요</div>')
    h.append('<div id="chart"></div>')

    # 검증 패널
    h.append('''<div style="border-top:1px solid rgba(255, 255, 255, 0.08);padding:8px;background:rgba(20, 24, 35, 0.75);display:flex;gap:12px;flex-wrap:wrap;align-items:center;font-size:12px;">
    <div style="display:flex;align-items:center;gap:4px;cursor:pointer;" onclick="var p=document.getElementById('vPanel');p.style.display=(p.style.display==='none'?'flex':'none');">
      <span style="font-weight:600;color:#2962ff;">▶ 매매신호 검증</span>
    </div>
    <div id="vPanel" style="display:none;width:100%;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 0;">
      <label style="color:#90a4ae;">익절%<input id="vTp" type="number" step="any" value="3.0" class="vinput" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:4px;padding:2px;"></label>
      <label style="color:#90a4ae;">손절%<input id="vSl" type="number" step="any" value="2.0" class="vinput" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:4px;padding:2px;"></label>
      <label style="color:#90a4ae;">최대보유봉<input id="vBars" type="number" value="30" class="vinput" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:4px;padding:2px;"></label>
      <label style="color:#90a4ae;">이격상한<input id="vDisp" type="number" step="any" value="110" class="vinput" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:4px;padding:2px;"></label>
      <label style="display:flex;align-items:center;gap:4px;color:#90a4ae;cursor:pointer;"><input id="vMacd" type="checkbox" checked style="width:auto;cursor:pointer;"> MACD교차</label>
      <button style="padding:5px 12px;background:linear-gradient(135deg, #2962ff, #1565c0);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;box-shadow: 0 2px 6px rgba(41, 98, 255, 0.3);transition: all 0.2s;" onmouseover="this.style.filter='brightness(1.15)';" onmouseout="this.style.filter='none';" onclick="runSignalCheck()">검증실행</button>
    </div>
    <div id="vSummary" style="width:100%;font-size:13px;color:#d1d4dc;font-weight:bold;"></div>
  </div>
  <div style="border-top:1px solid rgba(255, 255, 255, 0.08);flex:1;overflow-y:auto;background:rgba(15, 17, 24, 0.5);min-height:120px;">
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="background:rgba(20, 24, 35, 0.85);color:#90a4ae;position:sticky;top:0;border-bottom:1px solid rgba(255, 255, 255, 0.08);">
          <th style="padding:6px 3px;text-align:center;font-weight:600;">진입시각</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">청산시각</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">진입가</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">청산가</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">보유봉</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">사유</th>
          <th style="padding:6px 3px;text-align:center;font-weight:600;">수익률%</th>
        </tr>
      </thead>
      <tbody id="vTable"></tbody>
    </table>
  </div>''')
    h.append('</div>')

    # 속성 다이얼로그
    h.append('<div id="dlg" class="dlg"><div class="dlgbox" style="width:520px"><h3 style="color:#42a5f5">속성 — 지표/필터 파라미터</h3><div id="dlgfields"></div>')
    h.append('<div class="dlgbtns"><button class="bcancel" onclick="closeDlg()">취소</button><button class="bsave" onclick="saveDlg()">저장</button></div></div></div>')

    # 전략 다이얼로그
    h.append('''<div id="sdlg" class="dlg"><div class="dlgbox" style="width:1000px; display:flex; flex-direction:column;">
<h3 style="color:#ab47bc">전략 작성 / 백테스트 / 모의매매 및 AI 전략 도우미</h3>
<input type="hidden" id="s_id">
<div style="display:flex; gap:24px; flex:1; text-align:left;">
  <!-- Left Panel: Manual & Control -->
  <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:8px;">
    <h4 style="color:#42a5f5; font-size:13px; margin-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:4px;">기본 정보 및 수동 설정</h4>
    <div class="frow" style="margin-bottom:12px;"><label>전략 선택</label><select id="s_list" onchange="selectStrat(this.value)" style="width:200px; padding:4px 8px; background:rgba(255, 255, 255, 0.05); border:1px solid rgba(255, 255, 255, 0.12); color:#fff; border-radius:6px; outline:none;"></select></div>
    <div style="font-size:11px;color:#787b86;margin-bottom:8px">
    변수: close, ma5, ma20, ma60, obv, obv_signal, macd, macd_signal, macd_hist, disp20<br>
    함수: crossover(a,b), crossunder(a,b), abs, min, max</div>
    <div class="frow"><label>전략이름</label><input id="s_name" style="width:200px" value="MyStrategy"></div>
    <div class="frow"><label>대상종목코드</label><input id="s_code" style="width:120px" value="005930"></div>
    <div class="frow"><label>진입식(entry)</label><input id="s_entry" style="width:280px" value="macd > macd_signal and obv > obv_signal"></div>
    <div class="frow"><label>청산식(exit)</label><input id="s_exit" style="width:280px" value="macd < macd_signal"></div>
    <div class="frow"><label>수량</label><input id="s_qty" type="number" value="10" style="width:80px"></div>
    <div class="frow"><label>손절%</label><input id="s_stop" type="number" step="any" value="3" style="width:80px"></div>
    <div class="frow"><label>익절%</label><input id="s_take" type="number" step="any" value="6" style="width:80px"></div>
  </div>
  
  <!-- Vertical Divider -->
  <div style="width:1px; background:rgba(255,255,255,0.08); align-self:stretch;"></div>
  
  <!-- Right Panel: AI Strategy Assistant -->
  <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:8px;">
    <h4 style="color:#26a69a; font-size:13px; margin-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:4px;">AI 전략 작성 및 CRUD</h4>
    <div style="display:flex; flex-direction:column; gap:4px;">
      <label style="color:#90a4ae; font-size:11px;">1. 자연어 요구사항 입력 (예: 골든크로스 시 매수, 데드크로스 시 매도)</label>
      <textarea id="s_nl_input" oninput="updateLLMPrompt()" placeholder="전략 아이디어나 기존 전략 수정/삭제 명령을 입력하세요..." style="width:100%; height:70px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#fff; padding:6px; font-size:11px; outline:none; resize:none;"></textarea>
    </div>
    <div style="display:flex; flex-direction:column; gap:4px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <label style="color:#90a4ae; font-size:11px;">2. 외부 LLM 전송용 생성된 프롬프트</label>
        <button onclick="copyPrompt()" id="s_copy_btn" style="padding:2px 8px; font-size:10px; background:#ab47bc; border:none; border-radius:4px; color:#fff; cursor:pointer;">프롬프트 복사</button>
      </div>
      <textarea id="s_prompt_output" readonly placeholder="자연어를 입력하면 프롬프트가 자동으로 작성됩니다." style="width:100%; height:100px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.08); border-radius:6px; color:#90a4ae; padding:6px; font-size:10px; outline:none; resize:none;"></textarea>
    </div>
    <div style="display:flex; flex-direction:column; gap:4px;">
      <label style="color:#90a4ae; font-size:11px;">3. 외부 LLM 결과 (정형화된 JSON 붙여넣기)</label>
      <textarea id="s_llm_json" placeholder="LLM의 결과 JSON 문자열을 여기에 붙여넣어 주세요..." style="width:100%; height:110px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#fff; padding:6px; font-size:10px; font-family:monospace; outline:none; resize:none;"></textarea>
    </div>
    <div style="text-align:right; margin-top:4px;">
      <button style="width:100%; padding:8px; background:linear-gradient(135deg, #ab47bc, #8e24aa); color:#fff; border:none; border-radius:6px; font-weight:600; cursor:pointer; font-size:12px; box-shadow: 0 2px 6px rgba(171, 71, 188, 0.3);" onclick="verifyAndExecuteLLM()">검증 및 실행 (LLM CRUD)</button>
    </div>
  </div>
</div>
<div id="s_result" style="margin:12px 0 4px 0; font-size:12px; color:#26a69a; min-height:16px;"></div>
<div class="dlgbtns" style="margin-top:12px; border-top:1px solid rgba(255,255,255,0.05); padding-top:12px;">
<button class="bcancel" onclick="closeStrat()">닫기</button>
<button style="background:linear-gradient(135deg, #2962ff, #1565c0);color:#fff;box-shadow: 0 2px 6px rgba(41, 98, 255, 0.3);" onclick="runBacktest()">백테스트</button>
<button class="bsave" onclick="saveStrat()">저장</button>
<button id="s_del_btn" style="background:linear-gradient(135deg, #ef5350, #d32f2f);color:#fff;box-shadow: 0 2px 6px rgba(239, 83, 80, 0.3);display:none;" onclick="deleteStrat()">삭제</button>
<button style="background:linear-gradient(135deg, #00b0ff, #0091ea);color:#fff;box-shadow: 0 2px 6px rgba(0, 176, 255, 0.3);" onclick="applyStratToChart()">차트 적용</button>
<button style="background:linear-gradient(135deg, #26a69a, #00897b);color:#fff;box-shadow: 0 2px 6px rgba(38, 166, 154, 0.3);" onclick="liveOrder('buy')">매수</button>
<button style="background:linear-gradient(135deg, #ef5350, #d32f2f);color:#fff;box-shadow: 0 2px 6px rgba(239, 83, 80, 0.3);" onclick="liveOrder('sell')">매도</button>
</div></div></div>''')

    # 스크립트
    h.append('<script src="https://unpkg.com/lightweight-charts@5.1.0/dist/lightweight-charts.standalone.production.js"></script>')
    h.append('<script src="/static/strategy.js"></script>')
    h.append('<script>')
    h.append('var LWC=LightweightCharts;')
    h.append('var chart=null,cs=null,vs=null,maS=[],obvs=null,obvsigs=null,macds=null,macdsigs=null,macdhs=null,jmaS=null,supertrendS=null,vwmaS=null;')
    h.append('var markerHandle=null;')
    h.append('var curCode=null,curName="",curTF="' + st.chart["timeframe"] + '";')
    h.append('var DEFAULT_PARAMS=' + jdump(DEFAULT_PARAMS) + ';')
    h.append('var paramLabels={obv_signal_period:"OBV Signal 기간",macd_fast:"MACD Fast",macd_slow:"MACD Slow",macd_signal:"MACD Signal",supertrend_period:"Supertrend ATR 기간",supertrend_multiplier:"Supertrend 승수",jma_length:"JMA 길이",jma_phase:"JMA 페이즈",jma_power:"JMA 파워",vwma_length:"VWMA 길이",w_trade_value:"가중치:거래대금",w_change_rate:"가중치:상승률",w_obv:"가중치:OBV",w_macd:"가중치:MACD",w_disparity:"가중치:이격도",disp_ideal:"이격도 이상점",disp_penalty:"이격도 감점",overheat_disp20:"과열 이격도20",bull_strong:"강력매수 강세수",bull_watch:"매수관심 강세수",filter_enabled:"필터 사용",filter_min_price:"최소 현재가",filter_max_price:"최대 현재가(0=무한)",filter_min_chg_rate:"최소 등락률%",filter_max_tv_rank:"거래대금순위 이내(0=무한)",filter_obv_up_only:"OBV 상승만",filter_macd_bull_only:"MACD 정배열만",filter_exclude_overheat:"과열주의 제외",analyze_interval_sec:"분석주기(초)",chart_cache_ttl:"차트캐시(초)",fee_bp:"수수료(bp)",slippage_bp:"슬리피지(bp)"};')
    h.append('var _candles = [];')
    h.append('var activeParams = Object.assign({}, DEFAULT_PARAMS);')
    h.append('var curMAPeriods = [5, 20, 60];')

    h.append('''
function loadConditions(){
  fetch("/api/conditions").then(r=>r.json()).then(d=>{
    var s=document.getElementById("condSel");s.innerHTML="";
    (d.items||[]).forEach(it=>{var o=document.createElement("option");o.value=it[0];o.text=it[0]+" - "+it[1];s.appendChild(o);});
  }).catch(()=>{});
}
function registerCond(){var seq=document.getElementById("condSel").value;if(!seq)return;
  fetch("/api/register?seq="+seq).then(r=>r.json()).then(d=>{document.getElementById("status").innerText="조건식 "+seq+" 등록: "+d.msg;});}
function badge(s){var m={"강력매수후보":"b-buy","매수관심":"b-watch","과열주의":"b-hot","하락추세":"b-down","관망":"b-wait"};
  return '<span class="badge '+(m[s]||"b-wait")+'">'+s+'</span>';}
function fmt(n){return n==null?"-":Number(n).toLocaleString();}

function loadSettings(){
  fetch("/api/settings").then(r=>r.json()).then(d=>{
    if(d.params) Object.assign(activeParams, d.params);
  }).catch(()=>{});
}

function calculateSMA(data, period) {
  var sma = [];
  for (var i = 0; i < data.length; i++) {
    if (i + 1 < period) continue;
    var sum = 0;
    for (var j = 0; j < period; j++) {
      sum += data[i - j].close;
    }
    sma.push({ time: data[i].time, value: sum / period });
  }
  return sma;
}

function calculateEMA(values, period) {
  var ema = [];
  if (values.length === 0) return ema;
  var k = 2 / (period + 1);
  ema.push({ time: values[0].time, value: values[0].value });
  for (var i = 1; i < values.length; i++) {
    var val = values[i].value * k + ema[i - 1].value * (1 - k);
    ema.push({ time: values[i].time, value: val });
  }
  return ema;
}

function calculateOBV(data) {
  var obv = [];
  if (data.length === 0) return obv;
  obv.push({ time: data[0].time, value: data[0].volume });
  for (var i = 1; i < data.length; i++) {
    var prevVal = obv[i - 1].value;
    var val = prevVal;
    if (data[i].close > data[i - 1].close) {
      val = prevVal + data[i].volume;
    } else if (data[i].close < data[i - 1].close) {
      val = prevVal - data[i].volume;
    }
    obv.push({ time: data[i].time, value: val });
  }
  return obv;
}

function calculateMACD(data, fast, slow, signal) {
  var closes = data.map(d => ({ time: d.time, value: d.close }));
  var emaFast = calculateEMA(closes, fast);
  var emaSlow = calculateEMA(closes, slow);
  
  var macdLine = [];
  for (var i = 0; i < closes.length; i++) {
    macdLine.push({ time: closes[i].time, value: emaFast[i].value - emaSlow[i].value });
  }
  
  var signalLine = calculateEMA(macdLine, signal);
  
  var hist = [];
  for (var i = 0; i < macdLine.length; i++) {
    hist.push({
      time: macdLine[i].time,
      value: macdLine[i].value - signalLine[i].value,
      color: (macdLine[i].value >= signalLine[i].value ? "rgba(38,166,154,0.6)" : "rgba(239,83,80,0.6)")
    });
  }
  
  return { macd: macdLine, signal: signalLine, hist: hist };
}


function calculateJMA(candles, length, phase, power) {
  var jmaData = [];
  if (candles.length === 0) return jmaData;

  var e0 = new Array(candles.length).fill(0);
  var e1 = new Array(candles.length).fill(0);
  var e2 = new Array(candles.length).fill(0);
  var jma = new Array(candles.length).fill(0);
  var trend = new Array(candles.length).fill(0);
  var priceSum = new Array(candles.length).fill(0);

  var clampedPhase = Math.max(-100, Math.min(100, phase)) / 100 + 1.5;
  var beta = 0.45 * (length - 1) / (0.45 * (length - 1) + 2);
  var alpha = Math.pow(beta, Math.max(1, power));

  for (var i = 0; i < candles.length; i++) {
    var close = candles[i].close;
    var time = candles[i].time;

    if (i === 0) {
      e0[i] = close;
      e1[i] = 0;
      e2[i] = 0;
      jma[i] = close;
      trend[i] = 0;
      priceSum[i] = close;
    } else {
      var previousE0 = e0[i - 1];
      var previousE1 = e1[i - 1];
      var previousE2 = e2[i - 1];
      var previousJma = jma[i - 1];
      var previousTrend = trend[i - 1];
      var e0_val = (1 - alpha) * close + alpha * previousE0;
      var e1_val = (close - e0_val) * (1 - beta) + beta * previousE1;
      var phaseAdjusted = e0_val + clampedPhase * e1_val;
      var e2_val = (phaseAdjusted - previousJma) * (1 - alpha) * (1 - alpha) + alpha * alpha * previousE2;
      e0[i] = e0_val;
      e1[i] = e1_val;
      e2[i] = e2_val;
      priceSum[i] = priceSum[i - 1] + close;
      jma[i] = i < length
        ? priceSum[i] / (i + 1)
        : Math.round((previousJma + e2_val) * 10) / 10;
      trend[i] = jma[i] > previousJma
        ? 1
        : jma[i] < previousJma
          ? -1
          : previousTrend;
    }

    if (i === 0) {
      jma[i] = close;
    }

    jmaData.push({
      time: time,
      value: jma[i],
      color: trend[i] >= 0 ? '#00e676' : '#ff6d00'
    });
  }

  return jmaData;
}

function calculateSupertrend(candles, period, multiplier) {
  var supertrendData = [];
  if (candles.length === 0) return supertrendData;

  var atr = new Array(candles.length).fill(NaN);
  var finalUpper = new Array(candles.length).fill(NaN);
  var finalLower = new Array(candles.length).fill(NaN);
  var supertrend = new Array(candles.length).fill(NaN);
  var trendUp = new Array(candles.length).fill(false);

  var tr = new Array(candles.length).fill(0);
  for (var i = 0; i < candles.length; i++) {
    var highLow = candles[i].high - candles[i].low;
    if (i > 0) {
      var highClose = Math.abs(candles[i].high - candles[i - 1].close);
      var lowClose = Math.abs(candles[i].low - candles[i - 1].close);
      tr[i] = Math.max(highLow, highClose, lowClose);
    } else {
      tr[i] = highLow;
    }
  }

  var initializedIndex = -1;
  for (var i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      continue;
    }

    if (i === period - 1) {
      var sum = 0;
      for (var cursor = 0; cursor <= i; cursor++) {
        sum += tr[cursor];
      }
      atr[i] = sum / period;
    } else {
      atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period;
    }

    var atrValue = atr[i];
    var hl2 = (candles[i].high + candles[i].low) / 2;
    var basicUpper = hl2 + multiplier * atrValue;
    var basicLower = hl2 - multiplier * atrValue;

    if (initializedIndex === -1 || i === period - 1) {
      finalUpper[i] = basicUpper;
      finalLower[i] = basicLower;
      supertrend[i] = basicLower;
      trendUp[i] = true;
      initializedIndex = i;
    } else {
      var previousIndex = i - 1;
      finalUpper[i] = (basicUpper < finalUpper[previousIndex] || candles[previousIndex].close > finalUpper[previousIndex])
        ? basicUpper
        : finalUpper[previousIndex];

      finalLower[i] = (basicLower > finalLower[previousIndex] || candles[previousIndex].close < finalLower[previousIndex])
        ? basicLower
        : finalLower[previousIndex];

      if (supertrend[previousIndex] === finalUpper[previousIndex]) {
        supertrend[i] = candles[i].close <= finalUpper[i] ? finalUpper[i] : finalLower[i];
      } else {
        supertrend[i] = candles[i].close >= finalLower[i] ? finalLower[i] : finalUpper[i];
      }

      trendUp[i] = supertrend[i] === finalLower[i];
      initializedIndex = Math.max(initializedIndex, i);
    }

    supertrendData.push({
      time: candles[i].time,
      value: supertrend[i],
      color: trendUp[i] ? '#66d28a' : '#ff7a5c'
    });
  }

  return supertrendData;
}

function calculateVWMA(candles, length) {
  var vwmaData = [];
  if (candles.length === 0) return vwmaData;

  for (var i = 0; i < candles.length; i++) {
    if (i < length - 1) {
      continue;
    }
    var sumWeighted = 0;
    var sumVol = 0;
    for (var j = 0; j < length; j++) {
      var bar = candles[i - j];
      sumWeighted += bar.close * bar.volume;
      sumVol += bar.volume;
    }
    if (sumVol !== 0) {
      vwmaData.push({
        time: candles[i].time,
        value: sumWeighted / sumVol
      });
    }
  }
  return vwmaData;
}

function recalculateIndicators() {
  if (!chart || _candles.length === 0) return;
  var showMA = document.getElementById("show_ma").checked;
  var showOBV = document.getElementById("show_obv").checked;
  var showMACD = document.getElementById("show_macd").checked;
  var showSupertrend = document.getElementById("show_supertrend") && document.getElementById("show_supertrend").checked;
  var showJMA = document.getElementById("show_jma") && document.getElementById("show_jma").checked;
  var showVWMA = document.getElementById("show_vwma") && document.getElementById("show_vwma").checked;
  
  if (showMA && maS.length > 0) {
    curMAPeriods.forEach((p, idx) => {
      var sma = calculateSMA(_candles, p);
      if (maS[idx]) maS[idx].setData(sma);
    });
  }
  if (showOBV && obvs && obvsigs) {
    var obv = calculateOBV(_candles);
    var obvSig = calculateEMA(obv, activeParams.obv_signal_period || 9);
    obvs.setData(obv);
    obvsigs.setData(obvSig);
  }
  if (showMACD && macds && macdsigs && macdhs) {
    var macd = calculateMACD(_candles, activeParams.macd_fast || 12, activeParams.macd_slow || 26, activeParams.macd_signal || 9);
    macds.setData(macd.macd);
    macdsigs.setData(macd.signal);
    macdhs.setData(macd.hist);
  }
  if (showSupertrend && supertrendS) {
    var supertrend = calculateSupertrend(_candles, activeParams.supertrend_period || 10, activeParams.supertrend_multiplier || 3.0);
    supertrendS.setData(supertrend);
  }
  if (showJMA && jmaS) {
    var jma = calculateJMA(_candles, activeParams.jma_length || 7, activeParams.jma_phase || 50, activeParams.jma_power || 2);
    jmaS.setData(jma);
  }
  if (showVWMA && vwmaS) {
    var vwma = calculateVWMA(_candles, activeParams.vwma_length || 20);
    vwmaS.setData(vwma);
  }
}
function loadRanking(){
  var n=document.getElementById("topnSel").value;
  fetch("/api/ranking?n="+n).then(r=>r.json()).then(d=>{
    document.getElementById("status").innerText="["+d.mode+"] 포착 "+d.captured+" / 분석 "+d.analyzed+" / 필터통과 "+d.filtered+" / 갱신 "+d.updated;
    var tb=document.getElementById("rankBody");tb.innerHTML="";
    d.rows.forEach((m,i)=>{
      var tr=document.createElement("tr");tr.className="row"+(m.code===curCode?" sel":"");
      tr.onclick=function(){document.getElementById("codeInput").value=m.code;loadChart(m.code,m.name);};
      tr.innerHTML='<td>'+(i+1)+'</td><td>'+m.code+'</td><td style="text-align:left">'+m.name+'</td><td>'+fmt(m.price)+'</td>'+
        '<td class="'+(m.chg_rate>=0?"up":"dn")+'">'+m.chg_rate.toFixed(2)+'</td>'+
        '<td>'+(m.rank_tv<9999?m.rank_tv:"-")+'</td><td>'+(m.rank_cr<9999?m.rank_cr:"-")+'</td>'+
        '<td class="'+(m.obv_trend=="상승"?"up":"dn")+'">'+m.obv_trend+'</td><td>'+m.macd_array+'</td>'+
        '<td>'+(m.disp20?m.disp20.toFixed(1):"-")+'</td><td>'+badge(m.state)+'</td><td><b>'+m.score+'</b></td>';
      tb.appendChild(tr);
    });
  }).catch(e=>{document.getElementById("status").innerText="오류: "+e;});
}
function openDlg(){
  fetch("/api/settings").then(r=>r.json()).then(d=>{
    var p=d.params,c=document.getElementById("dlgfields");c.innerHTML="";
    Object.keys(DEFAULT_PARAMS).forEach(k=>{if(k==="ma_periods")return;
      var v=p[k],lab=paramLabels[k]||k,row=document.createElement("div");row.className="frow";
      if(typeof DEFAULT_PARAMS[k]==="boolean"){row.innerHTML='<label>'+lab+'</label><input type="checkbox" id="f_'+k+'"'+(v?' checked':'')+'>';}
      else{row.innerHTML='<label>'+lab+'</label><input type="number" step="any" id="f_'+k+'" value="'+v+'">';}
      c.appendChild(row);});
    document.getElementById("dlg").style.display="flex";});
}
function closeDlg(){document.getElementById("dlg").style.display="none";}
function saveDlg(){var out={};
  Object.keys(DEFAULT_PARAMS).forEach(k=>{if(k==="ma_periods")return;var el=document.getElementById("f_"+k);if(!el)return;
    out[k]=(typeof DEFAULT_PARAMS[k]==="boolean")?el.checked:parseFloat(el.value);});
  fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(out)}).then(r=>r.json()).then(()=>{closeDlg();loadRanking();if(curCode)reloadChart();});}
function setTF(el){document.querySelectorAll(".tfbtn").forEach(b=>b.classList.remove("on"));el.classList.add("on");curTF=el.getAttribute("data-tf");reloadChart();}
function reloadChart(){var code=document.getElementById("codeInput").value.trim();if(code)loadChart(code,curName||code);}
// 전략
function openStrat(){document.getElementById("s_code").value=document.getElementById("codeInput").value||"005930";document.getElementById("sdlg").style.display="flex";loadStrats();}
function closeStrat(){document.getElementById("sdlg").style.display="none";}
function stratObj(){return {name:document.getElementById("s_name").value,code:document.getElementById("s_code").value.trim(),
  entry_expr:document.getElementById("s_entry").value,exit_expr:document.getElementById("s_exit").value,
  qty:parseInt(document.getElementById("s_qty").value)||0,stop_pct:parseFloat(document.getElementById("s_stop").value)||0,
  take_pct:parseFloat(document.getElementById("s_take").value)||0};}
function runBacktest(){var s=stratObj();
  fetch("/api/backtest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:s.code,tf:curTF,strategy:s})}).then(r=>r.json()).then(d=>{
    var el=document.getElementById("s_result");
    if(d.error){el.style.color="#ef5350";el.innerText="오류: "+d.error;return;}
    el.style.color=d.total_return>=0?"#26a69a":"#ef5350";
    el.innerText="매매 "+d.n_trades+"회 / 승률 "+d.win_rate+"% / 누적수익률 "+d.total_return+"%";
    if(cs&&d.markers&&d.markers.length){
      try{if(markerHandle){markerHandle.setMarkers(d.markers);}else{markerHandle=LWC.createSeriesMarkers(cs,d.markers);}}catch(e){console.warn('marker',e);}
    }});}
function liveOrder(side){var s=stratObj();
  if(!confirm("["+(side=="buy"?"매수":"매도")+"] "+s.code+" "+s.qty+"주 시장가 주문 실행. 계속?"))return;
  fetch("/api/order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:s.code,side:side,qty:s.qty})}).then(r=>r.json()).then(d=>{
    var el=document.getElementById("s_result");el.style.color=d.error?"#ef5350":"#26a69a";el.innerText="주문결과: "+JSON.stringify(d);});}

// 매매신호 검증
function fmtT(t){
  if(typeof t!=="number"){return String(t);}
  var d=new Date(t*1000);return d.toISOString().substr(11,5);
}
function runSignalCheck(){
  if(!curCode){var se=document.getElementById('vSummary');se.style.color='#ef5350';se.textContent='⚠ 먼저 종목을 선택하세요';return;}
  var rule={
    entry:"score_cross",
    tp_pct:parseFloat(document.getElementById('vTp').value),
    sl_pct:parseFloat(document.getElementById('vSl').value),
    max_bars:parseInt(document.getElementById('vBars').value),
    disp_max:parseFloat(document.getElementById('vDisp').value),
    macd_cross:document.getElementById('vMacd').checked
  };
  fetch('/api/signal_check',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:curCode,tf:curTF,rule:rule})
  }).then(r=>r.json()).then(data=>{
    if(cs){
      try{
        if(markerHandle){markerHandle.setMarkers(data.markers||[]);}
        else{markerHandle=LWC.createSeriesMarkers(cs,data.markers||[]);}
      }catch(e){console.warn('marker',e);}
    }
    var s=data.summary||{};
    var sumEl=document.getElementById('vSummary');
    if(s.error){sumEl.textContent='⚠ '+s.error;sumEl.style.color='#ef5350';}
    else{
      sumEl.textContent='거래 '+s.trades+'회 · 승률 '+s.win_rate+'% · 평균 '+s.avg_ret+'% · 누적 '+s.sum_ret+'%';
      sumEl.style.color=(s.sum_ret>0)?'#26a69a':'#ef5350';
    }
    var tb=document.getElementById('vTable');tb.innerHTML='';
    (data.trades||[]).forEach(t=>{
      var tr=document.createElement('tr');
      var col=t.ret_pct>0?'#26a69a':'#ef5350';tr.style.color=col;
      tr.innerHTML='<td style="text-align:center">'+fmtT(t.entry_time)+'</td><td style="text-align:center">'+fmtT(t.exit_time)+'</td>'+
        '<td style="text-align:right">'+Number(t.entry_px).toLocaleString()+'</td><td style="text-align:right">'+Number(t.exit_px).toLocaleString()+'</td>'+
        '<td style="text-align:center">'+t.bars+'</td><td style="text-align:center">'+t.reason+'</td>'+
        '<td style="text-align:right">'+(t.ret_pct>=0?'+':'')+t.ret_pct+'%</td>';
      tb.appendChild(tr);
    });
  }).catch(e=>{var se=document.getElementById('vSummary');se.style.color='#ef5350';se.textContent='오류: '+e;});
}
''')

    h.append('''
function ensureChart(){
  if(chart)return;var el=document.getElementById("chart");
  chart=LWC.createChart(el,{width:el.clientWidth,height:el.clientHeight,
    layout:{background:{type:"solid",color:"#131722"},textColor:"#d1d4dc",attributionLogo:false,panes:{separatorColor:"#2a2e39"}},
    grid:{vertLines:{color:"#1e222d"},horzLines:{color:"#1e222d"}},crosshair:{mode:0},
    rightPriceScale:{borderColor:"#2a2e39"},timeScale:{borderColor:"#2a2e39",timeVisible:true,secondsVisible:false}});
  cs=chart.addSeries(LWC.CandlestickSeries,{upColor:"#26a69a",downColor:"#ef5350",borderUpColor:"#26a69a",borderDownColor:"#ef5350",wickUpColor:"#26a69a",wickDownColor:"#ef5350"});
  vs=chart.addSeries(LWC.HistogramSeries,{priceFormat:{type:"volume"},priceScaleId:"vol",lastValueVisible:false,priceLineVisible:false});
  chart.priceScale("vol").applyOptions({scaleMargins:{top:0.85,bottom:0}});
  window.addEventListener("resize",function(){chart.applyOptions({width:el.clientWidth,height:el.clientHeight});});
}
var maColors=["#ffeb3b","#42a5f5","#ab47bc","#26c6da","#ff7043"];
function clearExtraSeries(){maS.forEach(s=>chart.removeSeries(s));maS=[];
  [obvs,obvsigs,macds,macdsigs,macdhs,jmaS,supertrendS,vwmaS].forEach(s=>{if(s)chart.removeSeries(s);});obvs=obvsigs=macds=macdsigs=macdhs=jmaS=supertrendS=vwmaS=null;}
// 전체 로드: 종목 선택/TF·표시개수 변경 시에만 호출 (setData 전체 재그리기)
function loadChart(code,name){
  var bars=parseInt(document.getElementById("barsInput").value)||120;
  var showMA=document.getElementById("show_ma").checked,showOBV=document.getElementById("show_obv").checked,showMACD=document.getElementById("show_macd").checked;
  var showSupertrend=document.getElementById("show_supertrend") && document.getElementById("show_supertrend").checked;
  var showJMA=document.getElementById("show_jma") && document.getElementById("show_jma").checked;
  var showVWMA=document.getElementById("show_vwma") && document.getElementById("show_vwma").checked;
  curCode=code;curName=name||code;
  document.getElementById("chartTitle").innerText=code+" "+curName+"  ["+curTF+"]";
  fetch("/api/chart?code="+code+"&tf="+curTF+"&bars="+bars).then(r=>r.json()).then(d=>{
    if(d.error){document.getElementById("chartTitle").innerText=code+" — 오류: "+d.error;return;}
    ensureChart();
    var isIntraday = curTF.charAt(0) === 'm' || curTF.charAt(0) === 't';
    chart.timeScale().applyOptions({ timeVisible: isIntraday });
    var prevRange=null,sameView=(code===loadChart._lastCode&&curTF===loadChart._lastTF);
    if(sameView){try{prevRange=chart.timeScale().getVisibleLogicalRange();}catch(e){}}
    // 종목/TF 바뀌면 마커 초기화 (안전 try)
    if(!sameView&&markerHandle){try{markerHandle.setMarkers([]);}catch(e){}}
    clearExtraSeries();
    _candles = d.candles || [];
    curMAPeriods = (d.ma || []).map(m => m.period);
    cs.setData(d.candles);vs.setData(d.volumes);
    if(showMA){d.ma.forEach((m,i)=>{var s=chart.addSeries(LWC.LineSeries,{color:maColors[i%maColors.length],lineWidth:1,lastValueVisible:false,priceLineVisible:false,title:"MA"+m.period});s.setData(m.data);maS.push(s);});}
    if(showSupertrend && d.supertrend){
      supertrendS = chart.addSeries(LWC.LineSeries,{lineWidth:2,lastValueVisible:false,priceLineVisible:false,title:"Supertrend"});
      supertrendS.setData(d.supertrend);
    }
    if(showJMA && d.jma){
      jmaS = chart.addSeries(LWC.LineSeries,{lineWidth:2,lastValueVisible:false,priceLineVisible:false,title:"JMA"});
      jmaS.setData(d.jma);
    }
    if(showVWMA && d.vwma){
      vwmaS = chart.addSeries(LWC.LineSeries,{color:"#82d3ff",lineWidth:2,lastValueVisible:false,priceLineVisible:false,title:"VWMA"});
      vwmaS.setData(d.vwma);
    }
    var pane=1;
    if(showOBV){obvs=chart.addSeries(LWC.LineSeries,{color:"#26c6da",lineWidth:2,lastValueVisible:false,priceLineVisible:false,title:"OBV"},pane);
      obvsigs=chart.addSeries(LWC.LineSeries,{color:"#ff7043",lineWidth:1,lastValueVisible:false,priceLineVisible:false,title:"Signal"},pane);
      obvs.setData(d.obv);obvsigs.setData(d.obv_signal);pane++;}
    if(showMACD){macdhs=chart.addSeries(LWC.HistogramSeries,{lastValueVisible:false,priceLineVisible:false,title:"Hist"},pane);
      macds=chart.addSeries(LWC.LineSeries,{color:"#42a5f5",lineWidth:1,lastValueVisible:false,priceLineVisible:false,title:"MACD"},pane);
      macdsigs=chart.addSeries(LWC.LineSeries,{color:"#ff7043",lineWidth:1,lastValueVisible:false,priceLineVisible:false,title:"Sig"},pane);
      macdhs.setData(d.macd_hist);macds.setData(d.macd);macdsigs.setData(d.macd_signal);pane++;}
    var panes=chart.panes();for(var i=1;i<panes.length;i++)panes[i].setHeight(110);
    if(prevRange){chart.timeScale().setVisibleLogicalRange(prevRange);}
    else{var total=d.candles.length;chart.timeScale().setVisibleLogicalRange({from:Math.max(0,total-bars),to:total});}

    // 실시간 시작 기준점: 현재 차트의 마지막 봉 시각
    if(d.candles&&d.candles.length){
      var lt=d.candles[d.candles.length-1].time;
      _lastBarTime=(typeof lt==='number')?lt:0;
    }
    loadChart._lastCode=code;loadChart._lastTF=curTF;
    // ★ 실시간 시세 구독 (종목/TF 기준)
    fetch("/api/subscribe?code="+code+"&tf="+curTF).catch(()=>{});
  });
}

// SSE: 진행봉 수신 → 마지막 봉만 update (전체 재그리기 없음)
var _es=null;
var _lastBarTime=0;   // 마지막으로 차트에 반영한 봉 시각
function startStream(){
  if(_es)return;
  _es=new EventSource("/api/stream");
  _es.onmessage=function(ev){
    if(!chart||!cs)return;
    // 분봉(m) 및 틱봉(t) 실시간 틱 누적 적용. 일주월은 시간축이 달라 스킵
    if(curTF.charAt(0)!=='m' && curTF.charAt(0)!=='t')return;
    try{
      var d=JSON.parse(ev.data);
      if(!d||!d.bar)return;
      if(String(d.code)!==String(curCode))return;
      var b=d.bar;
      if(typeof b.time!=='number')return;
      // 시간 역행 방지: 마지막 봉보다 과거면 무시 (Value is null 에러 예방)
      if(b.time < _lastBarTime)return;
      _lastBarTime=b.time;
      
      // Update local _candles array
      if (_candles.length > 0 && _candles[_candles.length - 1].time === b.time) {
        _candles[_candles.length - 1] = b;
      } else {
        _candles.push(b);
      }
      
      cs.update({time:b.time,open:b.open,high:b.high,low:b.low,close:b.close});
      if(vs)vs.update({time:b.time,value:b.volume,
        color:(b.close>=b.open?"rgba(38,166,154,0.5)":"rgba(239,83,80,0.5)")});
      
      // Recalculate indicators for MA, OBV, MACD in real-time
      recalculateIndicators();
    }catch(e){/* 조용히 무시 */}
  };
  _es.onerror=function(){};
}

             
// 증분 업데이트: 같은 종목/TF일 때 마지막 1~2봉만 series.update (전체 재그리기 없음 → UI 멈춤 방지)
var _updating=false;
function updateChart(){
  if(!curCode||!chart||!cs||_updating)return;
  _updating=true;
  var bars=parseInt(document.getElementById("barsInput").value)||120;
  fetch("/api/chart?code="+curCode+"&tf="+curTF+"&bars="+bars).then(r=>r.json()).then(d=>{
    _updating=false;
    if(d.error||!d.candles||!d.candles.length)return;
    // 그새 종목/TF가 바뀌었으면 증분 스킵 (loadChart가 처리)
    if(curCode!==loadChart._lastCode||curTF!==loadChart._lastTF)return;
    var last=d.candles.length-1, start=Math.max(0,last-1);
    for(var i=start;i<=last;i++){
      try{
        cs.update(d.candles[i]);
        if(vs&&d.volumes[i])vs.update(d.volumes[i]);
        var tm=d.candles[i].time;
        maS.forEach((s,mi)=>{var arr=d.ma[mi]&&d.ma[mi].data;if(arr){var pt=arr[arr.length-1];if(pt&&pt.time===tm)s.update(pt);}});
        if(obvs&&d.obv[i])obvs.update(d.obv[i]);
        if(obvsigs&&d.obv_signal[i])obvsigs.update(d.obv_signal[i]);
        if(macds&&d.macd[i])macds.update(d.macd[i]);
        if(macdsigs&&d.macd_signal[i])macdsigs.update(d.macd_signal[i]);
        if(macdhs&&d.macd_hist[i])macdhs.update(d.macd_hist[i]);
        if(jmaS&&d.jma[i])jmaS.update(d.jma[i]);
        if(supertrendS&&d.supertrend[i])supertrendS.update(d.supertrend[i]);
        if(vwmaS&&d.vwma[i])vwmaS.update(d.vwma[i]);
      }catch(e){}
    }
    // 줌/스크롤 범위는 손대지 않음 → 사용자 시야 유지
  }).catch(()=>{_updating=false;});
}
''')

    h.append('loadSettings();')
    h.append('loadConditions();loadRanking();')
    h.append('loadChart("' + app.default_symbol + '","' + app.default_symbol + '");')
    h.append('setInterval(loadRanking,3000);')
    h.append('startStream();')   # ★ SSE 실시간 스트림 시작 (updateChart 폴링 제거)
    h.append('</script></body></html>')
    return '\n'.join(h)


# ═══════════════════════════════════════════════════════════════
#  HTTP 핸들러
# ═══════════════════════════════════════════════════════════════
def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json; charset=utf-8", code=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            path = u.path
            try:
                if path in ("/", "/index.html"):
                    self._send(build_html(app), "text/html; charset=utf-8")
                elif path == "/static/strategy.js":
                    import os
                    filepath = os.path.join(os.path.dirname(__file__), "static", "strategy.js")
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        self._send(content, "application/javascript; charset=utf-8")
                    except Exception as e:
                        self._send(str(e), "text/plain", 404)
                elif path == "/api/conditions":
                    self._send(jdump({"items": app.cond_items}))
                elif path == "/api/register":
                    seq = q.get("seq", [""])[0]
                    if app.ws and seq:
                        app.ws.register(seq)
                        self._send(jdump({"msg": "OK"}))
                    else:
                        self._send(jdump({"msg": "실패"}))
                elif path == "/api/ranking":
                    n = int(q.get("n", [str(app.top_n)])[0])
                    self._send(jdump(app.ranking_payload(n)))
                elif path == "/api/chart":
                    code = q.get("code", [""])[0]
                    tf = q.get("tf", ["D"])[0]
                    bars = int(q.get("bars", ["120"])[0])
                    max_bars = max(bars, app.settings.chart["max_bars"])
                    self._send(jdump(app.chart_data(code, tf, max_bars)))
                elif path == "/api/settings":
                    self._send(jdump(app.settings.to_dict()))
                elif path == "/api/strategies":
                    self._send(jdump({"items": app.store.list()}))
                
                elif path == "/api/subscribe":
                    code = q.get("code", [""])[0]
                    tf = q.get("tf", ["m1"])[0]
                    app.subscribe_rt(code, tf)
                    self._send(jdump({"msg": "OK", "code": code, "tf": tf}))

                elif path == "/api/stream":
                    # SSE: 진행봉을 push
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    last_seq = -1
                    try:
                        while True:
                            payload = app.latest_bar_payload()
                            if payload["seq"] != last_seq and payload["data"]:
                                last_seq = payload["seq"]
                                line = "data: " + jdump(payload["data"]) + "\n\n"
                                self.wfile.write(line.encode("utf-8"))
                                self.wfile.flush()
                            else:
                                time.sleep(0.2)
                    except Exception:
                        return
                
                
                else:
                    self._send("404", "text/plain", 404)
            except Exception as e:
                self._send(jdump({"error": str(e)}), code=500)

        def do_POST(self):
            u = urlparse(self.path)
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
            import json as _j
            try:
                data = _j.loads(raw) if raw else {}
            except Exception:
                data = {}
            try:
                if u.path == "/api/settings":
                    app.settings.update_params(data)
                    self._send(jdump({"msg": "OK", "params": app.settings.params}))
                elif u.path == "/api/strategies":
                    self._send(jdump(app.store.upsert(data)))
                elif u.path == "/api/strategies/delete":
                    sid = data.get("id")
                    if sid:
                        app.store.delete(sid)
                        self._send(jdump({"msg": "OK"}))
                    else:
                        self._send(jdump({"error": "ID가 없습니다"}), code=400)
                elif u.path == "/api/strategies/validate":
                    entry_expr = data.get("entry_expr", "")
                    exit_expr = data.get("exit_expr", "")
                    err_entry = validate_expression(entry_expr)
                    err_exit = validate_expression(exit_expr)
                    if err_entry:
                        self._send(jdump({"valid": False, "error": f"진입식 검증 실패: {err_entry}"}))
                    elif err_exit:
                        self._send(jdump({"valid": False, "error": f"청산식 검증 실패: {err_exit}"}))
                    else:
                        self._send(jdump({"valid": True}))
                elif u.path == "/api/backtest":
                    r = app.strategy_engine.backtest(
                        data.get("code", ""), data.get("tf", "D"), data.get("strategy"))
                    self._send(jdump(r))
                elif u.path == "/api/order":
                    qty = int(data.get("qty", 0))
                    if qty <= 0:
                        self._send(jdump({"error": "수량이 0입니다"}))
                        return
                    r = app.strategy_engine.execute_live(
                        data.get("code", ""), data.get("side", ""), qty)
                    self._send(jdump(r))
                elif u.path == "/api/signal_check":
                    code = data.get("code", app.default_symbol)
                    tf = data.get("tf", "D")
                    rule = data.get("rule", {})
                    try:
                        candles = app.engine.chart_candles(code, tf)
                        if not candles:
                            self._send(jdump({"markers": [], "trades": [],
                                              "summary": {"error": "차트 데이터 없음"}}))
                            return
                        result = app.strategy_engine.signal_backtest(
                            candles, rule,
                            fee_bp=app.settings.params.get("fee_bp", 15),
                            slippage_bp=app.settings.params.get("slippage_bp", 5))
                        self._send(jdump(result))
                    except Exception as e:
                        self._send(jdump({"markers": [], "trades": [],
                                          "summary": {"error": str(e)}}))
                else:
                    self._send("404", "text/plain", 404)
            except Exception as e:
                self._send(jdump({"error": str(e)}), code=500)
    return Handler


# ═══════════════════════════════════════════════════════════════
#  엔트리포인트
# ═══════════════════════════════════════════════════════════════
def main():
    cfg = Config()
    settings = Settings()
    app = WebApp(cfg, settings, top_n=10)
    app.start()
    server = ThreadingHTTPServer(("localhost", cfg.port), make_handler(app))
    url = "http://localhost:" + str(cfg.port)
    print("=" * 60)
    print("  실행 모드:", cfg.mode, "(KIWOOM_MOCK=" + str(cfg.is_mock) + ")")
    print("  REST:", cfg.rest_host)
    print("  WS  :", cfg.ws_host)
    print("  대시보드:", url, "| 거래소:", cfg.exchange, "| 기본종목:", cfg.default_symbol)
    print("=" * 60)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료 중...")
        app.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
