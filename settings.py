#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings.py — 가변 구조 (10%)
================================================================================
★ 이 파일과 속성/전략 다이얼로그만 수정. core.py는 건드리지 않는다.
"""

import json as _json
import os as _os
import time as _time
import threading as _threading

DEFAULT_PARAMS = {
    # 지표
    "obv_signal_period": 9,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "ma_periods": [5, 20, 60],
    "supertrend_period": 10,
    "supertrend_multiplier": 3.0,
    "jma_length": 7,
    "jma_phase": 50,
    "jma_power": 2,
    "vwma_length": 20,
    # 가중치
    "w_trade_value": 1.0,
    "w_change_rate": 1.0,
    "w_obv": 1.0,
    "w_macd": 1.0,
    "w_disparity": 0.8,
    # 이격도 점수
    "disp_ideal": 102.5,
    "disp_penalty": 4.0,
    # 상태 임계값
    "overheat_disp20": 115.0,
    "bull_strong": 3,
    "bull_watch": 2,
    # 필터
    "filter_enabled": False,
    "filter_min_price": 0,
    "filter_max_price": 0,
    "filter_min_chg_rate": -100.0,
    "filter_max_tv_rank": 0,
    "filter_obv_up_only": False,
    "filter_macd_bull_only": False,
    "filter_exclude_overheat": False,
    # 주기
    "analyze_interval_sec": 20,
    "chart_cache_ttl": 60,
    # 백테스트
    "fee_bp": 15,        # 왕복 수수료/세금 가정 (bp, 0.15%)
    "slippage_bp": 5,    # 한쪽 슬리피지 가정 (bp, 0.05%)
}

CHART_DEFAULTS = {
    "timeframe": "D",
    "visible_bars": 120,
    "max_bars": 600,
    "show_ma": True,
    "show_obv": True,
    "show_macd": True,
    "show_supertrend": False,
    "show_jma": False,
    "show_vwma": False,
}

TIMEFRAMES = [
    ("월", "M"), ("주", "W"), ("일", "D"),
    ("1분", "m1"), ("3분", "m3"), ("5분", "m5"),
    ("15분", "m15"), ("30분", "m30"),
    ("30틱", "t30"), ("60틱", "t60"), ("120틱", "t120"),
    ("240틱", "t240"), ("360틱", "t360"), ("720틱", "t720"),
]

INDICATOR_TOGGLES = [
    ("show_ma", "이동평균(MA)"),
    ("show_obv", "OBV"),
    ("show_macd", "MACD"),
    ("show_supertrend", "Supertrend"),
    ("show_jma", "JMA"),
    ("show_vwma", "VWMA"),
]


class Settings:
    def __init__(self):
        self.params = dict(DEFAULT_PARAMS)
        self.chart = dict(CHART_DEFAULTS)

    def update_params(self, new: dict):
        for k, v in new.items():
            if k not in self.params:
                continue
            old = self.params[k]
            try:
                if isinstance(old, bool):
                    self.params[k] = (v if not isinstance(v, str)
                                      else v.lower() in ("1", "true", "yes", "on"))
                elif isinstance(old, int):
                    self.params[k] = int(float(v))
                elif isinstance(old, float):
                    self.params[k] = float(v)
                elif isinstance(old, list):
                    self.params[k] = [int(x) for x in v] if isinstance(v, list) else old
                else:
                    self.params[k] = v
            except (ValueError, TypeError):
                pass

    def weights(self):
        p = self.params
        return {"tv": p["w_trade_value"], "cr": p["w_change_rate"],
                "obv": p["w_obv"], "macd": p["w_macd"], "disp": p["w_disparity"]}

    def to_dict(self):
        return {"params": self.params, "chart": self.chart}


class StrategyStore:
    """전략 CRUD (JSON 파일 영속화)."""
    def __init__(self, path="strategies.json"):
        self.path = path
        self.items = {}
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    self.items = _json.load(open(self.path, encoding="utf-8"))
                except Exception:
                    self.items = {}

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self):
        with self._lock:
            return list(self.items.values())

    def get(self, sid):
        with self._lock:
            return self.items.get(sid)

    def upsert(self, strat):
        with self._lock:
            sid = strat.get("id") or str(int(_time.time() * 1000))
            strat["id"] = sid
            self.items[sid] = strat
            self._save()
            return strat

    def delete(self, sid):
        with self._lock:
            self.items.pop(sid, None)
            self._save()


class ExperimentStore:
    """백테스트/탐색 실행 결과를 누적 저장한다."""
    def __init__(self, path="experiments.json"):
        self.path = path
        self.items = []
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, list) else []
                except Exception:
                    self.items = []

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self, limit=None):
        with self._lock:
            items = sorted(self.items, key=lambda x: x.get("ts", 0), reverse=True)
            if limit is not None:
                return items[:max(0, int(limit))]
            return items

    def append(self, item):
        with self._lock:
            row = dict(item or {})
            row["id"] = row.get("id") or str(int(_time.time() * 1000))
            row["ts"] = int(row.get("ts") or _time.time())
            self.items.append(row)
            self._save()
            return row

    def clear(self):
        with self._lock:
            self.items = []
            self._save()


class UniverseStore:
    """일자별 주도 후보군 스냅샷 저장소."""
    def __init__(self, path="universes.json"):
        self.path = path
        self.items = []
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, list) else []
                except Exception:
                    self.items = []

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self, limit=None):
        with self._lock:
            items = sorted(self.items, key=lambda x: x.get("ts", 0), reverse=True)
            if limit is not None:
                return items[:max(0, int(limit))]
            return items

    def latest(self):
        items = self.list(limit=1)
        return items[0] if items else None

    def append(self, item):
        with self._lock:
            row = dict(item or {})
            row["id"] = row.get("id") or str(int(_time.time() * 1000))
            row["ts"] = int(row.get("ts") or _time.time())
            self.items.append(row)
            self._save()
            return row


class RecommendationStore:
    """후보군 + 전략 평가 기반 추천 스냅샷 저장소."""
    def __init__(self, path="recommendations.json"):
        self.path = path
        self.items = []
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, list) else []
                except Exception:
                    self.items = []

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self, limit=None):
        with self._lock:
            items = sorted(self.items, key=lambda x: x.get("ts", 0), reverse=True)
            if limit is not None:
                return items[:max(0, int(limit))]
            return items

    def latest(self):
        items = self.list(limit=1)
        return items[0] if items else None

    def append(self, item):
        with self._lock:
            row = dict(item or {})
            row["id"] = row.get("id") or str(int(_time.time() * 1000))
            row["ts"] = int(row.get("ts") or _time.time())
            self.items.append(row)
            self._save()
            return row


class ConditionStore:
    """조건검색식 CRUD 저장소."""
    def __init__(self, path="conditions.json"):
        self.path = path
        self.items = {}
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, dict) else {}
                except Exception:
                    self.items = {}

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self):
        with self._lock:
            return list(self.items.values())

    def get(self, cid):
        with self._lock:
            return self.items.get(cid)

    def upsert(self, cond):
        with self._lock:
            cid = cond.get("id") or str(int(_time.time() * 1000))
            cond["id"] = cid
            self.items[cid] = cond
            self._save()
            return cond

    def delete(self, cid):
        with self._lock:
            self.items.pop(cid, None)
            self._save()


class ConditionRunStore:
    """조건검색 실행 스냅샷 저장소."""
    def __init__(self, path="condition_runs.json"):
        self.path = path
        self.items = []
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, list) else []
                except Exception:
                    self.items = []

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self, limit=None):
        with self._lock:
            items = sorted(self.items, key=lambda x: x.get("ts", 0), reverse=True)
            if limit is not None:
                return items[:max(0, int(limit))]
            return items

    def latest(self):
        items = self.list(limit=1)
        return items[0] if items else None

    def append(self, item):
        with self._lock:
            row = dict(item or {})
            row["id"] = row.get("id") or str(int(_time.time() * 1000))
            row["ts"] = int(row.get("ts") or _time.time())
            self.items.append(row)
            self._save()
            return row


class ConditionValidationStore:
    """조건검색 성과검증 결과 저장소."""
    def __init__(self, path="condition_validations.json"):
        self.path = path
        self.items = []
        self._lock = _threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if _os.path.exists(self.path):
                try:
                    loaded = _json.load(open(self.path, encoding="utf-8"))
                    self.items = loaded if isinstance(loaded, list) else []
                except Exception:
                    self.items = []

    def _save(self):
        with self._lock:
            _json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)

    def list(self, limit=None):
        with self._lock:
            items = sorted(self.items, key=lambda x: x.get("ts", 0), reverse=True)
            if limit is not None:
                return items[:max(0, int(limit))]
            return items

    def latest(self):
        items = self.list(limit=1)
        return items[0] if items else None

    def append(self, item):
        with self._lock:
            row = dict(item or {})
            row["id"] = row.get("id") or str(int(_time.time() * 1000))
            row["ts"] = int(row.get("ts") or _time.time())
            self.items.append(row)
            self._save()
            return row
