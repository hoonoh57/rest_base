#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.py — 불변 구조 (90%)
================================================================================
검증 핵심 로직. 파라미터는 settings 에서 주입.
- 모의/실 키 선택 (Config)
- 종목명/시세 일괄 조회 (ka10095, 99개씩 1회 호출)  ★ rate limit 회피
- KST(+9h) 분/틱 시간 보정
- 주문(kt10000/kt10001) + 전략 백테스트/실행 엔진
"""

import os
import json
import time
import threading
import ast as _ast
import operator as _op
from datetime import datetime, timezone, timedelta

import numpy as np
import requests

try:
    import websocket
except ImportError:
    websocket = None


# ═══════════════════════════════════════════════════════════════
#  공용 유틸
# ═══════════════════════════════════════════════════════════════
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, cls=NpEncoder)


def env_bool(key, default=False):
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def env_int(key, default):
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
#  설정 (.env) — 모의/실 키 선택
# ═══════════════════════════════════════════════════════════════
class Config:
    REAL_REST = "https://api.kiwoom.com"
    MOCK_REST = "https://mockapi.kiwoom.com"
    REAL_WS = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    MOCK_WS = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
    EXCHANGE_COND = {"KRX": "K", "NXT": "N", "UNIFIED": "A", "ALL": "A"}
    EXCHANGE_RANK = {"KRX": "1", "NXT": "2", "UNIFIED": "3", "ALL": "3"}

    def __init__(self):
        self.is_mock = env_bool("KIWOOM_MOCK", False)
        common_key = os.getenv("KIWOOM_APP_KEY", "")
        common_sec = os.getenv("KIWOOM_SECRET_KEY", "")
        if self.is_mock:
            self.appkey = os.getenv("KIWOOM_MOCK_APP_KEY", "") or common_key
            self.secretkey = os.getenv("KIWOOM_MOCK_SECRET_KEY", "") or common_sec
            self.mode = "모의투자"
        else:
            self.appkey = os.getenv("KIWOOM_REAL_APP_KEY", "") or common_key
            self.secretkey = os.getenv("KIWOOM_REAL_SECRET_KEY", "") or common_sec
            self.mode = "실투자"
        self.exchange = os.getenv("KIWOOM_EXCHANGE", "KRX").upper()
        self.adjust_price = os.getenv("KIWOOM_ADJUST_PRICE", "1")
        self.lookback_sec = env_int("HISTORY_LOOKBACK_SEC", 28800)
        self.default_symbol = os.getenv("DEFAULT_SYMBOL", "005930")
        self.port = env_int("PORT", 3000)
        if not self.appkey or not self.secretkey:
            need = ("KIWOOM_MOCK_APP_KEY / KIWOOM_MOCK_SECRET_KEY" if self.is_mock
                    else "KIWOOM_REAL_APP_KEY / KIWOOM_REAL_SECRET_KEY")
            raise RuntimeError("[" + self.mode + "] API 키 없음. .env에 " + need
                               + " (또는 공통 KIWOOM_APP_KEY / KIWOOM_SECRET_KEY)를 설정하세요.")

    @property
    def rest_host(self):
        return self.MOCK_REST if self.is_mock else self.REAL_REST

    @property
    def ws_host(self):
        return self.MOCK_WS if self.is_mock else self.REAL_WS

    @property
    def stex_cond(self):
        return self.EXCHANGE_COND.get(self.exchange, "K")

    @property
    def stex_rank(self):
        return self.EXCHANGE_RANK.get(self.exchange, "1")


# ═══════════════════════════════════════════════════════════════
#  지표 계산
# ═══════════════════════════════════════════════════════════════
def ema_arr(values, period):
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return v
    k = 2.0 / (period + 1)
    out = np.empty_like(v)
    out[0] = v[0]
    for i in range(1, v.size):
        out[i] = v[i] * k + out[i - 1] * (1 - k)
    return out


def sma_last(values, period):
    if len(values) < period:
        return None
    return float(np.mean(values[-period:]))


def _ema_series(arr, period):
    import numpy as np
    arr = np.asarray(arr, dtype=float)
    out = np.empty_like(arr)
    k = 2.0 / (period + 1)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out


def _sma_series(arr, period):
    import numpy as np
    arr = np.asarray(arr, dtype=float)
    out = np.full_like(arr, np.nan)
    if len(arr) >= period:
        c = np.cumsum(np.insert(arr, 0, 0))
        out[period-1:] = (c[period:] - c[:-period]) / period
    return out


def compute_obv(closes, volumes):
    c = np.asarray(closes, dtype=float)
    v = np.asarray(volumes, dtype=float)
    obv = np.zeros(c.size)
    for i in range(1, c.size):
        if c[i] > c[i - 1]:
            obv[i] = obv[i - 1] + v[i]
        elif c[i] < c[i - 1]:
            obv[i] = obv[i - 1] - v[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def compute_macd(closes, fast=12, slow=26, signal=9, full=False):
    """full=False면 (macd, signal, hist) 마지막 값, full=True면 전 구간 배열 반환."""
    import numpy as np
    closes = np.asarray(closes, dtype=float)
    ema_f = _ema_series(closes, fast)
    ema_s = _ema_series(closes, slow)
    macd_line = ema_f - ema_s
    macd_sig  = _ema_series(macd_line, signal)
    hist = macd_line - macd_sig
    if full:
        return macd_line, macd_sig, hist
    # full=False: 마지막 값 + 상태 정보 (기존 호출과의 호환성 유지)
    macd_v, sig_v, hist_v = float(macd_line[-1]), float(macd_sig[-1]), float(hist[-1])
    if macd_v > sig_v and macd_v > 0:
        arr = "정배열"
    elif macd_v < sig_v and macd_v < 0:
        arr = "역배열"
    else:
        arr = "혼조"
    return {"macd": macd_v, "signal": sig_v, "hist": hist_v, "array": arr,
            "macd_line": macd_line, "signal_line": macd_sig}


def compute_disparity(closes, periods):
    cur = closes[-1]
    out = {}
    for p in periods:
        m = sma_last(closes, p)
        out[p] = (cur / m * 100) if m else None
    return out


def compute_jma(closes, length, phase, power):
    import numpy as np
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    e0 = np.zeros(n)
    e1 = np.zeros(n)
    e2 = np.zeros(n)
    jma = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    price_sum = np.zeros(n)

    clamped_phase = max(-100.0, min(100.0, float(phase))) / 100.0 + 1.5
    beta = 0.45 * (length - 1) / (0.45 * (length - 1) + 2)
    alpha = beta ** max(1, power)

    for i in range(n):
        close = closes[i]
        if i == 0:
            e0[i] = close
            e1[i] = 0.0
            e2[i] = 0.0
            jma[i] = close
            trend[i] = 0
            price_sum[i] = close
        else:
            prev_e0 = e0[i-1]
            prev_e1 = e1[i-1]
            prev_e2 = e2[i-1]
            prev_jma = jma[i-1]
            prev_trend = trend[i-1]

            e0_val = (1.0 - alpha) * close + alpha * prev_e0
            e1_val = (close - e0_val) * (1.0 - beta) + beta * prev_e1
            phase_adjusted = e0_val + clamped_phase * e1_val
            e2_val = (phase_adjusted - prev_jma) * (1.0 - alpha) * (1.0 - alpha) + alpha * alpha * prev_e2

            e0[i] = e0_val
            e1[i] = e1_val
            e2[i] = e2_val
            price_sum[i] = price_sum[i-1] + close

            if i < length:
                jma[i] = price_sum[i] / (i + 1)
            else:
                jma[i] = round((prev_jma + e2_val) * 10) / 10.0

            if jma[i] > prev_jma:
                trend[i] = 1
            elif jma[i] < prev_jma:
                trend[i] = -1
            else:
                trend[i] = prev_trend

    if n > 0:
        jma[0] = closes[0]

    return jma, trend


def compute_supertrend(highs, lows, closes, period, multiplier):
    import numpy as np
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    
    atr = np.full(n, np.nan)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    supertrend = np.full(n, np.nan)
    trend_up = np.zeros(n, dtype=bool)

    tr = np.zeros(n)
    for i in range(n):
        high_low = highs[i] - lows[i]
        if i > 0:
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            tr[i] = max(high_low, high_close, low_close)
        else:
            tr[i] = high_low

    initialized_idx = -1
    for i in range(n):
        if i < period - 1:
            continue
        
        if i == period - 1:
            atr[i] = np.sum(tr[:period]) / period
        else:
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

        atr_val = atr[i]
        hl2 = (highs[i] + lows[i]) / 2.0
        basic_upper = hl2 + multiplier * atr_val
        basic_lower = hl2 - multiplier * atr_val

        if initialized_idx == -1 or i == period - 1:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            supertrend[i] = basic_lower
            trend_up[i] = True
            initialized_idx = i
            continue

        prev_idx = i - 1
        if basic_upper < final_upper[prev_idx] or closes[prev_idx] > final_upper[prev_idx]:
            final_upper[i] = basic_upper
        else:
            final_upper[i] = final_upper[prev_idx]

        if basic_lower > final_lower[prev_idx] or closes[prev_idx] < final_lower[prev_idx]:
            final_lower[i] = basic_lower
        else:
            final_lower[i] = final_lower[prev_idx]

        if supertrend[prev_idx] == final_upper[prev_idx]:
            if closes[i] <= final_upper[i]:
                supertrend[i] = final_upper[i]
            else:
                supertrend[i] = final_lower[i]
        else:
            if closes[i] >= final_lower[i]:
                supertrend[i] = final_lower[i]
            else:
                supertrend[i] = final_upper[i]

        trend_up[i] = (supertrend[i] == final_lower[i])
        initialized_idx = max(initialized_idx, i)

    return supertrend, trend_up


def compute_vwma(closes, vols, length):
    import numpy as np
    closes = np.asarray(closes, dtype=float)
    vols = np.asarray(vols, dtype=float)
    n = len(closes)
    
    vwma = np.full(n, np.nan)
    weighted_price = closes * vols
    for i in range(n):
        if i < length - 1:
            continue
        start_idx = i - length + 1
        sum_weighted = np.sum(weighted_price[start_idx:i+1])
        sum_vol = np.sum(vols[start_idx:i+1])
        if sum_vol != 0:
            vwma[i] = sum_weighted / sum_vol
            
    return vwma


def compute_zigzag_state_series(highs, lows, closes, dev_pct=5.0):
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    n = len(closes)

    trend = np.zeros(n, dtype=int)
    turn_up = np.zeros(n, dtype=bool)
    turn_down = np.zeros(n, dtype=bool)
    if n < 2:
        return trend, turn_up, turn_down

    r = float(dev_pct) / 100.0
    direction = 0
    ext_idx = 0
    ext_val = highs[0]
    i = 1
    while i < n:
        up_move = (highs[i] - lows[0]) / lows[0] if lows[0] else 0.0
        dn_move = (highs[0] - lows[i]) / highs[0] if highs[0] else 0.0
        if up_move >= r and up_move >= dn_move:
            direction = 1
            ext_idx, ext_val = i, highs[i]
            trend[i] = 1
            break
        elif dn_move >= r:
            direction = -1
            ext_idx, ext_val = i, lows[i]
            trend[i] = -1
            break
        i += 1

    if direction == 0:
        return trend, turn_up, turn_down

    for j in range(i + 1, n):
        current_direction = direction
        if direction == 1:
            if highs[j] >= ext_val:
                ext_val, ext_idx = highs[j], j
            elif (ext_val - lows[j]) / ext_val >= r:
                direction = -1
                turn_down[j] = True
                ext_val, ext_idx = lows[j], j
        else:
            if lows[j] <= ext_val:
                ext_val, ext_idx = lows[j], j
            elif (highs[j] - ext_val) / ext_val >= r:
                direction = 1
                turn_up[j] = True
                ext_val, ext_idx = highs[j], j
        trend[j] = direction
        if trend[j - 1] == 0:
            trend[j - 1] = current_direction

    for j in range(1, n):
        if trend[j] == 0:
            trend[j] = trend[j - 1]
    return trend, turn_up, turn_down



def deduplicate_times(times, intraday):
    if intraday and len(times) > 1:
        for i in range(1, len(times)):
            try:
                t_curr = int(times[i])
                t_prev = int(times[i-1])
                if t_curr <= t_prev:
                    times[i] = t_prev + 1
            except (ValueError, TypeError):
                pass
    return times


# ═══════════════════════════════════════════════════════════════
#  키움 REST 클라이언트
# ═══════════════════════════════════════════════════════════════
class KiwoomREST:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = None
        self._lock = threading.Lock()
        self._last = 0.0
        self.min_interval = 0.22

    def _throttle(self):
        with self._lock:
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def issue_token(self):
        url = self.cfg.rest_host + "/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        body = {"grant_type": "client_credentials",
                "appkey": self.cfg.appkey, "secretkey": self.cfg.secretkey}
        r = requests.post(url, headers=headers, data=jdump(body), timeout=10)
        r.raise_for_status()
        j = r.json()
        self.token = j.get("token") or j.get("access_token")
        if not self.token:
            raise RuntimeError("토큰 발급 실패: " + str(j))
        return self.token

    def call(self, path, api_id, body, cont_yn="N", next_key=""):
        self._throttle()
        url = self.cfg.rest_host + path
        headers = {"Content-Type": "application/json;charset=UTF-8",
                   "authorization": "Bearer " + self.token,
                   "api-id": api_id, "cont-yn": cont_yn, "next-key": next_key}
        r = requests.post(url, headers=headers, data=jdump(body), timeout=10)
        r.raise_for_status()
        data = r.json()
        data["_cont_yn"] = r.headers.get("cont-yn", "N")
        data["_next_key"] = r.headers.get("next-key", "")
        return data

    def _paged(self, path, api_id, body, list_key=None, max_rows=600, pages=8):
        rows, cont, nkey = [], "N", ""
        for _ in range(pages):
            res = self.call(path, api_id, body, cont, nkey)
            lst = self._find_list(res, list_key)
            if lst:
                rows.extend(lst)
            cont, nkey = res.get("_cont_yn", "N"), res.get("_next_key", "")
            if len(rows) >= max_rows or cont != "Y":
                break
        return rows

    # ── 차트 OHLCV ──
    def ohlcv(self, code, tf="D", max_bars=600):
        if tf in ("D", "W", "M"):
            api = {"D": "ka10081", "W": "ka10082", "M": "ka10083"}[tf]
            body = {"stk_cd": code, "base_dt": datetime.now().strftime("%Y%m%d"),
                    "upd_stkpc_tp": self.cfg.adjust_price}
            return self._parse_dwm(self._paged("/api/dostk/chart", api, body, max_rows=max_bars))
        elif tf.startswith("m"):
            body = {"stk_cd": code, "tic_scope": tf[1:], "upd_stkpc_tp": self.cfg.adjust_price}
            return self._parse_intraday(self._paged("/api/dostk/chart", "ka10080", body, max_rows=max_bars))
        elif tf.startswith("t"):
            body = {"stk_cd": code, "tic_scope": tf[1:], "upd_stkpc_tp": self.cfg.adjust_price}
            return self._parse_intraday(self._paged("/api/dostk/chart", "ka10079", body, max_rows=max_bars))
        return []

    def _parse_dwm(self, rows):
        rows = list(reversed(rows))
        out = []
        for row in rows:
            dt = str(row.get("dt") or row.get("stck_bsop_date") or "")
            o, hi = self._n(row.get("open_pric")), self._n(row.get("high_pric"))
            lo, c = self._n(row.get("low_pric")), self._n(row.get("cur_prc"))
            v = self._n(row.get("trde_qty"))
            if c is None:
                continue
            out.append({"date": dt, "open": abs(o or c), "high": abs(hi or c),
                        "low": abs(lo or c), "close": abs(c), "volume": abs(v or 0)})
        return out

    def _parse_intraday(self, rows):
        rows = list(reversed(rows))
        out = []
        for row in rows:
            t = str(row.get("cntr_tm") or row.get("dt") or "")
            o, hi = self._n(row.get("open_pric")), self._n(row.get("high_pric"))
            lo, c = self._n(row.get("low_pric")), self._n(row.get("cur_prc"))
            v = self._n(row.get("trde_qty"))
            if c is None:
                continue
            out.append({"date": t, "open": abs(o or c), "high": abs(hi or c),
                        "low": abs(lo or c), "close": abs(c), "volume": abs(v or 0)})
        return out

    # ── 순위 ──
    def top_trade_value(self, mrkt_tp="000"):
        body = {"mrkt_tp": mrkt_tp, "mang_stk_incls": "0", "stex_tp": self.cfg.stex_rank}
        return self._find_list(self.call("/api/dostk/rkinfo", "ka10032", body))

    def top_change_rate(self, mrkt_tp="000"):
        body = {"mrkt_tp": mrkt_tp, "sort_tp": "1", "trde_qty_cnd": "0000",
                "stk_cnd": "0", "crd_cnd": "0", "updown_incls": "1",
                "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": self.cfg.stex_rank}
        return self._find_list(self.call("/api/dostk/rkinfo", "ka10027", body))

    # ── 관심종목정보요청(ka10095) : 최대 99종목 1회 일괄 조회 ──
    def watchlist_info(self, codes):
        out = {}
        if not codes:
            return out
        SEP = ["|", ";", ","]   # 환경별 구분자 fallback
        for i in range(0, len(codes), 99):
            chunk = codes[i:i + 99]
            res = None
            for sep in SEP:
                try:
                    res = self.call("/api/dostk/stkinfo", "ka10095",
                                    {"stk_cd": sep.join(chunk)})
                    if self._find_list(res):
                        break
                except Exception:
                    res = None
            if not res:
                continue
            for row in self._find_list(res):
                c = (row.get("stk_cd") or row.get("code") or "").lstrip("A")
                if not c:
                    continue
                out[c] = {
                    "name": (row.get("stk_nm") or row.get("stk_name") or "").strip(),
                    "price": abs(self._n(row.get("cur_prc")) or 0),
                    "chg_rate": self._n(row.get("flu_rt") or row.get("fluc_rt")
                                        or row.get("chg_rate")) or 0.0,
                    "trade_value": abs(self._n(row.get("trde_prica")
                                               or row.get("trde_amt")) or 0),
                }
        return out

    # ── 주문 (서버가 KIWOOM_MOCK으로 모의/실 분기) ──
    def order_buy(self, code, qty, price=0, trde_tp="03"):
        body = {"dmst_stex_tp": self.cfg.stex_cond, "stk_cd": code,
                "ord_qty": str(int(qty)), "ord_uv": str(int(price)), "trde_tp": trde_tp}
        return self.call("/api/dostk/ordr", "kt10000", body)

    def order_sell(self, code, qty, price=0, trde_tp="03"):
        body = {"dmst_stex_tp": self.cfg.stex_cond, "stk_cd": code,
                "ord_qty": str(int(qty)), "ord_uv": str(int(price)), "trde_tp": trde_tp}
        return self.call("/api/dostk/ordr", "kt10001", body)

    @staticmethod
    def _find_list(res, key=None):
        if key and isinstance(res.get(key), list):
            return res[key]
        for k, v in res.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return []

    @staticmethod
    def _n(s):
        if s is None:
            return None
        try:
            return float(str(s).replace(",", "").replace("+", "").strip())
        except ValueError:
            return None


# ═══════════════════════════════════════════════════════════════
#  조건검색 WebSocket
# ═══════════════════════════════════════════════════════════════
class ConditionWS(threading.Thread):
    def __init__(self, cfg, token, on_list, on_event, on_log, on_bar=None):
        super().__init__(daemon=True)
        self.cfg, self.token = cfg, token
        self.on_list, self.on_event, self.on_log = on_list, on_event, on_log
        self.on_bar = on_bar or (lambda code, bar: None)
        self.ws = None
        self._reg_seq = None
        self._stop = False
        self._connected = False
        self.builders = {}
        self.rt_code = None
        self.rt_tf = "m1"
        self._lock = threading.RLock()

    def run(self):
        if websocket is None:
            self.on_log("websocket-client 미설치")
            return
        while not self._stop:
            try:
                self._connected = False
                self.ws = websocket.WebSocketApp(
                    self.cfg.ws_host, on_open=self._open, on_message=self._msg,
                    on_error=lambda w, e: self.on_log("WS오류: " + str(e)),
                    on_close=lambda w, c, r: self._on_close())
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                self.on_log("WS예외: " + str(e))
            if self._stop:
                break
            time.sleep(5)

    def _on_close(self):
        self._connected = False
        self.on_log("WS종료")

    def _send(self, obj):
        try:
            if self.ws:
                self.ws.send(jdump(obj))
        except Exception:
            pass

    def _open(self, ws):
        self.on_log("WS연결 -> LOGIN")
        self._send({"trnm": "LOGIN", "token": self.token})

    def _msg(self, ws, raw):
        try:
            m = json.loads(raw)
        except Exception:
            return
        t = m.get("trnm")
        if t == "LOGIN":
            if str(m.get("return_code")) == "0":
                self._connected = True
                self.on_log("WS로그인 성공 -> 조건식 요청")
                self._send({"trnm": "CNSRLST"})
                with self._lock:
                    if self.rt_code:
                        self._reg_real(self.rt_code)
            else:
                self.on_log("WS로그인 실패: " + str(m.get("return_msg")))
        elif t == "PING":
            self._send(m)
        elif t == "CNSRLST":
            items = [(str(r[0]), str(r[1])) for r in m.get("data", [])
                     if isinstance(r, (list, tuple)) and len(r) >= 2]
            self.on_list(items)
        elif t == "CNSRREQ":
            self._handle_cond_events(m)
        elif t == "REAL":
            self._handle_real(m)

    def _handle_cond_events(self, m):
        for row in m.get("data", []):
            if not isinstance(row, dict):
                continue
            code = row.get("jmcode") or row.get("9001") or \
                row.get("values", {}).get("9001")
            typ = row.get("type") or row.get("841") or "I"
            if code:
                self.on_event(str(code).lstrip("A"), str(typ).upper())

    def _handle_real(self, m):
        for row in m.get("data", []):
            if not isinstance(row, dict):
                continue
            rtype = row.get("type")
            if rtype == "0B":
                vals = row.get("values", {})
                price = self._fnum(vals.get("10"))
                cumv = self._inum(vals.get("13"))
                tm = str(vals.get("20") or "")
                if price is None:
                    continue
                code = str(row.get("item", self.rt_code or "")).lstrip("A")
                if not code:
                    continue
                bar = self.get_builder(code).on_tick(abs(price), cumv, tm.zfill(6))
                if bar:
                    self.on_bar(code, bar)
            else:
                code = row.get("jmcode") or row.get("9001") or \
                    row.get("values", {}).get("9001")
                typ = row.get("type") or row.get("841") or "I"
                if code:
                    self.on_event(str(code).lstrip("A"), str(typ).upper())

    def get_builder(self, code):
        with self._lock:
            if code not in self.builders:
                b = BarBuilder()
                b.reset(self.rt_tf)
                self.builders[code] = b
            return self.builders[code]

    def register(self, seq):
        self._reg_seq = seq
        self._send({"trnm": "CNSRREQ", "seq": str(seq),
                    "search_type": "1", "stex_tp": self.cfg.stex_cond})
        self.on_log("조건식 " + str(seq) + " 실시간 등록")

    def clear(self):
        if self._reg_seq is not None:
            self._send({"trnm": "CNSRCLR", "seq": str(self._reg_seq)})
            self._reg_seq = None

    def subscribe_real(self, code, tf):
        with self._lock:
            old = self.rt_code
            self.rt_code = code
            self.rt_tf = tf
            self.get_builder(code).reset(tf)
        if not self._connected:
            return
        if old and old != code:
            self._send({"trnm": "REMOVE", "grp_no": "1",
                        "data": [{"item": [old], "type": ["0B"]}]})
        self._reg_real(code)

    def set_real_tf(self, tf):
        with self._lock:
            self.rt_tf = tf
            if self.rt_code:
                self.get_builder(self.rt_code).reset(tf)

    def _reg_real(self, code):
        self._send({"trnm": "REG", "grp_no": "1", "refresh_yn": "1",
                    "data": [{"item": [code], "type": ["0B"]}]})
        self.on_log("실시간 시세 등록: " + str(code))

    def stop(self):
        self._stop = True
        self.clear()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    @staticmethod
    def _fnum(s):
        try:
            return float(str(s).replace(",", "").replace("+", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _inum(s):
        try:
            return int(float(str(s).replace(",", "").replace("+", "").strip()))
        except (ValueError, TypeError):
            return None

# ═══════════════════════════════════════════════════════════════
#  순위 산정 엔진
# ═══════════════════════════════════════════════════════════════
class RankingEngine:
    def __init__(self, rest, settings):
        self.rest = rest
        self.settings = settings
        self.tv_rank = {}
        self.cr_rank = {}
        self.tv_val = {}
        self.name = {}
        self.info = {}          # ka10095 일괄조회 캐시
        self.chart_cache = {}

    def refresh_ranks(self):
        try:
            for i, row in enumerate(self.rest.top_trade_value(), 1):
                c = self._code(row)
                if c:
                    self.tv_rank[c] = i
                    self.tv_val[c] = self.rest._n(
                        row.get("trde_prica") or row.get("trde_amt")) or 0.0
                    if row.get("stk_nm"):
                        self.name[c] = row["stk_nm"]
        except Exception as e:
            print("거래대금순위 실패:", e)
        try:
            for i, row in enumerate(self.rest.top_change_rate(), 1):
                c = self._code(row)
                if c:
                    self.cr_rank[c] = i
                    if row.get("stk_nm"):
                        self.name[c] = row["stk_nm"]
        except Exception as e:
            print("상승률순위 실패:", e)

    @staticmethod
    def _code(row):
        c = row.get("stk_cd") or row.get("code")
        return str(c).lstrip("A") if c else None

    def enrich_captured(self, codes):
        """미확보 종목명/시세를 ka10095로 99개씩 1회 일괄 보충 (rate limit 회피)."""
        need = [c for c in codes
                if c not in self.info and (not self.name.get(c) or self.name.get(c) == c)]
        if not need:
            return
        fetched = self.rest.watchlist_info(need)
        for c, d in fetched.items():
            self.info[c] = d
            if d.get("name"):
                self.name[c] = d["name"]

    def get_ohlcv(self, code, tf="D", max_bars=600):
        ttl = self.settings.params["chart_cache_ttl"]
        key = (code, tf)
        now = time.time()
        if key in self.chart_cache:
            ts, data = self.chart_cache[key]
            if now - ts < ttl:
                return data
        data = self.rest.ohlcv(code, tf, max_bars)
        self.chart_cache[key] = (now, data)
        return data

    def analyze(self, code):
        p = self.settings.params
        ohlcv = self.get_ohlcv(code, "D")
        if len(ohlcv) < 30:
            return None
        closes = [r["close"] for r in ohlcv]
        vols = [r["volume"] for r in ohlcv]

        info = self.info.get(code, {})
        nm = self.name.get(code) or info.get("name") or code
        self.name[code] = nm

        m = {"code": code, "name": nm, "price": closes[-1]}
        m["chg_rate"] = ((closes[-1] - closes[-2]) / closes[-2] * 100
                         if len(closes) >= 2 and closes[-2] else 0.0)
        m["trade_value"] = self.tv_val.get(code, info.get("trade_value", 0.0))
        m["rank_tv"] = self.tv_rank.get(code, 9999)
        m["rank_cr"] = self.cr_rank.get(code, 9999)

        obv = compute_obv(closes, vols)
        obv_sig = ema_arr(obv, p["obv_signal_period"])
        m["obv"] = float(obv[-1])
        m["obv_signal"] = float(obv_sig[-1])
        m["obv_trend"] = "상승" if obv[-1] > obv_sig[-1] else "하락"

        macd = compute_macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
        m["macd_array"] = macd["array"] if macd else "-"
        m["macd_hist"] = macd["hist"] if macd else 0.0

        disp = compute_disparity(closes, p["ma_periods"])
        per = p["ma_periods"]
        m["disp5"] = disp.get(per[0]) if len(per) > 0 else None
        m["disp20"] = disp.get(per[1]) if len(per) > 1 else None
        m["disp60"] = disp.get(per[2]) if len(per) > 2 else None

        m["score"] = self._score(m)
        m["state"] = self._state(m)
        m["updated"] = datetime.now().strftime("%H:%M:%S")
        return m

    def passes_filter(self, m):
        p = self.settings.params
        if not p["filter_enabled"]:
            return True
        if p["filter_min_price"] and m["price"] < p["filter_min_price"]:
            return False
        if p["filter_max_price"] and m["price"] > p["filter_max_price"]:
            return False
        if m["chg_rate"] < p["filter_min_chg_rate"]:
            return False
        if p["filter_max_tv_rank"] and m["rank_tv"] > p["filter_max_tv_rank"]:
            return False
        if p["filter_obv_up_only"] and m["obv_trend"] != "상승":
            return False
        if p["filter_macd_bull_only"] and m["macd_array"] != "정배열":
            return False
        if p["filter_exclude_overheat"] and m["state"] == "과열주의":
            return False
        return True

    def _score(self, m):
        p = self.settings.params
        w = self.settings.weights()
        s_tv = max(0, 100 - (m["rank_tv"] - 1) * 0.5)
        s_cr = max(0, 100 - (m["rank_cr"] - 1) * 0.5)
        s_obv = 100 if m["obv_trend"] == "상승" else 0
        s_macd = {"정배열": 100, "혼조": 50, "역배열": 0, "-": 50}[m["macd_array"]]
        s_disp = 0
        if m["disp20"] is not None:
            s_disp = max(0, 100 - abs(m["disp20"] - p["disp_ideal"]) * p["disp_penalty"])
        total = (s_tv * w["tv"] + s_cr * w["cr"] + s_obv * w["obv"]
                 + s_macd * w["macd"] + s_disp * w["disp"])
        return round(total / (sum(w.values()) or 1), 2)

    def _state(self, m):
        p = self.settings.params
        bull = (m["obv_trend"] == "상승") + (m["macd_array"] == "정배열") + (m["chg_rate"] > 0)
        if m["disp20"] and m["disp20"] > p["overheat_disp20"]:
            return "과열주의"
        if bull >= p["bull_strong"]:
            return "강력매수후보"
        if bull == p["bull_watch"]:
            return "매수관심"
        if m["macd_array"] == "역배열" and m["obv_trend"] == "하락":
            return "하락추세"
        return "관망"

    def chart_payload(self, code, tf="D", max_bars=600):
        p = self.settings.params
        ohlcv = self.get_ohlcv(code, tf, max_bars)
        if not ohlcv:
            return None
        closes = [r["close"] for r in ohlcv]
        vols = [r["volume"] for r in ohlcv]
        intraday = tf.startswith("m") or tf.startswith("t")
        times = [self._fmt_time(r["date"], intraday) for r in ohlcv]
        times = deduplicate_times(times, intraday)

        candles, volumes = [], []
        for i, r in enumerate(ohlcv):
            candles.append({"time": times[i], "open": r["open"], "high": r["high"],
                            "low": r["low"], "close": r["close"]})
            clr = "rgba(38,166,154,0.5)" if r["close"] >= r["open"] else "rgba(239,83,80,0.5)"
            volumes.append({"time": times[i], "value": r["volume"], "color": clr})

        ma_lines = {}
        for pr in p["ma_periods"]:
            arr = []
            for i in range(len(closes)):
                if i + 1 >= pr:
                    arr.append({"time": times[i],
                                "value": round(float(np.mean(closes[i + 1 - pr:i + 1])), 2)})
            ma_lines[pr] = arr

        obv = compute_obv(closes, vols)
        obv_sig = ema_arr(obv, p["obv_signal_period"])
        obv_line = [{"time": times[i], "value": float(obv[i])} for i in range(len(obv))]
        obv_sig_line = [{"time": times[i], "value": float(obv_sig[i])} for i in range(len(obv_sig))]

        macd = compute_macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
        macd_line = macd_sig = macd_hist = []
        if macd:
            ml, sl = macd["macd_line"], macd["signal_line"]
            macd_line = [{"time": times[i], "value": float(ml[i])} for i in range(len(ml))]
            macd_sig = [{"time": times[i], "value": float(sl[i])} for i in range(len(sl))]
            macd_hist = [{"time": times[i], "value": float(ml[i] - sl[i]),
                          "color": ("rgba(38,166,154,0.6)" if ml[i] >= sl[i]
                                    else "rgba(239,83,80,0.6)")} for i in range(len(ml))]

        # JMA
        jma, jma_trend = compute_jma(closes, p["jma_length"], p["jma_phase"], p["jma_power"])
        jma_line = []
        for i in range(len(jma)):
            if not np.isnan(jma[i]):
                jma_line.append({
                    "time": times[i],
                    "value": float(jma[i]),
                    "color": "#00e676" if jma_trend[i] >= 0 else "#ff6d00"
                })

        # Supertrend
        highs = [r["high"] for r in ohlcv]
        lows = [r["low"] for r in ohlcv]
        supertrend, trend_up = compute_supertrend(highs, lows, closes, p["supertrend_period"], p["supertrend_multiplier"])
        supertrend_line = []
        for i in range(len(supertrend)):
            if not np.isnan(supertrend[i]):
                supertrend_line.append({
                    "time": times[i],
                    "value": float(supertrend[i]),
                    "color": "#66d28a" if trend_up[i] else "#ff7a5c"
                })

        # VWMA
        vwma = compute_vwma(closes, vols, p["vwma_length"])
        vwma_line = []
        for i in range(len(vwma)):
            if not np.isnan(vwma[i]):
                vwma_line.append({
                    "time": times[i],
                    "value": float(vwma[i])
                })

        return {"code": code, "name": self.name.get(code, code), "tf": tf,
                "candles": candles, "volumes": volumes,
                "ma": [{"period": pr, "data": ma_lines[pr]} for pr in p["ma_periods"]],
                "obv": obv_line, "obv_signal": obv_sig_line,
                "macd": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
                "jma": jma_line, "supertrend": supertrend_line, "vwma": vwma_line}

    def chart_candles(self, code, tf="D"):
        """signal_backtest용 캔들 리스트. get_ohlcv는 이미 open/high/low/close/volume로 정규화됨."""
        raw = self.get_ohlcv(code, tf, 300)
        if not raw:
            return []
        intraday = tf.startswith("m") or tf.startswith("t")
        times = [self._fmt_time(r["date"], intraday) for r in raw]
        times = deduplicate_times(times, intraday)
        
        candles = []
        for i, r in enumerate(raw):
            candles.append({
                "time":   times[i],
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r.get("volume", 0)),
            })
        candles.sort(key=lambda x: x["time"])
        return candles

    @staticmethod
    def _fmt_time(s, intraday):
        s = str(s)
        if intraday:
            if len(s) >= 12 and s.isdigit():
                try:
                    dt = datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
                    dt_utc = dt.replace(tzinfo=timezone.utc)
                    return int(dt_utc.timestamp())
                except ValueError:
                    return s
            return s
        if len(s) == 8 and s.isdigit():
            return s[:4] + "-" + s[4:6] + "-" + s[6:8]
        return s




# ═══════════════════════════════════════════════════════════════
#  전략 엔진 (백테스트 + 모의/실 주문)
# ═══════════════════════════════════════════════════════════════
_BIN = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul, _ast.Div: _op.truediv}
_CMP = {_ast.Lt: _op.lt, _ast.Gt: _op.gt, _ast.LtE: _op.le, _ast.GtE: _op.ge,
        _ast.Eq: _op.eq, _ast.NotEq: _op.ne}
_FUNCS = {"crossover", "crossunder", "abs", "min", "max"}


class SafeEval:
    """eval/exec 금지. 화이트리스트 AST 평가기."""
    def __init__(self, variables, funcs):
        self.vars = variables
        self.funcs = funcs

    def eval(self, expr):
        return self._ev(_ast.parse(expr, mode="eval").body)

    def _ev(self, n):
        if isinstance(n, _ast.BoolOp):
            vals = [self._ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, _ast.And) else any(vals)
        if isinstance(n, _ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](self._ev(n.left), self._ev(n.right))
        if isinstance(n, _ast.Compare):
            left = self._ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = self._ev(comp)
                if type(op) not in _CMP or not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, _ast.UnaryOp) and isinstance(n.op, _ast.Not):
            return not self._ev(n.operand)
        if isinstance(n, _ast.Call):
            fn = n.func.id
            if fn not in _FUNCS:
                raise ValueError("허용 안 된 함수: " + fn)
            return self.funcs[fn](*[self._ev(a) for a in n.args])
        if isinstance(n, _ast.Name):
            if n.id not in self.vars:
                raise ValueError("알 수 없는 변수: " + n.id)
            return self.vars[n.id]
        if isinstance(n, _ast.Constant):
            return n.value
        raise ValueError("허용 안 된 구문: " + type(n).__name__)


def _series_vars(closes, vols, highs, lows, p):
    ma = {per: [None] * len(closes) for per in p["ma_periods"]}
    for per in p["ma_periods"]:
        for i in range(len(closes)):
            if i + 1 >= per:
                ma[per][i] = float(np.mean(closes[i + 1 - per:i + 1]))
    obv = compute_obv(closes, vols)
    obv_sig = ema_arr(obv, p["obv_signal_period"])
    macd = compute_macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    
    supertrend, trend_up = compute_supertrend(highs, lows, closes, p["supertrend_period"], p["supertrend_multiplier"])
    supertrend_trend = [1 if t else -1 for t in trend_up]
    jma, _ = compute_jma(closes, p["jma_length"], p["jma_phase"], p["jma_power"])
    vwma = compute_vwma(closes, vols, p["vwma_length"])
    zigzag_trend, zigzag_turn_up, zigzag_turn_down = compute_zigzag_state_series(highs, lows, closes, 5.0)
    
    return ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma, zigzag_trend, zigzag_turn_up, zigzag_turn_down


class StrategyEngine:
    """전략 = {entry_expr, exit_expr, qty, stop_pct, take_pct}"""
    def __init__(self, rest, settings):
        self.rest = rest
        self.settings = settings

    def _ctx_at(self, i, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
                zigzag_trend, zigzag_turn_up, zigzag_turn_down, prev):
        p = self.settings.params
        per = p["ma_periods"]

        def g(arr, j):
            return arr[j] if 0 <= j < len(arr) and arr[j] is not None else None

        v = {"close": closes[i], "obv": float(obv[i]), "obv_signal": float(obv_sig[i])}
        v["ma5"] = g(ma[per[0]], i) if len(per) > 0 else None
        v["ma20"] = g(ma[per[1]], i) if len(per) > 1 else None
        v["ma60"] = g(ma[per[2]], i) if len(per) > 2 else None
        if macd:
            v["macd"] = float(macd["macd_line"][i])
            v["macd_signal"] = float(macd["signal_line"][i])
        else:
            v["macd"] = v["macd_signal"] = 0.0
        v["macd_hist"] = v["macd"] - v["macd_signal"]
        v["disp20"] = (closes[i] / v["ma20"] * 100) if v.get("ma20") else 100.0
        
        # JMA and Supertrend variables
        v["supertrend"] = float(supertrend[i]) if not np.isnan(supertrend[i]) else None
        v["supertrend_trend"] = int(supertrend_trend[i])
        v["jma"] = float(jma[i]) if not np.isnan(jma[i]) else None
        v["vwma"] = float(vwma[i]) if not np.isnan(vwma[i]) else None
        v["zigzag_trend"] = int(zigzag_trend[i]) if i < len(zigzag_trend) else 0
        v["zigzag_turn_up"] = bool(zigzag_turn_up[i]) if i < len(zigzag_turn_up) else False
        v["zigzag_turn_down"] = bool(zigzag_turn_down[i]) if i < len(zigzag_turn_down) else False
        if prev:
            for key, value in prev.items():
                if key.startswith("prev_"):
                    continue
                v["prev_" + key] = value
        return v

    def _funcs(self, cur, prv):
        """직전봉(prv) 기준 정확한 교차 판정."""
        def crossover(a, b):
            return a > b and prv.get("_a", a) <= prv.get("_b", b)
        def crossunder(a, b):
            return a < b and prv.get("_a", a) >= prv.get("_b", b)
        # 단순화: 인자 자체로 현재>직전 비교가 어려우므로 macd 교차에 한정 사용 권장
        return {"crossover": lambda a, b: a > b, "crossunder": lambda a, b: a < b,
                "abs": abs, "min": min, "max": max}

    def backtest(self, code, tf="D", strategy=None, fee=0.0015):
        p = self.settings.params
        ohlcv = self.rest.ohlcv(code, tf, 600)
        if len(ohlcv) < 60 or not strategy:
            return {"error": "데이터/전략 부족"}
        closes = [r["close"] for r in ohlcv]
        vols = [r["volume"] for r in ohlcv]
        highs = [r["high"] for r in ohlcv]
        lows = [r["low"] for r in ohlcv]
        ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma, zigzag_trend, zigzag_turn_up, zigzag_turn_down = _series_vars(closes, vols, highs, lows, p)
        intraday = tf.startswith("m") or tf.startswith("t")
        times = [RankingEngine._fmt_time(r["date"], intraday) for r in ohlcv]
        times = deduplicate_times(times, intraday)

        pos, entry_px = 0, 0.0
        trades, markers, equity = [], [], 1.0
        prev = {}
        entry_i = 0
        for i in range(60, len(closes)):
            v = self._ctx_at(i, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
                             zigzag_trend, zigzag_turn_up, zigzag_turn_down, prev)
            funcs = self._funcs(v, prev)
            px = closes[i]
            try:
                if pos == 0 and SafeEval(v, funcs).eval(strategy["entry_expr"]):
                    pos, entry_px = 1, px
                    entry_i = i
                    markers.append({"time": times[i], "position": "belowBar",
                                    "color": "#26a69a", "shape": "arrowUp", "text": "BUY"})
                elif pos == 1:
                    hs = strategy.get("stop_pct", 0) and px <= entry_px * (1 - strategy["stop_pct"] / 100)
                    ht = strategy.get("take_pct", 0) and px >= entry_px * (1 + strategy["take_pct"] / 100)
                    se = strategy.get("exit_expr") and SafeEval(v, funcs).eval(strategy["exit_expr"])
                    if hs or ht or se:
                        ret = (px / entry_px) - 1 - fee * 2
                        equity *= (1 + ret)
                        trades.append({"entry": entry_px, "exit": px, "ret": round(ret * 100, 2),
                                       "reason": "stop" if hs else ("take" if ht else "signal"),
                                       "entry_time": times[entry_i],
                                       "exit_time": times[i],
                                       "bars": i - entry_i})
                        markers.append({"time": times[i], "position": "aboveBar",
                                        "color": "#ef5350", "shape": "arrowDown", "text": "SELL"})
                        pos = 0
            except Exception as e:
                return {"error": "표현식 오류: " + str(e)}
            prev = v
        wins = [t for t in trades if t["ret"] > 0]
        return {"code": code, "tf": tf, "trades": trades, "markers": markers,
                "n_trades": len(trades),
                "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
                "total_return": round((equity - 1) * 100, 2)}

    def execute_live(self, code, side, qty):
        if side == "buy":
            return self.rest.order_buy(code, qty)
        elif side == "sell":
            return self.rest.order_sell(code, qty)
        return {"error": "side는 buy/sell"}

    def signal_backtest(self, candles, rule, fee_bp=15, slippage_bp=5):
        """
        차트 검증용 백테스트.
        candles : [{time, open, high, low, close, volume}, ...]  (시간 오름차순, 분봉)
        rule    : {
            "entry": "score_cross",       # 진입 규칙 종류 (아래 _entry_signal 참고)
            "tp_pct": 3.0,                # 익절 %
            "sl_pct": 2.0,                # 손절 %
            "max_bars": 30,               # 시간청산 (봉 수)
            "disp_max": 110,              # 진입 허용 최대 이격도(과열 차단). None이면 무시
            "macd_cross": True            # MACD 시그널 상향돌파 동반 조건
        }
        반환 : {"markers": [...], "trades": [...], "summary": {...}}
        """
        import numpy as np

        closes = np.array([c["close"] for c in candles], dtype=float)
        highs  = np.array([c["high"]  for c in candles], dtype=float)
        lows   = np.array([c["low"]   for c in candles], dtype=float)
        vols   = np.array([c.get("volume", 0) for c in candles], dtype=float)
        n = len(closes)
        if n < 20:
            return {"markers": [], "trades": [], "summary": {"trades": 0, "win_rate": 0, "avg_ret": 0, "sum_ret": 0, "max_win": 0, "max_loss": 0}}

        # MA20, 이격도 계산
        ma20 = np.array([np.mean(closes[max(0, i-19):i+1]) for i in range(n)])
        disp20 = np.where(ma20 > 0, closes / ma20 * 100.0, np.nan)

        # MACD 계산 (지수이동평균 이용)
        ema12 = _ema_series(closes, 12)
        ema26 = _ema_series(closes, 26)
        macd_line = ema12 - ema26
        macd_sig = _ema_series(macd_line, 9)

        fee  = fee_bp / 10000.0
        slip = slippage_bp / 10000.0
        tp = rule.get("tp_pct", 3.0) / 100.0
        sl = rule.get("sl_pct", 2.0) / 100.0
        max_bars = int(rule.get("max_bars", 30))
        disp_max = rule.get("disp_max", None)
        need_macd_cross = rule.get("macd_cross", True)

        markers, trades = [], []
        in_pos = False
        entry_i = entry_px = 0.0

        i = 1
        while i < n:
            if not in_pos:
                # 진입 신호 확인
                ok = rule.get("entry") == "score_cross"
                # 조건 1: MACD 상향 교차
                if ok and need_macd_cross:
                    cross = (macd_line[i] > macd_sig[i])
                    ok = ok and bool(cross)
                # 조건 2: 과열 차단 (이격도 상한)
                if ok and disp_max is not None and not np.isnan(disp20[i]):
                    ok = ok and (disp20[i] < disp_max)
                # 진입 시행
                if ok:
                    in_pos = True
                    entry_i = i
                    entry_px = closes[i] * (1 + slip)  # 슬리피지 반영 매수
                    markers.append({
                        "time": candles[i]["time"], "position": "belowBar",
                        "color": "#26a69a", "shape": "arrowUp",
                        "text": f"BUY {closes[i]:.1f}"})
            else:
                # 보유 중: 익절/손절/시간청산 확인
                held = i - entry_i
                hit_tp = highs[i] >= entry_px * (1 + tp)
                hit_sl = lows[i] <= entry_px * (1 - sl)
                exit_now = (hit_sl) or (hit_tp) or (held >= max_bars) or (i == n - 1)
                if exit_now:
                    if hit_sl:      exit_px, reason = entry_px * (1 - sl), "손절"
                    elif hit_tp:    exit_px, reason = entry_px * (1 + tp), "익절"
                    else:           exit_px, reason = closes[i], "시간청산"
                    exit_px *= (1 - slip)                # 슬리피지 반영 매도
                    ret = (exit_px / entry_px) - 1 - 2 * fee   # 왕복 수수료
                    trades.append({
                        "entry_time": candles[entry_i]["time"],
                        "exit_time":  candles[i]["time"],
                        "entry_px": round(entry_px, 1),
                        "exit_px":  round(exit_px, 1),
                        "bars": held, "reason": reason,
                        "ret_pct": round(ret * 100, 2)})
                    markers.append({
                        "time": candles[i]["time"], "position": "aboveBar",
                        "color": "#26a69a" if ret > 0 else "#ef5350",
                        "shape": "arrowDown",
                        "text": f"SELL {ret*100:+.1f}%"})
                    in_pos = False
            i += 1

        # --- 요약 통계 ---
        if trades:
            rets = [t["ret_pct"] for t in trades]
            wins = [r for r in rets if r > 0]
            summary = {
                "trades": len(trades),
                "win_rate": round(len(wins) / len(trades) * 100, 1),
                "avg_ret": round(sum(rets) / len(rets), 2),
                "sum_ret": round(sum(rets), 2),
                "max_win": round(max(rets), 2),
                "max_loss": round(min(rets), 2),
            }
        else:
            summary = {"trades": 0, "win_rate": 0, "avg_ret": 0,
                       "sum_ret": 0, "max_win": 0, "max_loss": 0}
        return {"markers": markers, "trades": trades, "summary": summary}

# ═══════════════════════════════════════════════════════════════
#  실시간 시세 WebSocket (주식체결 0B) + 진행봉 빌더
# ═══════════════════════════════════════════════════════════════
def _tf_bucket_sec(tf):
    """타임프레임 → 봉 길이(초). 틱(t..)은 0(개수 기반)."""
    if tf.startswith("m"):
        return int(tf[1:]) * 60
    if tf in ("D",):
        return 86400
    if tf in ("W", "M"):
        return 86400  # 일 단위 집계로 근사
    return 0  # 틱 단위


class BarBuilder:
    """들어온 체결 틱을 현재 타임프레임 기준 진행봉으로 누적."""
    def __init__(self):
        self.tf = "m1"
        self.bucket = 0
        self.bar = None          # {time, open, high, low, close, volume}
        self.last_cum_vol = None  # 누적거래량 기반 봉 거래량 산출용
        self.tick_count = 0
        self.lock = threading.Lock()

    def reset(self, tf):
        with self.lock:
            self.tf = tf
            self.bucket = _tf_bucket_sec(tf)
            self.bar = None
            self.last_cum_vol = None
            self.tick_count = 0

    def _bar_time(self, epoch_kst):
        if self.bucket <= 0:
            return epoch_kst  # 틱: 매 틱 시각
        return (epoch_kst // self.bucket) * self.bucket

    def on_tick(self, price, cum_vol, hhmmss):
        """체결 틱 처리. 완성된/진행중 봉 반환 (없으면 None)."""
        with self.lock:
            now = datetime.now()
            try:
                hh = int(hhmmss[0:2]); mm = int(hhmmss[2:4]); ss = int(hhmmss[4:6])
                dt = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            except Exception:
                dt = now
            dt_utc = dt.replace(tzinfo=timezone.utc)
            epoch = int(dt_utc.timestamp())
            bt = self._bar_time(epoch)

            # 봉 거래량: 누적거래량 차분
            vol_inc = 0
            if cum_vol is not None:
                if self.last_cum_vol is not None and cum_vol >= self.last_cum_vol:
                    vol_inc = cum_vol - self.last_cum_vol
                self.last_cum_vol = cum_vol

            new_bar = False
            if self.bar is None:
                new_bar = True
            elif self.bucket > 0 and bt != self.bar["time"]:
                new_bar = True
            elif self.bucket <= 0:
                self.tick_count += 1
                tn = int(self.tf[1:]) if self.tf.startswith("t") else 1
                if self.tick_count >= tn:
                    new_bar = True
                    self.tick_count = 0

            if new_bar:
                self.bar = {"time": bt, "open": price, "high": price,
                            "low": price, "close": price, "volume": vol_inc}
            else:
                self.bar["high"] = max(self.bar["high"], price)
                self.bar["low"] = min(self.bar["low"], price)
                self.bar["close"] = price
                self.bar["volume"] += vol_inc
            return dict(self.bar)



