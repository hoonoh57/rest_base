#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_simulation.py — 차트 시뮬레이션 대시보드
================================================================================
성공한 기존 원본(app.py, core.py, settings.py)을 보존하면서 별도의 포트(5000)로 구동됩니다.
"""

import os
import json
import time
import threading
import itertools
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core import (Config, RankingEngine, KiwoomREST, deduplicate_times, _ema_series, _sma_series,
                  compute_obv, compute_macd, compute_disparity, compute_jma,
                  compute_supertrend, compute_vwma, SafeEval, jdump)
from settings import (
    Settings,
    StrategyStore,
    ExperimentStore,
    UniverseStore,
    RecommendationStore,
    ConditionStore,
    ConditionRunStore,
    ConditionValidationStore,
    TIMEFRAMES,
    DEFAULT_PARAMS,
)


STRATEGY_PARAM_KEYS = [
    "obv_signal_period",
    "macd_fast",
    "macd_slow",
    "macd_signal",
    "ma_periods",
    "supertrend_period",
    "supertrend_multiplier",
    "jma_length",
    "jma_phase",
    "jma_power",
    "vwma_length",
    "fee_bp",
    "slippage_bp",
]

CONDITION_ROW_SAMPLE = [
    {
        "key": "A",
        "indicator": "box_range_pct",
        "label": "박스권 폭",
        "timeframe": "m5",
        "lookback": 20,
        "operator": "<=",
        "value": 8,
        "params": {},
        "enabled": True,
    },
    {
        "key": "B",
        "indicator": "base_candle",
        "label": "기준봉",
        "timeframe": "m5",
        "lookback": 20,
        "operator": "is_true",
        "value": 1,
        "params": {"volume_ratio_min": 3.0, "body_pct_min": 4.0},
        "enabled": True,
    },
    {
        "key": "C",
        "indicator": "zigzag_turn_up",
        "label": "ZigZag 상승전환",
        "timeframe": "m5",
        "lookback": 0,
        "operator": "is_true",
        "value": 1,
        "params": {},
        "enabled": True,
    },
]


def clone_params(params):
    copied = {}
    for key, value in params.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


def normalize_strategy_params(raw_params):
    normalized = {}
    raw_params = raw_params or {}
    for key in STRATEGY_PARAM_KEYS:
        if key not in raw_params or key not in DEFAULT_PARAMS:
            continue
        default_value = DEFAULT_PARAMS[key]
        try:
            if isinstance(default_value, bool):
                value = raw_params[key]
                normalized[key] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default_value, int):
                normalized[key] = int(float(raw_params[key]))
            elif isinstance(default_value, float):
                normalized[key] = float(raw_params[key])
            elif isinstance(default_value, list):
                values = raw_params[key]
                if not isinstance(values, list):
                    continue
                normalized[key] = [int(float(x)) for x in values[:3]]
            else:
                normalized[key] = raw_params[key]
        except (TypeError, ValueError):
            continue
    return normalized


def normalize_strategy_payload(raw_strategy, fallback_code=""):
    raw_strategy = raw_strategy or {}
    return {
        "id": str(raw_strategy.get("id") or "").strip(),
        "parent_id": str(raw_strategy.get("parent_id") or "").strip(),
        "parent_version": str(raw_strategy.get("parent_version") or "").strip(),
        "name": str(raw_strategy.get("name") or "UntitledStrategy").strip(),
        "code": str(raw_strategy.get("code") or fallback_code or "").strip(),
        "entry_expr": str(raw_strategy.get("entry_expr") or "").strip(),
        "exit_expr": str(raw_strategy.get("exit_expr") or "").strip(),
        "qty": int(float(raw_strategy.get("qty") or 0)),
        "stop_pct": float(raw_strategy.get("stop_pct") or 0),
        "take_pct": float(raw_strategy.get("take_pct") or 0),
        "version": str(raw_strategy.get("version") or "v0.1.0").strip(),
        "stage": str(raw_strategy.get("stage") or "draft").strip().lower(),
        "benchmark_id": str(raw_strategy.get("benchmark_id") or "").strip(),
        "notes": str(raw_strategy.get("notes") or "").strip(),
        "params": normalize_strategy_params(raw_strategy.get("params") or {}),
    }


def normalize_condition_rows(raw_rows):
    rows = raw_rows or []
    if isinstance(rows, str):
        rows = rows.strip()
        if not rows:
            rows = []
        else:
            rows = json.loads(rows)
    if not isinstance(rows, list):
        raise ValueError("condition rows must be a list")
    normalized = []
    for index, row in enumerate(rows):
        item = row or {}
        key = str(item.get("key") or chr(65 + index)).strip().upper()[:4] or f"R{index + 1}"
        normalized.append({
            "key": key,
            "indicator": str(item.get("indicator") or "").strip(),
            "label": str(item.get("label") or key).strip(),
            "timeframe": str(item.get("timeframe") or "m5").strip(),
            "lookback": max(0, int(float(item.get("lookback") or 0))),
            "offset": max(0, int(float(item.get("offset") or 0))),
            "operator": str(item.get("operator") or ">=").strip(),
            "value": item.get("value"),
            "value2": item.get("value2"),
            "unit": str(item.get("unit") or "").strip(),
            "params": item.get("params") if isinstance(item.get("params"), dict) else {},
            "enabled": bool(item.get("enabled", True)),
        })
    return normalized


def normalize_condition_payload(raw_condition):
    raw_condition = raw_condition or {}
    rows = normalize_condition_rows(raw_condition.get("rows") or CONDITION_ROW_SAMPLE)
    expr = str(raw_condition.get("expression") or " and ".join(row["key"] for row in rows if row.get("enabled"))).strip()
    return {
        "id": str(raw_condition.get("id") or "").strip(),
        "parent_id": str(raw_condition.get("parent_id") or "").strip(),
        "parent_version": str(raw_condition.get("parent_version") or "").strip(),
        "name": str(raw_condition.get("name") or "UntitledCondition").strip(),
        "version": str(raw_condition.get("version") or "v0.1.0").strip(),
        "stage": str(raw_condition.get("stage") or "draft").strip().lower(),
        "description": str(raw_condition.get("description") or "").strip(),
        "market": str(raw_condition.get("market") or "KRX").strip(),
        "enabled": bool(raw_condition.get("enabled", True)),
        "search_timeframe": str(raw_condition.get("search_timeframe") or "m5").strip(),
        "search_basis": raw_condition.get("search_basis") if isinstance(raw_condition.get("search_basis"), dict) else {"mode": "point_in_time", "reference_time": "09:10"},
        "universe": raw_condition.get("universe") if isinstance(raw_condition.get("universe"), dict) else {"scope": "manual"},
        "rows": rows,
        "expression": expr,
        "score_model": raw_condition.get("score_model") if isinstance(raw_condition.get("score_model"), dict) else {"type": "matched_count", "weights": {}},
        "notes": str(raw_condition.get("notes") or "").strip(),
    }


def validate_condition_expression(expr, rows):
    expr = str(expr or "").strip()
    if not expr:
        return "condition expression is empty"
    keys = [row.get("key") for row in rows if row.get("enabled")]
    if not keys:
        return "at least one enabled condition row is required"
    try:
        import ast
        ast.parse(expr, mode="eval")
        ctx = {key: True for key in keys}
        SafeEval(ctx, {"abs": abs, "min": min, "max": max}).eval(expr)
        return None
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (offset={exc.offset})"
    except Exception as exc:
        return str(exc)


def normalize_condition_validation_config(raw_config):
    raw_config = raw_config or {}

    def parse_int(name, default, min_value=1, max_value=None):
        try:
            value = int(float(raw_config.get(name, default)))
        except (TypeError, ValueError):
            value = default
        value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    def parse_codes(value):
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [x.strip() for x in str(value or "").split(",") if x.strip()]

    return {
        "search_date": str(raw_config.get("search_date") or "").strip(),
        "search_time": str(raw_config.get("search_time") or "09:10").strip(),
        "timeframe": str(raw_config.get("timeframe") or "m5").strip(),
        "bars": parse_int("bars", 500, 120, 3000),
        "top_n": parse_int("top_n", 15, 1, 100),
        "symbols": parse_codes(raw_config.get("symbols")),
        "source": str(raw_config.get("source") or "latest_universe").strip(),
        "strategy_id": str(raw_config.get("strategy_id") or "").strip(),
    }


def parse_numeric(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compare_condition_value(actual, operator, expected=None, expected2=None):
    op = str(operator or "==").strip().lower()
    if actual is None:
        return False
    if op in ("is_true", "true"):
        return bool(actual)
    if op in ("is_false", "false"):
        return not bool(actual)
    if isinstance(actual, bool):
        left = 1 if actual else 0
        right = 1 if bool(expected) else 0
    else:
        left = actual
        right = expected
    if op in ("=", "=="):
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == "between":
        return parse_numeric(expected2, right) >= left >= right or right <= left <= parse_numeric(expected2, right)
    return False


def validate_expression(expr):
    if not expr:
        return "expression is empty"
    try:
        import ast
        ast.parse(expr.strip(), mode="eval")
        dummy_vars = {
            "close": 1000.0,
            "prev_close": 998.0,
            "ma5": 999.0,
            "prev_ma5": 995.0,
            "ma20": 997.0,
            "prev_ma20": 996.0,
            "ma60": 990.0,
            "prev_ma60": 989.0,
            "obv": 50000.0,
            "prev_obv": 49800.0,
            "obv_signal": 48000.0,
            "prev_obv_signal": 47950.0,
            "macd": 10.0,
            "prev_macd": 8.0,
            "macd_signal": 8.0,
            "prev_macd_signal": 8.5,
            "macd_hist": 2.0,
            "prev_macd_hist": -0.5,
            "disp20": 100.0,
            "prev_disp20": 99.0,
            "supertrend": 995.0,
            "prev_supertrend": 994.0,
            "supertrend_trend": 1,
            "prev_supertrend_trend": -1,
            "jma": 1001.0,
            "prev_jma": 999.0,
            "vwma": 998.0,
            "prev_vwma": 997.0,
            "zigzag_trend": 1,
            "prev_zigzag_trend": -1,
            "zigzag_turn_up": True,
            "zigzag_turn_down": False,
        }
        dummy_funcs = {
            "crossover": lambda a, b: a > b,
            "crossunder": lambda a, b: a < b,
            "abs": abs,
            "min": min,
            "max": max,
        }
        SafeEval(dummy_vars, dummy_funcs).eval(expr)
        return None
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (offset={exc.offset})"
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return str(exc)


def enrich_backtest_result(result):
    trades = result.get("trades", []) or []
    if not trades:
        result["summary"] = {
            "trades": 0,
            "win_rate": 0.0,
            "total_return": result.get("total_return", 0.0),
            "avg_ret": 0.0,
            "sum_ret": 0.0,
            "profit_factor": 0.0,
            "max_win": 0.0,
            "max_loss": 0.0,
        }
        return result

    rets = [float(t.get("ret", t.get("ret_pct", 0.0))) for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    result["summary"] = {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_return": round(result.get("total_return", sum(rets)), 2),
        "avg_ret": round(sum(rets) / len(rets), 2),
        "sum_ret": round(sum(rets), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "max_win": round(max(rets), 2),
        "max_loss": round(min(rets), 2),
    }
    return result


def compute_strategy_score(summary):
    summary = summary or {}
    total_return = float(summary.get("total_return", 0.0) or 0.0)
    win_rate = float(summary.get("win_rate", 0.0) or 0.0)
    trades = float(summary.get("trades", 0) or 0)
    profit_factor = float(summary.get("profit_factor", 0.0) or 0.0)
    avg_ret = float(summary.get("avg_ret", 0.0) or 0.0)
    return round(total_return * 1.8 + win_rate * 0.2 + profit_factor * 5.0 + avg_ret * 1.5 + min(trades, 20) * 0.3, 2)


def build_lab_snapshot(strategies, experiments):
    stage_counts = {"draft": 0, "candidate": 0, "promoted": 0, "archived": 0}
    for item in strategies:
        stage = str(item.get("stage") or "draft").lower()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    top_experiments = sorted(
        experiments,
        key=lambda x: float(((x.get("summary") or {}).get("score")) or -10**9),
        reverse=True,
    )[:5]
    return {
        "strategy_count": len(strategies),
        "experiment_count": len(experiments),
        "stage_counts": stage_counts,
        "top_experiments": top_experiments,
    }


def normalize_universe_config(raw_config):
    raw_config = raw_config or {}

    def parse_int(name, default, min_value=1, max_value=None):
        try:
            value = int(float(raw_config.get(name, default)))
        except (TypeError, ValueError):
            value = default
        value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    return {
        "limit_each": parse_int("limit_each", 30, 5, 200),
        "top_n": parse_int("top_n", 40, 5, 200),
        "include_trade_value": bool(raw_config.get("include_trade_value", True)),
        "include_change_rate": bool(raw_config.get("include_change_rate", True)),
        "analyze_daily": bool(raw_config.get("analyze_daily", True)),
    }


def build_candidate_universe(rest, settings, config=None):
    config = normalize_universe_config(config)
    engine = RankingEngine(rest, settings)
    rows_by_code = {}
    codes = set()

    def get_row(code):
        if code not in rows_by_code:
            rows_by_code[code] = {
                "code": code,
                "name": code,
                "tags": [],
                "leader_score": 0.0,
                "rank_tv": None,
                "rank_cr": None,
                "trade_value": 0.0,
                "chg_rate": 0.0,
            }
        return rows_by_code[code]

    limit_each = config["limit_each"]
    if config.get("include_trade_value", True):
        for rank, row in enumerate(rest.top_trade_value(), start=1):
            if rank > limit_each:
                break
            code = RankingEngine._code(row)
            if not code:
                continue
            codes.add(code)
            item = get_row(code)
            item["name"] = (row.get("stk_nm") or item["name"]).strip()
            item["rank_tv"] = rank
            item["trade_value"] = abs(rest._n(row.get("trde_prica") or row.get("trde_amt")) or item["trade_value"])
            item["leader_score"] += max(0, limit_each - rank + 1) * 1.2
            item["tags"].append("tv_top")

    if config.get("include_change_rate", True):
        for rank, row in enumerate(rest.top_change_rate(), start=1):
            if rank > limit_each:
                break
            code = RankingEngine._code(row)
            if not code:
                continue
            codes.add(code)
            item = get_row(code)
            item["name"] = (row.get("stk_nm") or item["name"]).strip()
            item["rank_cr"] = rank
            item["chg_rate"] = float(rest._n(row.get("flu_rt") or row.get("fluc_rt") or row.get("chg_rate")) or item["chg_rate"])
            item["leader_score"] += max(0, limit_each - rank + 1) * 1.0
            item["tags"].append("chg_top")

    if codes:
        info = rest.watchlist_info(sorted(codes))
        for code, meta in info.items():
            item = get_row(code)
            item["name"] = meta.get("name") or item["name"]
            item["trade_value"] = float(meta.get("trade_value", item["trade_value"]) or item["trade_value"])
            item["chg_rate"] = float(meta.get("chg_rate", item["chg_rate"]) or item["chg_rate"])
            item["price"] = float(meta.get("price", 0.0) or 0.0)

    if config.get("analyze_daily", True):
        engine.refresh_ranks()
        engine.enrich_captured(sorted(codes))
        for code in list(codes):
            try:
                analyzed = engine.analyze(code)
            except Exception:
                analyzed = None
            if not analyzed:
                continue
            item = get_row(code)
            item["daily_score"] = float(analyzed.get("score", 0.0) or 0.0)
            item["state"] = analyzed.get("state", "")
            item["obv_trend"] = analyzed.get("obv_trend", "")
            item["macd_array"] = analyzed.get("macd_array", "")
            item["disp20"] = analyzed.get("disp20")
            item["leader_score"] += item["daily_score"] * 0.35

    rows = list(rows_by_code.values())
    for item in rows:
        item["tags"] = sorted(set(item.get("tags", [])))
        item["leader_score"] = round(float(item.get("leader_score", 0.0) or 0.0), 2)
        item["trade_value"] = round(float(item.get("trade_value", 0.0) or 0.0), 0)
        item["chg_rate"] = round(float(item.get("chg_rate", 0.0) or 0.0), 2)
        if "price" in item:
            item["price"] = round(float(item.get("price", 0.0) or 0.0), 2)

    rows.sort(key=lambda x: (x.get("leader_score", 0.0), x.get("daily_score", 0.0), -(x.get("rank_tv") or 9999)), reverse=True)
    top_n = config["top_n"]
    snapshot = {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": config,
        "summary": {
            "candidate_count": len(rows),
            "top_n": top_n,
            "tag_counts": {
                "tv_top": sum(1 for r in rows if "tv_top" in r.get("tags", [])),
                "chg_top": sum(1 for r in rows if "chg_top" in r.get("tags", [])),
                "dual_top": sum(1 for r in rows if {"tv_top", "chg_top"}.issubset(set(r.get("tags", [])))),
            },
        },
        "rows": rows[:top_n],
    }
    return snapshot


def normalize_recommendation_config(raw_config):
    raw_config = raw_config or {}

    def parse_int(name, default, min_value=1, max_value=None):
        try:
            value = int(float(raw_config.get(name, default)))
        except (TypeError, ValueError):
            value = default
        value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    def parse_list(value, default):
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
        else:
            items = [x.strip() for x in str(value or "").split(",") if x.strip()]
        return items or list(default)

    return {
        "timeframes": parse_list(raw_config.get("timeframes"), ["t360", "t720"]),
        "windows": [max(60, x) for x in [parse_int("window_1", 120), parse_int("window_2", 240), parse_int("window_3", 480)]],
        "bars": parse_int("bars", 1000, 300, 3000),
        "top_n": parse_int("top_n", 10, 1, 100),
        "universe_limit": parse_int("universe_limit", 20, 1, 100),
    }


def select_recommendation_strategies(strategies):
    promoted = [s for s in strategies if str(s.get("stage") or "").lower() == "promoted"]
    if promoted:
        return promoted, "promoted"
    candidates = [s for s in strategies if str(s.get("stage") or "").lower() == "candidate"]
    if candidates:
        return candidates[:5], "candidate"
    drafts = [s for s in strategies if str(s.get("stage") or "").lower() == "draft"]
    return drafts[:3], "draft"


def build_recommendations(rest, settings, strategies, universe_snapshot, config=None):
    config = normalize_recommendation_config(config)
    chosen, basis = select_recommendation_strategies(strategies)
    if not chosen:
        raise ValueError("no saved strategies available")
    if not universe_snapshot or not (universe_snapshot.get("rows") or []):
        raise ValueError("universe snapshot is empty")

    rows = []
    source_rows = (universe_snapshot.get("rows") or [])[:config["universe_limit"]]
    for row in source_rows:
        code = row.get("code")
        if not code:
            continue
        best = None
        fetched = {}
        for tf in config["timeframes"]:
            try:
                candles = rest.ohlcv(code, tf, config["bars"])
            except Exception:
                candles = []
            if not candles:
                continue
            temp_session = SimulationSession()
            temp_session.init_data(candles, tf, "", "")
            temp_session.code = code
            fetched[tf] = temp_session
            end_idx = len(temp_session.times) - 1
            if end_idx <= 60:
                continue
            for strategy in chosen:
                local = dict(strategy)
                local["code"] = code
                for window in config["windows"]:
                    range_start = max(60, end_idx - int(window) + 1)
                    res = run_session_backtest(
                        rest,
                        settings,
                        temp_session,
                        code,
                        tf,
                        local,
                        range_start=range_start,
                        range_end=end_idx,
                        apply_view_params=False,
                    )
                    summary = dict(res.get("summary", {}) or {})
                    strategy_score = compute_strategy_score(summary)
                    candidate = {
                        "code": code,
                        "tf": tf,
                        "window_bars": int(window),
                        "strategy_id": local.get("id"),
                        "strategy_name": local.get("name"),
                        "strategy_version": local.get("version"),
                        "strategy_stage": local.get("stage"),
                        "summary": summary,
                        "strategy_score": strategy_score,
                    }
                    if best is None or candidate["strategy_score"] > best["strategy_score"]:
                        best = candidate
        if best is None:
            continue
        leader_score = float(row.get("leader_score", 0.0) or 0.0)
        total_return = float((best.get("summary") or {}).get("total_return", 0.0) or 0.0)
        win_rate = float((best.get("summary") or {}).get("win_rate", 0.0) or 0.0)
        recommendation_score = round(leader_score * 0.45 + best["strategy_score"] * 0.55, 2)
        rows.append({
            "code": code,
            "name": row.get("name", code),
            "tags": row.get("tags", []),
            "leader_score": leader_score,
            "strategy_score": best["strategy_score"],
            "recommendation_score": recommendation_score,
            "chg_rate": row.get("chg_rate", 0.0),
            "trade_value": row.get("trade_value", 0.0),
            "strategy_id": best["strategy_id"],
            "strategy_name": best["strategy_name"],
            "strategy_version": best["strategy_version"],
            "strategy_stage": best["strategy_stage"],
            "tf": best["tf"],
            "window_bars": best["window_bars"],
            "total_return": round(total_return, 2),
            "win_rate": round(win_rate, 1),
        })
    rows.sort(key=lambda x: (x.get("recommendation_score", 0.0), x.get("strategy_score", 0.0), x.get("leader_score", 0.0)), reverse=True)
    return {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy_basis": basis,
        "strategy_count": len(chosen),
        "config": config,
        "universe_id": universe_snapshot.get("id"),
        "rows": rows[:config["top_n"]],
    }


def strategy_signature(strategy):
    payload = normalize_strategy_payload(strategy, (strategy or {}).get("code", "") if isinstance(strategy, dict) else "")
    signature_payload = {
        "code": payload.get("code", ""),
        "preferred_tf": payload.get("preferred_tf", ""),
        "entry_expr": payload.get("entry_expr", ""),
        "exit_expr": payload.get("exit_expr", ""),
        "qty": payload.get("qty", 0),
        "stop_pct": payload.get("stop_pct", 0),
        "take_pct": payload.get("take_pct", 0),
        "params": payload.get("params", {}),
    }
    raw = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_worker_config(raw_config, fallback_code="", fallback_tf="t360"):
    raw_config = raw_config or {}

    def parse_list(value, default):
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
        else:
            items = [x.strip() for x in str(value or "").split(",") if x.strip()]
        return items or list(default)

    def parse_int_list(value, default):
        parsed = []
        if isinstance(value, list):
            source = value
        else:
            source = str(value or "").split(",")
        for item in source:
            try:
                parsed.append(int(float(str(item).strip())))
            except (TypeError, ValueError):
                continue
        return parsed or list(default)

    def parse_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    return {
        "symbols": parse_list(raw_config.get("symbols") or fallback_code, [fallback_code or "000660"]),
        "timeframes": parse_list(raw_config.get("timeframes") or fallback_tf, [fallback_tf or "t360"]),
        "windows": [max(60, x) for x in parse_int_list(raw_config.get("windows"), [120, 240, 480])],
        "bars": max(300, int(float(raw_config.get("bars") or 1000))),
        "interval_sec": max(15, int(float(raw_config.get("interval_sec") or 300))),
        "limit": max(1, int(float(raw_config.get("limit") or 12))),
        "save_top_n": max(1, min(5, int(float(raw_config.get("save_top_n") or 3)))),
        "auto_candidate": parse_bool(raw_config.get("auto_candidate"), True),
        "run_once": parse_bool(raw_config.get("run_once"), False),
    }


def generate_strategy_variants(base_strategy):
    base = normalize_strategy_payload(base_strategy, base_strategy.get("code", "") if isinstance(base_strategy, dict) else "")
    variants = []
    combos = [
        {"entry_expr": "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend", "exit_expr": "zigzag_turn_down or zigzag_trend < 0 or close < supertrend", "suffix": "zz_gate_st"},
        {"entry_expr": "zigzag_trend > 0 and supertrend_trend > 0 and close > supertrend", "exit_expr": "zigzag_trend < 0 or supertrend_trend < 0 or close < supertrend", "suffix": "zz_st_trend"},
        {"entry_expr": "zigzag_turn_up and close > supertrend", "exit_expr": "zigzag_turn_down or close < supertrend", "suffix": "zz_turn_only"},
        {"entry_expr": "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend and macd > macd_signal", "exit_expr": "zigzag_turn_down or zigzag_trend < 0 or macd < macd_signal", "suffix": "zz_macd"},
        {"entry_expr": "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend and obv > obv_signal", "exit_expr": "zigzag_turn_down or zigzag_trend < 0 or obv < obv_signal", "suffix": "zz_obv"},
        {"entry_expr": "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend and close > vwma", "exit_expr": "zigzag_turn_down or zigzag_trend < 0 or close < vwma", "suffix": "zz_vwma"},
        {"entry_expr": "(zigzag_turn_up or zigzag_trend > 0) and close > supertrend and jma > prev_jma", "exit_expr": "zigzag_turn_down or zigzag_trend < 0 or jma < prev_jma", "suffix": "zz_jma"},
        {"entry_expr": "zigzag_trend > 0 and supertrend_trend > 0 and macd > 0 and close > vwma", "exit_expr": "zigzag_trend < 0 or macd < 0 or close < vwma", "suffix": "zz_vwma_macd"},
    ]
    risk_overrides = [
        {"stop_pct": 0.0, "take_pct": 0.0},
        {"stop_pct": 2.0, "take_pct": 0.0},
        {"stop_pct": 2.5, "take_pct": 5.0},
        {"stop_pct": 3.0, "take_pct": 6.0},
    ]
    qty_choices = [base.get("qty") or 100]
    for combo, risk, qty in itertools.product(combos, risk_overrides, qty_choices):
        item = dict(base)
        item["id"] = ""
        item["parent_id"] = base.get("id") or base.get("parent_id") or ""
        item["parent_version"] = base.get("version") or base.get("parent_version") or ""
        item["benchmark_id"] = base.get("id") or ""
        item["stage"] = "candidate"
        item["entry_expr"] = combo["entry_expr"]
        item["exit_expr"] = combo["exit_expr"]
        item["stop_pct"] = float(risk["stop_pct"])
        item["take_pct"] = float(risk["take_pct"])
        item["qty"] = int(qty)
        item["version"] = str(base.get("version") or "v0.1.0") + "+" + combo["suffix"]
        item["notes"] = (base.get("notes") or "").strip()
        variants.append(item)
    return variants


def filter_backtest_range(result, times, range_start=None, range_end=None):
    if range_start is None and range_end is None:
        return enrich_backtest_result(result)

    if not times:
        return enrich_backtest_result(result)

    start_idx = max(0, int(range_start or 0))
    end_idx = min(len(times) - 1, int(range_end if range_end is not None else len(times) - 1))
    start_time = times[start_idx]
    end_time = times[end_idx]

    trades = [
        t for t in result.get("trades", [])
        if start_time <= t.get("entry_time") <= end_time
    ]
    markers = [
        m for m in result.get("markers", [])
        if start_time <= m.get("time") <= end_time
    ]
    filtered = dict(result)
    filtered["trades"] = trades
    filtered["markers"] = markers
    filtered["n_trades"] = len(trades)

    equity = 1.0
    for trade in trades:
        equity *= (1.0 + float(trade.get("ret", 0.0)) / 100.0)
    filtered["total_return"] = round((equity - 1.0) * 100.0, 2)
    wins = [t for t in trades if float(t.get("ret", 0.0)) > 0]
    filtered["win_rate"] = round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0
    return enrich_backtest_result(filtered)


def resolve_validation_symbols(config, latest_universe=None, fallback_code=""):
    config = config or {}
    symbols = [str(x).strip() for x in (config.get("symbols") or []) if str(x).strip()]
    if symbols:
        return symbols
    if str(config.get("source") or "") == "latest_universe" and latest_universe:
        rows = latest_universe.get("rows") or []
        symbols = [str(row.get("code") or "").strip() for row in rows if str(row.get("code") or "").strip()]
        if symbols:
            return symbols
    if fallback_code:
        return [fallback_code]
    return []


def find_condition_cutoff(candles, tf, search_date="", search_time=""):
    if not candles:
        return None, None
    intraday = tf.startswith("m") or tf.startswith("t") or tf.isdigit()
    if not search_date:
        return max(60, len(candles) - 1), len(candles) - 1
    target = search_date.replace("-", "")
    if intraday and search_time:
        target += search_time.replace(":", "")
    cutoff_idx = None
    for idx, row in enumerate(candles):
        stamp = str(row.get("date") or "")
        if intraday:
            if stamp[:12] <= target:
                cutoff_idx = idx
            elif cutoff_idx is not None:
                break
        else:
            if stamp[:8] <= target[:8]:
                cutoff_idx = idx
            elif cutoff_idx is not None:
                break
    if cutoff_idx is None:
        return None, None
    cutoff_idx = max(60, cutoff_idx)
    prefix = str(candles[cutoff_idx].get("date") or "")[:8]
    end_idx = cutoff_idx
    for idx in range(cutoff_idx, len(candles)):
        if str(candles[idx].get("date") or "")[:8] != prefix:
            break
        end_idx = idx
    return cutoff_idx, end_idx


def compute_condition_row_actual(row, session, idx, params):
    indicator = str(row.get("indicator") or "").strip()
    lookback = max(0, int(row.get("lookback") or 0))
    offset = max(0, int(row.get("offset") or 0))
    cur = idx - offset
    if cur < 1 or cur >= len(session.closes):
        return None
    closes = session.closes[:cur + 1]
    highs = session.highs[:cur + 1]
    lows = session.lows[:cur + 1]
    vols = session.vols[:cur + 1]
    candle = session.candles[cur]
    row_params = row.get("params") or {}
    if indicator == "price_change_rate":
        ref = max(0, cur - max(1, lookback))
        base = session.closes[ref]
        if not base:
            return None
        return round((session.closes[cur] - base) / base * 100.0, 2)
    if indicator == "trade_value":
        return round(session.closes[cur] * session.vols[cur] / 1000000.0, 2)
    if indicator == "volume_ratio":
        period = max(2, lookback or int(row_params.get("period") or 20))
        start = max(0, cur - period)
        hist = session.vols[start:cur]
        avg = float(np.mean(hist)) if hist else 0.0
        return round(session.vols[cur] / avg, 2) if avg > 0 else None
    if indicator == "price_above_ma":
        period = max(2, int(row_params.get("period") or row.get("value") or 20))
        if cur + 1 < period:
            return None
        ma = float(np.mean(session.closes[cur + 1 - period:cur + 1]))
        return round((session.closes[cur] - ma) / ma * 100.0, 2) if ma else None
    if indicator == "ma_cross_up":
        fast = max(2, int(row_params.get("fast") or 5))
        slow = max(fast + 1, int(row_params.get("slow") or 20))
        if cur < slow:
            return None
        prev_fast = float(np.mean(session.closes[cur - fast:cur]))
        prev_slow = float(np.mean(session.closes[cur - slow:cur]))
        now_fast = float(np.mean(session.closes[cur + 1 - fast:cur + 1]))
        now_slow = float(np.mean(session.closes[cur + 1 - slow:cur + 1]))
        return now_fast > now_slow and prev_fast <= prev_slow
    if indicator == "box_range_pct":
        period = max(3, lookback or int(row_params.get("period") or 20))
        start = max(0, cur + 1 - period)
        hi = max(session.highs[start:cur + 1])
        lo = min(session.lows[start:cur + 1])
        return round((hi - lo) / lo * 100.0, 2) if lo else None
    if indicator == "breakout_high":
        period = max(2, lookback or int(row_params.get("period") or 20))
        start = max(0, cur - period)
        ref_high = max(session.highs[start:cur]) if cur > start else session.highs[cur]
        return round((session.closes[cur] - ref_high) / ref_high * 100.0, 2) if ref_high else None
    if indicator == "base_candle":
        body_pct = abs(candle["close"] - candle["open"]) / candle["open"] * 100.0 if candle["open"] else 0.0
        period = max(2, lookback or int(row_params.get("period") or 20))
        start = max(0, cur - period)
        avg_vol = float(np.mean(session.vols[start:cur])) if cur > start else 0.0
        vol_ratio = session.vols[cur] / avg_vol if avg_vol > 0 else 0.0
        return body_pct >= float(row_params.get("body_pct_min", 4.0)) and vol_ratio >= float(row_params.get("volume_ratio_min", 3.0))
    if indicator == "zigzag_trend" or indicator == "zigzag_turn_up":
        pivots, unconfirmed = calculate_non_repaint_zigzag(highs, lows, closes, dev_pct=float(row_params.get("dev_pct", 5.0)))
        trend = 0
        if unconfirmed:
            trend = 1 if unconfirmed[2] == "high" else -1
        if indicator == "zigzag_trend":
            return trend
        if cur < 2:
            return False
        prev_pivots, prev_unconfirmed = calculate_non_repaint_zigzag(highs[:-1], lows[:-1], closes[:-1], dev_pct=float(row_params.get("dev_pct", 5.0)))
        prev_trend = 1 if prev_unconfirmed and prev_unconfirmed[2] == "high" else (-1 if prev_unconfirmed else 0)
        return trend > 0 and prev_trend <= 0
    if indicator == "supertrend_state":
        supertrend, trend_up = compute_supertrend(highs, lows, closes, int(row_params.get("period") or params["supertrend_period"]), float(row_params.get("multiplier") or params["supertrend_multiplier"]))
        return 1 if len(trend_up) and trend_up[-1] else -1
    if indicator == "vwma_position":
        length = int(row_params.get("length") or params["vwma_length"])
        vwma = compute_vwma(closes, vols, length)
        if len(vwma) == 0 or np.isnan(vwma[-1]):
            return None
        return round((closes[-1] - float(vwma[-1])) / float(vwma[-1]) * 100.0, 2) if vwma[-1] else None
    if indicator == "jma_trend":
        _, trend = compute_jma(closes, int(row_params.get("length") or params["jma_length"]), float(row_params.get("phase") or params["jma_phase"]), int(row_params.get("power") or params["jma_power"]))
        return int(trend[-1]) if len(trend) else 0
    if indicator == "obv_cross_up":
        obv = compute_obv(closes, vols)
        sig = _ema_series(obv, int(row_params.get("signal_period") or params["obv_signal_period"]))
        if len(obv) < 2 or len(sig) < 2:
            return False
        return obv[-1] > sig[-1] and obv[-2] <= sig[-2]
    if indicator == "macd_cross_up":
        macd = compute_macd(closes, int(row_params.get("fast") or params["macd_fast"]), int(row_params.get("slow") or params["macd_slow"]), int(row_params.get("signal") or params["macd_signal"]), full=True)
        if not macd or len(macd[0]) < 2:
            return False
        macd_line, sig_line, _ = macd
        return macd_line[-1] > sig_line[-1] and macd_line[-2] <= sig_line[-2]
    return None


def evaluate_condition_rows(session, idx, condition, settings):
    params = clone_params(settings.params)
    values = {}
    matched_keys = []
    for row in condition.get("rows", []):
        if not row.get("enabled", True):
            continue
        actual = compute_condition_row_actual(row, session, idx, params)
        passed = compare_condition_value(actual, row.get("operator"), row.get("value"), row.get("value2"))
        values[row["key"]] = bool(passed)
        if passed:
            matched_keys.append(row["key"])
    expr = condition.get("expression") or " and ".join(values.keys())
    matched = bool(SafeEval(values, {"abs": abs, "min": min, "max": max}).eval(expr)) if values else False
    score = 0.0
    if matched_keys:
        score = round(len(matched_keys) * 10.0 + (5.0 if matched else 0.0), 2)
    return {
        "matched": matched,
        "matched_keys": matched_keys,
        "score": score,
        "values": values,
    }


def build_condition_candidates(rest, settings, condition, config, latest_universe=None, fallback_code=""):
    config = normalize_condition_validation_config(config)
    condition = normalize_condition_payload(condition)
    symbols = resolve_validation_symbols(config, latest_universe=latest_universe, fallback_code=fallback_code)
    if not symbols:
        raise ValueError("validation symbols are empty")
    rows = []
    for code in symbols:
        try:
            candles = rest.ohlcv(code, config["timeframe"], config["bars"])
        except Exception:
            candles = []
        if not candles:
            continue
        cutoff_idx, day_end_idx = find_condition_cutoff(candles, config["timeframe"], config["search_date"], config["search_time"])
        if cutoff_idx is None or day_end_idx is None:
            continue
        temp_session = SimulationSession()
        temp_session.init_data(candles, config["timeframe"], "", "")
        temp_session.code = code
        temp_session.tf = config["timeframe"]
        evaluation = evaluate_condition_rows(temp_session, cutoff_idx, condition, settings)
        if not evaluation["matched"]:
            continue
        close_price = float(temp_session.closes[cutoff_idx])
        rows.append({
            "code": code,
            "name": code,
            "score": evaluation["score"],
            "matched_keys": evaluation["matched_keys"],
            "cutoff_idx": cutoff_idx,
            "cutoff_price": round(close_price, 2),
            "search_date": config["search_date"],
            "search_time": config["search_time"],
            "timeframe": config["timeframe"],
            "bars": config["bars"],
            "day_end_idx": day_end_idx,
        })
    try:
        meta = rest.watchlist_info([row["code"] for row in rows]) if rows else {}
    except Exception:
        meta = {}
    for row in rows:
        info = meta.get(row["code"], {})
        row["name"] = info.get("name") or row["name"]
    rows.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "condition_id": condition.get("id"),
        "condition_version": condition.get("version"),
        "config": config,
        "rows": rows[:config["top_n"]],
        "summary": {
            "candidate_count": len(rows),
            "top_n": config["top_n"],
            "matched_count": len(rows),
        },
    }


def run_condition_validation(rest, settings, condition, strategy, config, latest_universe=None, fallback_code=""):
    strategy = normalize_strategy_payload(strategy or {}, fallback_code)
    snapshot = build_condition_candidates(rest, settings, condition, config, latest_universe=latest_universe, fallback_code=fallback_code)
    rows = []
    strategy_returns = []
    max_runups = []
    for item in snapshot.get("rows", []):
        code = item.get("code")
        try:
            candles = rest.ohlcv(code, item.get("timeframe") or config.get("timeframe") or "m5", int(item.get("bars") or config.get("bars") or 500))
        except Exception:
            candles = []
        if not candles:
            continue
        cutoff_idx, day_end_idx = find_condition_cutoff(candles, item.get("timeframe") or config.get("timeframe") or "m5", item.get("search_date") or config.get("search_date"), item.get("search_time") or config.get("search_time"))
        if cutoff_idx is None or day_end_idx is None:
            continue
        temp_session = SimulationSession()
        temp_session.init_data(candles, item.get("timeframe") or config.get("timeframe") or "m5", "", "")
        temp_session.code = code
        temp_session.tf = item.get("timeframe") or config.get("timeframe") or "m5"
        cutoff_price = float(temp_session.closes[cutoff_idx])
        future_high = max(temp_session.highs[cutoff_idx:day_end_idx + 1])
        future_low = min(temp_session.lows[cutoff_idx:day_end_idx + 1])
        max_runup = round((future_high - cutoff_price) / cutoff_price * 100.0, 2) if cutoff_price else 0.0
        max_drawdown = round((future_low - cutoff_price) / cutoff_price * 100.0, 2) if cutoff_price else 0.0
        strategy_return = 0.0
        strategy_summary = {"trades": 0, "win_rate": 0.0, "total_return": 0.0}
        if strategy.get("entry_expr") and strategy.get("exit_expr"):
            local_strategy = dict(strategy)
            local_strategy["code"] = code
            result = run_session_backtest(rest, settings, temp_session, code, temp_session.tf, local_strategy, range_start=cutoff_idx, range_end=day_end_idx, apply_view_params=False)
            strategy_summary = dict(result.get("summary", {}) or {})
            strategy_return = float(strategy_summary.get("total_return", 0.0) or 0.0)
        rows.append({
            "code": code,
            "name": item.get("name") or code,
            "score": item.get("score", 0.0),
            "matched_keys": item.get("matched_keys", []),
            "cutoff_price": round(cutoff_price, 2),
            "max_runup_pct": max_runup,
            "max_drawdown_pct": max_drawdown,
            "strategy_return_pct": round(strategy_return, 2),
            "strategy_win_rate": round(float(strategy_summary.get("win_rate", 0.0) or 0.0), 1),
            "strategy_trades": int(strategy_summary.get("trades", 0) or 0),
        })
        strategy_returns.append(strategy_return)
        max_runups.append(max_runup)
    avg_runup = round(float(np.mean(max_runups)), 2) if max_runups else 0.0
    avg_strategy = round(float(np.mean(strategy_returns)), 2) if strategy_returns else 0.0
    median_runup = round(float(np.median(max_runups)), 2) if max_runups else 0.0
    median_strategy = round(float(np.median(strategy_returns)), 2) if strategy_returns else 0.0
    win_rate = round(sum(1 for x in strategy_returns if x > 0) / len(strategy_returns) * 100.0, 1) if strategy_returns else 0.0
    return {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "condition_id": condition.get("id"),
        "condition_version": condition.get("version"),
        "search_date": config.get("search_date"),
        "search_time": config.get("search_time"),
        "strategy_id": strategy.get("id", ""),
        "strategy_version": strategy.get("version", ""),
        "run_snapshot": snapshot,
        "summary": {
            "candidate_count": len(rows),
            "avg_max_runup_pct": avg_runup,
            "median_max_runup_pct": median_runup,
            "avg_strategy_return_pct": avg_strategy,
            "median_strategy_return_pct": median_strategy,
            "strategy_positive_rate": win_rate,
        },
        "rows": rows,
    }


def get_snapshot_by_date(items, date_text):
    date_text = str(date_text or "").strip()
    if not date_text:
        return None
    matched = []
    for item in items or []:
        built_at = str(item.get("built_at") or "")
        if built_at.startswith(date_text):
            matched.append(item)
    if not matched:
        return None
    matched.sort(key=lambda x: str(x.get("built_at") or ""))
    return matched[-1]


def analyze_daily_factors_at(rest, settings, code, target_date, max_bars=120):
    raw = rest.ohlcv(code, "D", max_bars)
    if not raw:
        return None
    target = str(target_date or "").replace("-", "")
    rows = []
    for row in raw:
        stamp = str(row.get("date") or "")[:8]
        if not target or stamp <= target:
            rows.append(row)
    if len(rows) < 30:
        return None
    closes = np.asarray([float(r["close"]) for r in rows], dtype=float)
    highs = np.asarray([float(r["high"]) for r in rows], dtype=float)
    lows = np.asarray([float(r["low"]) for r in rows], dtype=float)
    vols = np.asarray([float(r["volume"]) for r in rows], dtype=float)
    params = settings.params
    obv = compute_obv(closes, vols)
    obv_sig = _ema_series(obv, int(params["obv_signal_period"]))
    macd = compute_macd(closes, int(params["macd_fast"]), int(params["macd_slow"]), int(params["macd_signal"]))
    disp = compute_disparity(closes, params["ma_periods"])
    jma_vals, jma_trend = compute_jma(closes, int(params["jma_length"]), float(params["jma_phase"]), int(params["jma_power"]))
    supertrend, trend_up = compute_supertrend(highs, lows, closes, int(params["supertrend_period"]), float(params["supertrend_multiplier"]))
    vwma = compute_vwma(closes, vols, int(params["vwma_length"]))
    period = 20
    avg_vol = float(np.mean(vols[-period-1:-1])) if len(vols) > period else float(np.mean(vols[:-1])) if len(vols) > 1 else 0.0
    volume_ratio = float(vols[-1] / avg_vol) if avg_vol > 0 else 0.0
    recent_high = float(np.max(highs[-period:])) if len(highs) >= period else float(np.max(highs))
    recent_low = float(np.min(lows[-period:])) if len(lows) >= period else float(np.min(lows))
    box_range_pct = (recent_high - recent_low) / recent_low * 100.0 if recent_low else 0.0
    body_pct = abs(float(rows[-1]["close"]) - float(rows[-1]["open"])) / float(rows[-1]["open"]) * 100.0 if float(rows[-1]["open"]) else 0.0
    chg_rate = (closes[-1] - closes[-2]) / closes[-2] * 100.0 if len(closes) >= 2 and closes[-2] else 0.0
    trade_value = closes[-1] * vols[-1] / 1000000.0
    if macd:
        macd_state = str(macd.get("array") or "-")
    else:
        macd_state = "-"
    return {
        "code": code,
        "date": target_date,
        "close": round(float(closes[-1]), 2),
        "chg_rate": round(float(chg_rate), 2),
        "trade_value": round(float(trade_value), 2),
        "obv_trend": "상승" if obv[-1] > obv_sig[-1] else "하락",
        "macd_array": macd_state,
        "disp20": round(float(disp.get(params["ma_periods"][1]) or 0.0), 2),
        "volume_ratio": round(float(volume_ratio), 2),
        "box_range_pct": round(float(box_range_pct), 2),
        "body_pct": round(float(body_pct), 2),
        "supertrend_trend": 1 if len(trend_up) and trend_up[-1] else -1,
        "jma_trend": int(jma_trend[-1]) if len(jma_trend) else 0,
        "vwma_gap_pct": round((float(closes[-1]) - float(vwma[-1])) / float(vwma[-1]) * 100.0, 2) if len(vwma) and not np.isnan(vwma[-1]) and vwma[-1] else 0.0,
    }


def build_top_riser_profile(rest, settings, source_snapshot, source_date, top_n=10):
    rows = sorted(source_snapshot.get("rows") or [], key=lambda x: float(x.get("chg_rate", 0.0) or 0.0), reverse=True)[:max(1, int(top_n))]
    factors = []
    for row in rows:
        analyzed = analyze_daily_factors_at(rest, settings, row.get("code"), source_date)
        if analyzed:
            analyzed["name"] = row.get("name") or row.get("code")
            factors.append(analyzed)
    if not factors:
        raise ValueError("no source top riser factors available")
    obv_up_rate = sum(1 for x in factors if x.get("obv_trend") == "상승") / len(factors)
    macd_counts = {}
    for item in factors:
        macd_counts[item.get("macd_array") or "-"] = macd_counts.get(item.get("macd_array") or "-", 0) + 1
    macd_majority = sorted(macd_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
    return {
        "source_date": source_date,
        "top_n": len(factors),
        "source_rows": factors,
        "summary": {
            "avg_chg_rate": round(float(np.mean([x["chg_rate"] for x in factors])), 2),
            "avg_trade_value": round(float(np.mean([x["trade_value"] for x in factors])), 2),
            "avg_volume_ratio": round(float(np.mean([x["volume_ratio"] for x in factors])), 2),
            "avg_box_range_pct": round(float(np.mean([x["box_range_pct"] for x in factors])), 2),
            "avg_disp20": round(float(np.mean([x["disp20"] for x in factors])), 2),
            "obv_up_rate": round(obv_up_rate * 100.0, 1),
            "macd_majority": macd_majority,
            "supertrend_up_rate": round(sum(1 for x in factors if x.get("supertrend_trend", 0) > 0) / len(factors) * 100.0, 1),
            "jma_up_rate": round(sum(1 for x in factors if x.get("jma_trend", 0) > 0) / len(factors) * 100.0, 1),
        },
    }


def score_top_riser_similarity(factors, profile):
    summary = profile.get("summary") or {}
    score = 0.0
    reasons = []
    if summary.get("obv_up_rate", 0.0) >= 60.0 and factors.get("obv_trend") == "상승":
        score += 20.0
        reasons.append("obv_up")
    if factors.get("macd_array") == summary.get("macd_majority"):
        score += 20.0
        reasons.append("macd_match")
    if summary.get("supertrend_up_rate", 0.0) >= 60.0 and factors.get("supertrend_trend", 0) > 0:
        score += 15.0
        reasons.append("supertrend_up")
    if summary.get("jma_up_rate", 0.0) >= 60.0 and factors.get("jma_trend", 0) > 0:
        score += 10.0
        reasons.append("jma_up")
    avg_disp20 = float(summary.get("avg_disp20", 100.0) or 100.0)
    disp_gap = abs(float(factors.get("disp20", 0.0) or 0.0) - avg_disp20)
    score += max(0.0, 15.0 - disp_gap * 0.6)
    if disp_gap <= 8:
        reasons.append("disp20_near")
    avg_vol_ratio = float(summary.get("avg_volume_ratio", 1.0) or 1.0)
    vol_gap = abs(float(factors.get("volume_ratio", 0.0) or 0.0) - avg_vol_ratio)
    score += max(0.0, 10.0 - vol_gap * 2.0)
    if vol_gap <= 2:
        reasons.append("volume_near")
    avg_box = float(summary.get("avg_box_range_pct", 10.0) or 10.0)
    box_gap = abs(float(factors.get("box_range_pct", 0.0) or 0.0) - avg_box)
    score += max(0.0, 10.0 - box_gap * 0.5)
    if box_gap <= 6:
        reasons.append("box_near")
    return round(score, 2), reasons


def run_top_riser_study(rest, settings, universe_items, source_date, target_date, top_n=10, candidate_limit=20, symbols=None):
    source_snapshot = get_snapshot_by_date(universe_items, source_date)
    if not source_snapshot:
        raise ValueError("source universe snapshot not found for date")
    profile = build_top_riser_profile(rest, settings, source_snapshot, source_date, top_n=top_n)
    target_snapshot = get_snapshot_by_date(universe_items, target_date)
    candidate_symbols = [str(x).strip() for x in (symbols or []) if str(x).strip()]
    if not candidate_symbols:
        if not target_snapshot:
            raise ValueError("target universe snapshot not found for date; provide symbols or build universe on target date")
        candidate_symbols = [str(row.get("code") or "").strip() for row in (target_snapshot.get("rows") or []) if str(row.get("code") or "").strip()]
    rows = []
    try:
        info = rest.watchlist_info(candidate_symbols)
    except Exception:
        info = {}
    for code in candidate_symbols:
        factors = analyze_daily_factors_at(rest, settings, code, target_date)
        if not factors:
            continue
        sim_score, reasons = score_top_riser_similarity(factors, profile)
        meta = info.get(code, {})
        rows.append({
            "code": code,
            "name": meta.get("name") or code,
            "similarity_score": sim_score,
            "matched_reasons": reasons,
            "chg_rate": factors.get("chg_rate", 0.0),
            "trade_value": factors.get("trade_value", 0.0),
            "obv_trend": factors.get("obv_trend", ""),
            "macd_array": factors.get("macd_array", ""),
            "disp20": factors.get("disp20", 0.0),
            "volume_ratio": factors.get("volume_ratio", 0.0),
            "box_range_pct": factors.get("box_range_pct", 0.0),
        })
    rows.sort(key=lambda x: (x.get("similarity_score", 0.0), x.get("chg_rate", 0.0)), reverse=True)
    selected = rows[:max(1, int(candidate_limit))]
    selected_avg_chg = round(float(np.mean([x.get("chg_rate", 0.0) for x in selected])), 2) if selected else 0.0
    return {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_date": source_date,
        "target_date": target_date,
        "profile": profile,
        "summary": {
            "source_avg_chg_rate": profile.get("summary", {}).get("avg_chg_rate", 0.0),
            "selected_avg_chg_rate": selected_avg_chg,
            "selected_count": len(selected),
            "candidate_count": len(rows),
        },
        "rows": selected,
    }


# ═══════════════════════════════════════════════════════════════
#  Non-Repainting Zigzag 및 프랙탈 추세선 알고리즘
# ═══════════════════════════════════════════════════════════════

def calculate_non_repaint_zigzag(highs, lows, closes, dev_pct=5.0, **kwargs):
    """
    Standard percentage ZigZag (Kiwoom 0601 style, e.g. 5% deviation).
    Rule: track the running extreme; when price reverses >= dev_pct% from that
    extreme, the extreme becomes a confirmed pivot and direction flips.
    Confirmed pivots never change (no repaint). The final leg from the last
    confirmed pivot to the current running extreme is returned as 'unconfirmed'.
    Extra kwargs (depth/atr_*) are accepted and ignored for API compatibility.
    """
    n = len(highs)
    if n < 2:
        return [], None

    r = dev_pct / 100.0
    pivots = []

    # establish initial direction from bar 0 using first decisive move
    direction = 0           # +1 = looking for HIGH, -1 = looking for LOW
    ext_idx, ext_val = 0, highs[0]   # provisional
    i = 1
    while i < n:
        up_move = (highs[i] - lows[0]) / lows[0] if lows[0] else 0.0
        dn_move = (highs[0] - lows[i]) / highs[0] if highs[0] else 0.0
        if up_move >= r and up_move >= dn_move:
            direction = 1
            ext_idx, ext_val = i, highs[i]
            pivots.append((0, float(lows[0]), "low"))
            break
        elif dn_move >= r:
            direction = -1
            ext_idx, ext_val = i, lows[i]
            pivots.append((0, float(highs[0]), "high"))
            break
        i += 1

    if direction == 0:
        return [], None

    for j in range(i + 1, n):
        if direction == 1:
            # extending an up-leg: track higher highs
            if highs[j] >= ext_val:
                ext_val, ext_idx = highs[j], j
            # reversal down >= dev_pct% from the tracked high -> confirm HIGH pivot
            elif (ext_val - lows[j]) / ext_val >= r:
                pivots.append((ext_idx, float(ext_val), "high"))
                direction = -1
                ext_val, ext_idx = lows[j], j
        else:
            if lows[j] <= ext_val:
                ext_val, ext_idx = lows[j], j
            elif (highs[j] - ext_val) / ext_val >= r:
                pivots.append((ext_idx, float(ext_val), "low"))
                direction = 1
                ext_val, ext_idx = highs[j], j

    unconfirmed = (ext_idx, float(ext_val), "high" if direction == 1 else "low")
    return pivots, unconfirmed


def calculate_fractal_trendlines(highs, lows, closes, times, current_idx):
    n = current_idx + 1
    up_fractals = []
    dn_fractals = []
    
    for i in range(2, n - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            up_fractals.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            dn_fractals.append((i, lows[i]))
            
    res = {"up_line": [], "dn_line": [], "up_anchors": [], "dn_anchors": []}
    
    if len(up_fractals) >= 2:
        idx2, val2 = up_fractals[-2]
        idx1, val1 = up_fractals[-1]
        slope = (val1 - val2) / (idx1 - idx2)
        line_points = []
        for idx in range(idx2, current_idx + 1):
            val = val2 + slope * (idx - idx2)
            line_points.append({"time": times[idx], "value": val})
        res["up_line"] = line_points
        res["up_anchors"] = [{"time": times[idx2], "value": val2}, {"time": times[idx1], "value": val1}]
        
    if len(dn_fractals) >= 2:
        idx2, val2 = dn_fractals[-2]
        idx1, val1 = dn_fractals[-1]
        slope = (val1 - val2) / (idx1 - idx2)
        line_points = []
        for idx in range(idx2, current_idx + 1):
            val = val2 + slope * (idx - idx2)
            line_points.append({"time": times[idx], "value": val})
        res["dn_line"] = line_points
        res["dn_anchors"] = [{"time": times[idx2], "value": val2}, {"time": times[idx1], "value": val1}]
        
    return res


def compute_linear_regression_channel(closes, times, current_idx, period=50, std_dev_mult=2.0):
    if current_idx + 1 < period:
        return {}
    start_idx = current_idx - period + 1
    x = np.arange(period)
    y = np.array(closes[start_idx:current_idx+1], dtype=float)
    
    slope, intercept = np.polyfit(x, y, 1)
    reg_line = slope * x + intercept
    residuals = y - reg_line
    std_dev = np.std(residuals)
    
    line_points = []
    upper_points = []
    lower_points = []
    for i in range(period):
        idx = start_idx + i
        val = reg_line[i]
        line_points.append({"time": times[idx], "value": val})
        upper_points.append({"time": times[idx], "value": val + std_dev_mult * std_dev})
        lower_points.append({"time": times[idx], "value": val - std_dev_mult * std_dev})
        
    return {"center": line_points, "upper": upper_points, "lower": lower_points}


# ═══════════════════════════════════════════════════════════════
#  시뮬레이션 데이터 관리
# ═══════════════════════════════════════════════════════════════

class SimulationSession:
    def __init__(self):
        self.code = ""
        self.tf = ""
        self.candles = []
        self.times = []
        self.closes = []
        self.highs = []
        self.lows = []
        self.vols = []
        self.view_params = clone_params(DEFAULT_PARAMS)
                # [안전망] time 오름차순 보장 (lightweight-charts 'YM' 파싱오류 방지)
        if self.times and isinstance(self.times[0], int):
            order = sorted(range(len(self.times)), key=lambda j: self.times[j])
            self.candles = [self.candles[j] for j in order]
            self.times   = [self.times[j]   for j in order]
            self.closes  = [self.closes[j]  for j in order]
            self.highs   = [self.highs[j]   for j in order]
            self.lows    = [self.lows[j]    for j in order]
            self.vols    = [self.vols[j]    for j in order]
            for j in range(1, len(self.times)):
                if self.times[j] <= self.times[j-1]:
                    self.times[j] = self.times[j-1] + 1
        self.start_idx = 60

    def init_data(self, ohlcv_data, tf, target_date="", target_time=""):
        self.candles = ohlcv_data
        self.tf = tf
        self.view_params = clone_params(DEFAULT_PARAMS)
        
        # 원본 app.py의 분봉 판별 규칙 적용 (숫자 타임프레임 대응)
        intraday = tf.isdigit() or tf.startswith("m") or tf.startswith("t")
        raw_times = [r["date"] for r in ohlcv_data]
        
        self.times = [self._fmt_time(t, intraday) for t in raw_times]
        self.times = deduplicate_times(self.times, intraday)
        
        self.closes = [r["close"] for r in ohlcv_data]
        self.highs = [r["high"] for r in ohlcv_data]
        self.lows = [r["low"] for r in ohlcv_data]
        self.vols = [r["volume"] for r in ohlcv_data]
        
        self.start_idx = 60
        if target_date:
            target_str = target_date.replace("-", "")
            if target_time:
                target_str += target_time.replace(":", "")
            for i, c in enumerate(ohlcv_data):
                if str(c["date"]) >= target_str:
                    self.start_idx = max(60, i)
                    break

    def get_slice(self, current_idx, p=None):
        p = p or self.view_params
        k = current_idx
        times = self.times
        closes = self.closes
        highs = self.highs
        lows = self.lows
        vols = self.vols
        
        candles_slice = []
        vols_slice = []
        for i in range(k + 1):
            candles_slice.append({
                "time": times[i], "open": self.candles[i]["open"], "high": highs[i],
                "low": lows[i], "close": closes[i]
            })
            clr = "rgba(38,166,154,0.5)" if closes[i] >= self.candles[i]["open"] else "rgba(239,83,80,0.5)"
            vols_slice.append({"time": times[i], "value": vols[i], "color": clr})
            
        ma_lines = {}
        for pr in p["ma_periods"]:
            arr = []
            for i in range(k + 1):
                if i + 1 >= pr:
                    arr.append({"time": times[i], "value": round(float(np.mean(closes[i+1-pr:i+1])), 2)})
            ma_lines[pr] = arr
            
        obv = compute_obv(closes[:k+1], vols[:k+1])
        obv_sig = _ema_series(obv, p["obv_signal_period"])
        obv_line = [{"time": times[i], "value": float(obv[i])} for i in range(len(obv))]
        obv_sig_line = [{"time": times[i], "value": float(obv_sig[i])} for i in range(len(obv_sig))]
        
        ema12 = _ema_series(closes[:k+1], p["macd_fast"])
        ema26 = _ema_series(closes[:k+1], p["macd_slow"])
        macd_line = ema12 - ema26
        macd_sig = _ema_series(macd_line, p["macd_signal"])
        macd_hist = macd_line - macd_sig
        
        macd_points = [{"time": times[i], "value": float(macd_line[i])} for i in range(len(macd_line))]
        macd_sig_points = [{"time": times[i], "value": float(macd_sig[i])} for i in range(len(macd_sig))]
        macd_hist_points = [{"time": times[i], "value": float(macd_hist[i]),
                             "color": ("rgba(38,166,154,0.6)" if macd_hist[i] >= 0 else "rgba(239,83,80,0.6)")}
                            for i in range(len(macd_hist))]

        jma, jma_trend = compute_jma(closes[:k+1], p["jma_length"], p["jma_phase"], p["jma_power"])
        jma_line = []
        for i in range(len(jma)):
            if not np.isnan(jma[i]):
                jma_line.append({
                    "time": times[i], "value": float(jma[i]),
                    "color": "#00e676" if jma_trend[i] >= 0 else "#ff6d00"
                })

        supertrend, trend_up = compute_supertrend(highs[:k+1], lows[:k+1], closes[:k+1],
                                                  p["supertrend_period"], p["supertrend_multiplier"])
        supertrend_line = []
        for i in range(len(supertrend)):
            if not np.isnan(supertrend[i]):
                supertrend_line.append({
                    "time": times[i], "value": float(supertrend[i]),
                    "color": "#66d28a" if trend_up[i] else "#ff7a5c"
                })

        vwma = compute_vwma(closes[:k+1], vols[:k+1], p["vwma_length"])
        vwma_line = []
        for i in range(len(vwma)):
            if not np.isnan(vwma[i]):
                vwma_line.append({"time": times[i], "value": float(vwma[i])})

        zigzag_pivots, unconfirmed = calculate_non_repaint_zigzag(highs[:k+1], lows[:k+1], closes[:k+1], dev_pct=5)
        zigzag_confirmed = []
        zigzag_unconfirmed = []
        for idx, val, typ in zigzag_pivots:
            zigzag_confirmed.append({"time": times[idx], "value": float(val)})
        if unconfirmed and len(zigzag_confirmed) > 0:
            last_confirmed = zigzag_confirmed[-1]
            zigzag_unconfirmed.append(last_confirmed)
            zigzag_unconfirmed.append({"time": times[unconfirmed[0]], "value": float(unconfirmed[1])})

        fractal_lines = calculate_fractal_trendlines(highs, lows, closes, times, k)
        lr_channel = compute_linear_regression_channel(closes, times, k, period=50, std_dev_mult=2.0)

        ctx_val = {
            "close": closes[k],
            "ma5": ma_lines[p["ma_periods"][0]][-1]["value"] if len(ma_lines[p["ma_periods"][0]]) > 0 else closes[k],
            "ma20": ma_lines[p["ma_periods"][1]][-1]["value"] if len(ma_lines[p["ma_periods"][1]]) > 0 else closes[k],
            "ma60": ma_lines[p["ma_periods"][2]][-1]["value"] if len(ma_lines[p["ma_periods"][2]]) > 0 else closes[k],
            "obv": float(obv[-1]), "obv_signal": float(obv_sig[-1]),
            "macd": float(macd_line[-1]), "macd_signal": float(macd_sig[-1]), "macd_hist": float(macd_hist[-1]),
            "jma": float(jma[-1]) if len(jma) > 0 and not np.isnan(jma[-1]) else closes[k],
            "supertrend": float(supertrend[-1]) if len(supertrend) > 0 and not np.isnan(supertrend[-1]) else closes[k],
            "supertrend_trend": 1 if len(trend_up) > 0 and trend_up[-1] else -1,
            "vwma": float(vwma[-1]) if len(vwma) > 0 and not np.isnan(vwma[-1]) else closes[k]
        }

        return {
            "candles": candles_slice, "volumes": vols_slice,
            "ma": [{"period": pr, "data": ma_lines[pr]} for pr in p["ma_periods"]],
            "obv": obv_line, "obv_signal": obv_sig_line,
            "macd": macd_points, "macd_signal": macd_sig_points, "macd_hist": macd_hist_points,
            "jma": jma_line, "supertrend": supertrend_line, "vwma": vwma_line,
            "zigzag_confirmed": zigzag_confirmed, "zigzag_unconfirmed": zigzag_unconfirmed,
            "fractals": fractal_lines, "lr_channel": lr_channel, "ctx": ctx_val
        }

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


def run_session_backtest(rest, settings, session, code, tf, strategy, range_start=None, range_end=None, apply_view_params=False):
    from core import StrategyEngine

    strategy = normalize_strategy_payload(strategy, code or session.code)
    params_backup = clone_params(settings.params)
    original_ohlcv = rest.ohlcv
    try:
        settings.update_params(strategy.get("params") or {})
        if apply_view_params:
            session.view_params = clone_params(settings.params)
        engine = StrategyEngine(rest, settings)
        rest.ohlcv = lambda c, t, m: session.candles
        result = engine.backtest(code, tf, strategy)
    finally:
        rest.ohlcv = original_ohlcv
        settings.params = params_backup

    if "error" in result:
        return result
    result["strategy_name"] = strategy.get("name", "")
    result["strategy_version"] = strategy.get("version", "")
    result["strategy_params"] = strategy.get("params", {})
    return filter_backtest_range(result, session.times, range_start, range_end)


def run_batch_experiment(rest, settings, session, base_strategy, range_start=None, range_end=None, limit=12):
    if not session.candles:
        raise ValueError("simulation data is empty")
    variants = generate_strategy_variants(base_strategy)
    results = []
    for variant in variants:
        res = run_session_backtest(
            rest,
            settings,
            session,
            variant.get("code") or session.code,
            session.tf,
            variant,
            range_start=range_start,
            range_end=range_end,
            apply_view_params=False,
        )
        summary = res.get("summary", {}) or {}
        summary["score"] = compute_strategy_score(summary)
        results.append({
            "strategy": variant,
            "summary": summary,
            "trades": res.get("trades", []),
            "markers": res.get("markers", []),
        })
    results.sort(key=lambda x: x["summary"].get("score", -10**9), reverse=True)
    return {
        "base_strategy": normalize_strategy_payload(base_strategy, session.code),
        "tested": len(results),
        "results": results[:max(1, int(limit))],
    }


def upsert_generated_candidate(store, strategy, summary, code, tf, benchmark_id=""):
    payload = normalize_strategy_payload(strategy, code)
    payload["code"] = code
    payload["stage"] = "candidate"
    payload["benchmark_id"] = payload.get("benchmark_id") or benchmark_id or ""
    payload["preferred_tf"] = tf
    payload["signature"] = strategy_signature(payload)
    payload["last_summary"] = summary
    payload["notes"] = (
        (payload.get("notes") or "").strip()
        + f" | worker candidate code={code} tf={tf} total={summary.get('total_return', 0)} win={summary.get('win_rate', 0)} score={summary.get('score', 0)}"
    ).strip(" |")

    existing = None
    for item in store.list():
        item_sig = item.get("signature") or strategy_signature(dict(item, preferred_tf=item.get("preferred_tf") or tf))
        if item_sig == payload["signature"] and item.get("code") == code:
            existing = item
            break
    if existing:
        payload["id"] = existing.get("id")
        if not payload.get("parent_id"):
            payload["parent_id"] = existing.get("parent_id", "")
        if not payload.get("parent_version"):
            payload["parent_version"] = existing.get("parent_version", "")
    else:
        payload["id"] = "cand_" + payload["signature"]
    return store.upsert(payload)


class LabWorker:
    def __init__(self, rest, store, experiments):
        self.rest = rest
        self.store = store
        self.experiments = experiments
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False
        self.completed_jobs = 0
        self.completed_cycles = 0
        self.last_error = ""
        self.last_started_at = 0
        self.last_finished_at = 0
        self.current_job = {}
        self.last_results = []
        self.config = {}
        self.base_strategy = {}

    def status(self):
        with self._lock:
            return {
                "running": self.running,
                "completed_jobs": self.completed_jobs,
                "completed_cycles": self.completed_cycles,
                "last_error": self.last_error,
                "last_started_at": self.last_started_at,
                "last_finished_at": self.last_finished_at,
                "current_job": dict(self.current_job),
                "last_results": list(self.last_results[:8]),
                "config": dict(self.config),
                "base_strategy": dict(self.base_strategy),
            }

    def start(self, config, base_strategy):
        with self._lock:
            if self.running:
                return self.status()
            self.config = normalize_worker_config(
                config,
                fallback_code=(base_strategy or {}).get("code", ""),
                fallback_tf=(config or {}).get("timeframes", "t360") if isinstance(config, dict) else "t360",
            )
            self.base_strategy = normalize_strategy_payload(base_strategy, self.config["symbols"][0])
            self.last_error = ""
            self.current_job = {}
            self.last_results = []
            self.last_started_at = int(time.time())
            self._stop_event.clear()
            self.running = True
            self._thread = threading.Thread(target=self._loop, name="lab-worker", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        with self._lock:
            self._stop_event.set()
            self.running = False
            self.current_job = {}
            self.last_finished_at = int(time.time())
            return self.status()

    def _set_current_job(self, **kwargs):
        with self._lock:
            self.current_job = kwargs

    def _push_result(self, row):
        with self._lock:
            self.last_results.insert(0, row)
            self.last_results = self.last_results[:8]
            self.completed_jobs += 1

    def _loop(self):
        try:
            while not self._stop_event.is_set():
                self._run_cycle()
                with self._lock:
                    self.completed_cycles += 1
                    self.last_finished_at = int(time.time())
                if self.config.get("run_once"):
                    break
                wait_sec = int(self.config.get("interval_sec", 300))
                for _ in range(wait_sec):
                    if self._stop_event.wait(1.0):
                        break
                if self._stop_event.is_set():
                    break
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)
        finally:
            with self._lock:
                self.running = False
                self.current_job = {}
                self.last_finished_at = int(time.time())

    def _run_cycle(self):
        cfg = dict(self.config)
        base = dict(self.base_strategy)
        for code in cfg.get("symbols", []):
            for tf in cfg.get("timeframes", []):
                if self._stop_event.is_set():
                    return
                self._set_current_job(code=code, tf=tf, step="download")
                candles = self.rest.ohlcv(code, tf, cfg.get("bars", 1000))
                if not candles:
                    continue
                temp_session = SimulationSession()
                temp_session.init_data(candles, tf, "", "")
                temp_session.code = code
                end_idx = len(temp_session.times) - 1
                if end_idx <= 60:
                    continue
                for window in cfg.get("windows", [120, 240, 480]):
                    if self._stop_event.is_set():
                        return
                    range_start = max(60, end_idx - int(window) + 1)
                    local_base = dict(base)
                    local_base["code"] = code
                    self._set_current_job(code=code, tf=tf, window=window, step="backtest")
                    batch = run_batch_experiment(
                        self.rest,
                        Settings(),
                        temp_session,
                        local_base,
                        range_start=range_start,
                        range_end=end_idx,
                        limit=cfg.get("limit", 12),
                    )
                    winners = batch.get("results", [])[:cfg.get("save_top_n", 3)]
                    for rank, item in enumerate(winners, start=1):
                        summary = dict(item.get("summary", {}))
                        summary["score"] = compute_strategy_score(summary)
                        strategy = dict(item.get("strategy", {}))
                        experiment_row = self.experiments.append({
                            "type": "worker_scan",
                            "source": "lab_worker",
                            "code": code,
                            "tf": tf,
                            "window_bars": int(window),
                            "rank": rank,
                            "range_start": range_start,
                            "range_end": end_idx,
                            "base_strategy": batch.get("base_strategy"),
                            "strategy": strategy,
                            "summary": summary,
                        })
                        if cfg.get("auto_candidate", True):
                            upsert_generated_candidate(
                                self.store,
                                strategy,
                                summary,
                                code,
                                tf,
                                benchmark_id=base.get("id", ""),
                            )
                        self._push_result({
                            "code": code,
                            "tf": tf,
                            "window_bars": int(window),
                            "rank": rank,
                            "summary": summary,
                            "strategy": strategy,
                            "experiment_id": experiment_row.get("id"),
                        })


def evaluate_strategy_status(rest, settings, session, strategy, idx):
    from core import StrategyEngine, _series_vars

    strategy = normalize_strategy_payload(strategy, session.code)
    if not session.candles:
        return {"ready": False, "message": "no simulation data"}

    idx = max(0, min(int(idx), len(session.candles) - 1))
    candles = session.candles[:idx + 1]
    if len(candles) < 60:
        return {"ready": False, "message": "warming up", "bars_ready": len(candles)}

    params_backup = clone_params(settings.params)
    try:
        settings.update_params(strategy.get("params") or {})
        engine = StrategyEngine(rest, settings)
        closes = [r["close"] for r in candles]
        vols = [r["volume"] for r in candles]
        highs = [r["high"] for r in candles]
        lows = [r["low"] for r in candles]
        ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma, zigzag_trend, zigzag_turn_up, zigzag_turn_down = _series_vars(closes, vols, highs, lows, settings.params)
        prev_ctx = {}
        if idx > 0:
            prev_ctx = engine._ctx_at(
                idx - 1, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
                zigzag_trend, zigzag_turn_up, zigzag_turn_down, {}
            )
        cur_ctx = engine._ctx_at(
            idx, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
            zigzag_trend, zigzag_turn_up, zigzag_turn_down, prev_ctx
        )
        funcs = engine._funcs(cur_ctx, prev_ctx)
        entry_hit = bool(strategy.get("entry_expr")) and bool(SafeEval(cur_ctx, funcs).eval(strategy["entry_expr"]))
        exit_hit = bool(strategy.get("exit_expr")) and bool(SafeEval(cur_ctx, funcs).eval(strategy["exit_expr"]))
        signal = "entry" if entry_hit else ("exit" if exit_hit else "neutral")
        return {
            "ready": True,
            "signal": signal,
            "entry_hit": entry_hit,
            "exit_hit": exit_hit,
            "message": "entry" if entry_hit else ("exit" if exit_hit else "waiting"),
            "ctx": {
                "close": round(float(cur_ctx.get("close", 0.0)), 2),
                "obv": round(float(cur_ctx.get("obv", 0.0)), 2),
                "obv_signal": round(float(cur_ctx.get("obv_signal", 0.0)), 2),
                "macd": round(float(cur_ctx.get("macd", 0.0)), 4),
                "macd_signal": round(float(cur_ctx.get("macd_signal", 0.0)), 4),
                "macd_hist": round(float(cur_ctx.get("macd_hist", 0.0)), 4),
                "supertrend_trend": int(cur_ctx.get("supertrend_trend", 0) or 0),
                "jma": round(float(cur_ctx.get("jma", 0.0) or 0.0), 2),
                "vwma": round(float(cur_ctx.get("vwma", 0.0) or 0.0), 2),
                "zigzag_trend": int(cur_ctx.get("zigzag_trend", 0) or 0),
                "zigzag_turn_up": bool(cur_ctx.get("zigzag_turn_up", False)),
            },
        }
    finally:
        settings.params = params_backup


# ═══════════════════════════════════════════════════════════════
#  HTML 대시보드 템플릿
# ═══════════════════════════════════════════════════════════════

def build_simulation_html():
    h = []
    h.append('<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">')
    h.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    h.append('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    h.append('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">')
    h.append('<title>What You See Is What You Trade! - 차트 시뮬레이터</title>')
    h.append('''<style>
* { margin:0; padding:0; box-sizing:border-box; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.1); }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.12); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.22); }

body { background:#0a0c10; color:#d1d4dc; font-family:'Inter', 'Segoe UI', sans-serif; display:flex; height:100vh; overflow:hidden; }
#left { width:400px; border-right:1px solid rgba(255, 255, 255, 0.08); display:flex; flex-direction:column; background: #0f1118; }
#right { flex:1; display:flex; flex-direction:column; min-width:0; background: #0f1118; }
.bar { padding:10px 14px; background:rgba(20, 24, 35, 0.75); backdrop-filter:blur(8px); border-bottom:1px solid rgba(255, 255, 255, 0.08);
       display:flex; align-items:center; gap:8px; font-size:12px; flex-wrap:wrap; }
.bar select, .bar input { padding:5px 8px; background:rgba(255, 255, 255, 0.05); border:1px solid rgba(255, 255, 255, 0.12);
       color:#e1e4ea; border-radius:6px; font-size:12px; outline:none; transition: all 0.2s ease; }
.bar select:focus, .bar input:focus { border-color: #2962ff; box-shadow: 0 0 0 3px rgba(41, 98, 255, 0.2); }

.bar button { padding:6px 12px; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:12px; font-weight:600; 
              transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
.bar button:hover { filter: brightness(1.15); transform: translateY(-1px); }
.bar button:active { transform: translateY(0); filter: brightness(0.95); }

#chart { flex:1; min-height:200px; }
.tag { font-size:11px; color:#90a4ae; cursor:pointer; display:flex; align-items:center; gap:4px; }
.tag input { cursor:pointer; }

.section-title { font-size:13px; font-weight:700; color:#42a5f5; padding:12px 14px 4px 14px; border-top:1px solid rgba(255,255,255,0.05); }
.panel-box { padding:8px 14px; display:flex; flex-direction:column; gap:8px; }
.frow { display:flex; align-items:center; font-size:12px; }
.frow label { flex:1; color:#90a4ae; }
.frow input, .frow select { width:150px; }

.sim-controls { display:flex; align-items:center; gap:4px; margin-left:auto; }
.sim-btn { background: rgba(255, 255, 255, 0.06) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; color:#e1e4ea !important; }
.sim-btn:hover { background: rgba(255, 255, 255, 0.12) !important; }
.sim-btn.active { background: #2962ff !important; border-color: transparent !important; color:#fff !important; }

.trade-board { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 4px; }
.trade-card { background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 10px; text-align: center; }
.trade-card .lbl { font-size: 10px; color: #90a4ae; margin-bottom: 2px; }
.trade-card .val { font-size: 14px; font-weight: 700; color: #fff; }

.btn-buy { background: linear-gradient(135deg, #26a69a, #00897b) !important; box-shadow: 0 2px 6px rgba(38, 166, 154, 0.2); }
.btn-sell { background: linear-gradient(135deg, #ef5350, #d32f2f) !important; box-shadow: 0 2px 6px rgba(239, 83, 80, 0.2); }

.tbl-wrap { flex:1; overflow-y:auto; font-size:11px; border-top:1px solid rgba(255,255,255,0.08); background:rgba(15,17,24,0.3); }
table { width:100%; border-collapse:collapse; }
th { background:rgba(20, 24, 35, 0.85); color:#90a4ae; padding:6px 4px; border-bottom:1px solid rgba(255,255,255,0.08); font-weight:600; text-align:center; }
td { padding:6px 4px; text-align:center; border-bottom:1px solid rgba(255,255,255,0.03); color:#fff; }
.up { color:#26a69a; } .dn { color:#ef5350; }

.dlg { position:fixed; inset:0; display:none; align-items:center; justify-content:center; background:rgba(6, 8, 12, 0.72); z-index:50; }
.dlg.show { display:flex; }
.dlgbox { width:min(1160px, 94vw); max-height:90vh; overflow:hidden; display:flex; flex-direction:column;
          background:#10141c; border:1px solid rgba(255,255,255,0.08); border-radius:14px; box-shadow:0 24px 64px rgba(0,0,0,0.45); }
.dlghead { padding:14px 18px; border-bottom:1px solid rgba(255,255,255,0.08); display:flex; justify-content:space-between; align-items:center; }
.dlgtabs { padding:10px 18px; border-bottom:1px solid rgba(255,255,255,0.08); display:flex; gap:8px; flex-wrap:wrap; }
.tabbtn { padding:7px 12px; border:none; border-radius:999px; cursor:pointer; font-size:12px; font-weight:700; color:#c7d0db; background:rgba(255,255,255,0.06); }
.tabbtn.active { color:#fff; background:linear-gradient(135deg, #2962ff, #1565c0); }
.dlgbody { padding:16px 18px; display:grid; grid-template-columns: 1.15fr 1fr; gap:16px; overflow:auto; }
.dlgfoot { padding:14px 18px; border-top:1px solid rgba(255,255,255,0.08); display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; }
.dlgcard { background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:12px; }
.dlgcard h4 { font-size:12px; color:#42a5f5; margin-bottom:0; }
.card-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:10px; }
.help-btn { width:22px; height:22px; border:none; border-radius:999px; cursor:pointer; font-size:12px; font-weight:800; color:#fff; background:rgba(66,165,245,0.22); }
.help-btn:hover { background:rgba(66,165,245,0.36); }
.help-card { grid-column:1 / span 2; display:none; }
.help-block { display:none; }
.help-block.show { display:block; }
.param-grid { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:8px; }
.param-item { display:flex; flex-direction:column; gap:4px; }
.param-item label { font-size:11px; color:#90a4ae; }
.param-item input { width:100%; padding:6px 8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:6px; color:#fff; }
.chip { display:inline-flex; align-items:center; padding:3px 8px; border-radius:999px; font-size:11px; font-weight:700; }
.chip.neutral { background:rgba(255,255,255,0.08); color:#c7d0db; }
.chip.entry { background:rgba(38,166,154,0.16); color:#53dfd0; }
.chip.exit { background:rgba(239,83,80,0.16); color:#ff8e8a; }
.subtle { font-size:11px; color:#90a4ae; line-height:1.45; }
.mono { font-family:Consolas, 'Courier New', monospace; }
.action-row { display:flex; gap:8px; flex-wrap:wrap; }
.action-row button, .dlgfoot button { padding:7px 12px; border:none; border-radius:8px; color:#fff; cursor:pointer; font-size:12px; font-weight:600; }
.soft-btn { background:rgba(255,255,255,0.08); }
.primary-btn { background:linear-gradient(135deg, #2962ff, #1565c0); }
.accent-btn { background:linear-gradient(135deg, #ab47bc, #8e24aa); }
.good-btn { background:linear-gradient(135deg, #26a69a, #00897b); }
.danger-btn { background:linear-gradient(135deg, #ef5350, #d32f2f); }
.result-box { min-height:34px; padding:8px 10px; border-radius:8px; background:rgba(255,255,255,0.03); font-size:12px; }
.compact-table { max-height:220px; overflow:auto; border:1px solid rgba(255,255,255,0.08); border-radius:10px; }
.compact-table table { font-size:11px; }
.workspace-shell { display:grid; grid-template-columns:220px 1fr; gap:16px; min-height:560px; }
.workspace-nav { background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:12px; display:flex; flex-direction:column; gap:8px; }
.workspace-nav-btn { text-align:left; padding:10px 12px; border:none; border-radius:10px; cursor:pointer; font-size:12px; font-weight:700; color:#c7d0db; background:rgba(255,255,255,0.05); }
.workspace-nav-btn.active { color:#fff; background:linear-gradient(135deg, #2962ff, #1565c0); }
.workspace-main { min-width:0; display:flex; flex-direction:column; gap:12px; }
.workspace-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.workspace-panel { background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:12px; min-width:0; }
.workspace-panel h4 { margin:0 0 10px 0; font-size:12px; color:#42a5f5; }
.workspace-textarea { width:100%; min-height:160px; resize:vertical; padding:10px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:8px; color:#fff; font-size:12px; line-height:1.5; }
.workspace-textarea.mono { min-height:220px; }
.preset-row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
.preset-btn { padding:7px 10px; border:none; border-radius:999px; cursor:pointer; font-size:11px; font-weight:700; color:#dce6f5; background:rgba(66,165,245,0.16); }
.preset-btn:hover { background:rgba(66,165,245,0.28); }
.workspace-hint { padding:10px 12px; border-radius:10px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.05); font-size:12px; line-height:1.65; white-space:pre-line; }
.workspace-kv { display:grid; grid-template-columns:110px 1fr; gap:8px; font-size:12px; }
.workspace-kv div:nth-child(odd) { color:#90a4ae; }
.workspace-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
.workspace-actions button { padding:7px 12px; border:none; border-radius:8px; color:#fff; cursor:pointer; font-size:12px; font-weight:600; }
</style></head><body>''')

    # 좌측 패널
    h.append('<div id="left">')
    h.append('<div class="bar" style="font-weight:700; font-size:14px; color:#2962ff; justify-content:center; border-bottom:2px solid #2962ff;">Simulate What You Trade!</div>')
    
    h.append('<div class="section-title">📊 데이터 설정</div>')
    h.append('<div class="panel-box">')
    h.append('<div class="frow"><label>종목코드</label><input type="text" id="simCode" value="000660"></div>')
    h.append('<div class="frow"><label>타임프레임</label><select id="simTF">')
    
    # 🚨 원본 settings.py의 정의를 그대로 바인딩 (m5가 아니라 원본 규격 값 매핑)
    for label, val in TIMEFRAMES:
        h.append(f'<option value="{val}">{label}</option>')
    h.append('</select></div>')
    
    h.append('<div class="frow"><label>시작일자</label><input type="date" id="simDate"></div>')
    h.append('<div class="frow"><label>시작시간</label><input type="time" id="simTime" value="09:00:00"></div>')
    h.append('<button style="background:linear-gradient(135deg, #2962ff, #1565c0); margin-top:4px;" onclick="loadSimulationData()">차트 데이터 다운로드</button>')
    h.append('</div>')

    h.append('<div class="section-title">💰 실시간 가상 매매 시뮬레이션</div>')
    h.append('<div class="panel-box">')
    h.append('<div class="trade-board">')
    h.append('<div class="trade-card"><div class="lbl">예수금</div><div class="val" id="trBalance">10,000,000 원</div></div>')
    h.append('<div class="trade-card"><div class="lbl">평가손익</div><div class="val" id="trPnL">0 원 (0.00%)</div></div>')
    h.append('<div class="trade-card"><div class="lbl">보유수량</div><div class="val" id="trQty">0 주</div></div>')
    h.append('<div class="trade-card"><div class="lbl">평균단가</div><div class="val" id="trAvgPrice">0 원</div></div>')
    h.append('</div>')
    h.append('<div style="display:flex; gap:8px; margin-top:4px;">')
    h.append('<button class="btn-buy" style="flex:1;" onclick="executeTrade(\'buy\')">매 수</button>')
    h.append('<button class="btn-sell" style="flex:1;" onclick="executeTrade(\'sell\')">매 도</button>')
    h.append('<button class="sim-btn" style="flex:1;" onclick="executeTrade(\'exit\')">전량청산</button>')
    h.append('</div>')
    h.append('</div>')

    h.append('<div class="tbl-wrap">')
    h.append('<table><thead><tr><th>시각</th><th>구분</th><th>체결가</th><th>수량</th><th>실현손익</th></tr></thead>')
    h.append('<tbody id="tradeLogBody"></tbody></table>')
    h.append('</div>')

    h.append('<div class="section-title">🧪 백테스트 실행</div>')
    h.append('<div class="panel-box">')
    h.append('<div class="frow"><label>진입조건식</label><input type="text" id="sEntry" value="close > supertrend" style="width:180px"></div>')
    h.append('<div class="frow"><label>청산조건식</label><input type="text" id="sExit" value="close < supertrend" style="width:180px"></div>')
    h.append('<div class="frow"><label>주문수량</label><input type="number" id="sQty" value="100" style="width:100px"></div>')
    h.append('<button style="background:linear-gradient(135deg, #ab47bc, #8e24aa);" onclick="runBacktest()">조건 백테스트 실행</button>')
    h.append('<div id="btSummary" style="font-size:11px; color:#26a69a; margin-top:2px;"></div>')
    h.append('</div>')
    h.append('</div>')

    # 우측 차트 및 시뮬레이션 제어 바
    h.append('<div id="right">')
    h.append('<div class="section-title">Strategy Manager</div>')
    h.append('<div class="panel-box">')
    h.append('<div class="action-row">')
    h.append('<button class="accent-btn" onclick="openStrategyManager()">Open Manager</button>')
    h.append('<button class="primary-btn" onclick="openStrategyManager(\'workspace\')">Simple Workspace</button>')
    h.append('<button class="primary-btn" onclick="insertCurrentStrategy()">Insert To Chart</button>')
    h.append('<button class="good-btn" onclick="runPreciseEvaluation()">Precise PnL</button>')
    h.append('</div>')
    h.append('<div class="subtle">Active Strategy: <span id="activeStrategyName">None</span></div>')
    h.append('<div class="subtle">Live Signal: <span id="strategySignalChip" class="chip neutral">Idle</span></div>')
    h.append('<div id="strategyStatusText" class="subtle">Open the manager, validate a strategy, insert it into the chart, then use range play to confirm live entry timing.</div>')
    h.append('<div id="strategyPanelSummary" class="result-box">No strategy result yet.</div>')
    h.append('</div>')
    h.append('<div class="tbl-wrap" style="max-height:200px;">')
    h.append('<table><thead><tr><th>Entry</th><th>Exit</th><th>Bars</th><th>Reason</th><th>Return</th></tr></thead>')
    h.append('<tbody id="strategyTradesBody"></tbody></table>')
    h.append('</div>')
    h.append('<div id="right">')
    h.append('<div class="bar">')
    h.append('<span id="chartSymbol" style="font-weight:700; font-size:14px; color:#fff;">종목 데이터 없음</span>')
    
    h.append('<label class="tag"><input type="checkbox" id="show_ma" checked onchange="toggleIndicator(\'ma\')"> MA</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_obv" checked onchange="toggleIndicator(\'obv\')"> OBV</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_macd" checked onchange="toggleIndicator(\'macd\')"> MACD</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_jma" checked onchange="toggleIndicator(\'jma\')"> JMA</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_supertrend" checked onchange="toggleIndicator(\'supertrend\')"> Supertrend</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_vwma" checked onchange="toggleIndicator(\'vwma\')"> VWMA</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_zigzag" checked onchange="toggleIndicator(\'zigzag\')"> 🔵 ZigZag(Non-Repaint)</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_fractals" checked onchange="toggleIndicator(\'fractals\')"> 📈 프랙탈추세선</label>')
    h.append('<label class="tag"><input type="checkbox" id="show_lr" onchange="toggleIndicator(\'lr\')"> 회귀채널</label>')

    h.append('<div class="sim-controls">')
    h.append('<button class="sim-btn" id="rangeBtn" onclick="toggleRangeSelect()">구간선택</button>')
    h.append('<button class="sim-btn" onclick="clearRange()">구간해제</button>')
    h.append('<button class="sim-btn" onclick="simGoStart()">처음</button>')
    h.append('<button class="sim-btn" onclick="simPrev()">이전</button>')
    h.append('<button class="sim-btn" id="playBtn" onclick="simTogglePlay()">플레이</button>')
    h.append('<button class="sim-btn" onclick="simNext()">다음</button>')
    h.append('<button class="sim-btn" onclick="simGoEnd()">마지막</button>')
    h.append('<button class="sim-btn" onclick="viewAllCandles()">전체보기</button>')
    h.append('<select id="simSpeed" style="width:80px">')
    h.append('<option value="100">0.1초</option>')
    h.append('<option value="200" selected>0.2초</option>')
    h.append('<option value="500">0.5초</option>')
    h.append('<option value="1000">1초</option>')
    h.append('<option value="2000">2초</option>')
    h.append('</select>')
    h.append('</div>')
    h.append('</div>')

    h.append('<div id="chart"></div>')
    h.append('</div>')

    # CDN 스크립트 연결 및 자바스크립트 초기화
    h.append('<script src="https://unpkg.com/lightweight-charts@5.1.0/dist/lightweight-charts.standalone.production.js"></script>')
    h.append('<script src="https://unpkg.com/lightweight-charts@5.1.0/dist/lightweight-charts.standalone.production.js"></script>')
    h.append('''<div id="strategyDlg" class="dlg"><div class="dlgbox">
<div class="dlghead">
  <div>
    <div style="font-size:16px;font-weight:700;color:#fff;">Strategy Manager</div>
    <div class="subtle">Create, validate, save, insert, and evaluate strategies for the current simulation session.</div>
  </div>
  <button class="soft-btn" onclick="closeStrategyManager()">Close</button>
</div>
<div class="dlgtabs">
  <button id="tabBtn_strategy" class="tabbtn active" onclick="setManagerTab('strategy')">전략작성</button>
  <button id="tabBtn_workspace" class="tabbtn" onclick="setManagerTab('workspace')">간편작업</button>
  <button id="tabBtn_discovery" class="tabbtn" onclick="setManagerTab('discovery')">후보발굴</button>
  <button id="tabBtn_automation" class="tabbtn" onclick="setManagerTab('automation')">자동탐색</button>
  <button id="tabBtn_help" class="tabbtn" onclick="setManagerTab('help')">도움말</button>
</div>
<div class="dlgbody">
  <div id="llmWorkspaceCard" class="dlgcard" data-tab-group="workspace" style="grid-column:1 / span 2;">
    <div class="card-head">
      <h4>LLM Workspace</h4>
      <button class="help-btn" onclick="showHelp('llm_workspace')">?</button>
    </div>
    <div class="workspace-shell">
      <div class="workspace-nav">
        <button id="llmTaskBtn_condition_search" class="workspace-nav-btn active" onclick="setLLMTask('condition_search')">조건검색</button>
        <button id="llmTaskBtn_performance_validation" class="workspace-nav-btn" onclick="setLLMTask('performance_validation')">성과검증</button>
        <button id="llmTaskBtn_top_riser_study" class="workspace-nav-btn" onclick="setLLMTask('top_riser_study')">상승요인분석</button>
        <button id="llmTaskBtn_stock_recommendation" class="workspace-nav-btn" onclick="setLLMTask('stock_recommendation')">종목추천</button>
        <div class="workspace-hint" id="llmMenuHint" style="margin-top:8px;">자연어로 작업을 설명하고, 생성된 프롬프트를 외부 LLM에 붙여 넣은 뒤 JSON만 다시 가져오면 됩니다.</div>
      </div>
      <div class="workspace-main">
        <div class="workspace-grid">
          <div class="workspace-panel">
            <h4 id="llmTaskTitle">조건검색</h4>
            <div class="subtle" id="llmTaskSubtle" style="margin-bottom:10px;">찾고 싶은 종목 구조를 자연어로 설명하면 JSON 초안을 만들기 위한 프롬프트를 생성합니다.</div>
            <div class="preset-row" id="llmPresetButtons"></div>
            <textarea id="llmUserPrompt" class="workspace-textarea" placeholder="예: 지난 20일 일봉 박스권 이후, 오늘 5분봉 기준봉이 나오고 상단 돌파 초입인 종목을 찾아줘."></textarea>
            <div class="workspace-actions">
              <button class="primary-btn" onclick="generateLLMPrompt()">프롬프트 생성</button>
              <button class="soft-btn" onclick="clearLLMUserPrompt()">초기화</button>
            </div>
          </div>
          <div class="workspace-panel">
            <h4>외부 LLM용 프롬프트</h4>
            <div class="subtle" style="margin-bottom:10px;">아래 내용을 복사해서 외부 LLM에 넣고, 반드시 JSON만 반환받아 오른쪽 아래 입력란에 붙여 넣으세요.</div>
            <textarea id="llmGeneratedPrompt" class="workspace-textarea mono" placeholder="프롬프트 생성 버튼을 누르면 여기에 외부 LLM용 프롬프트가 생성됩니다."></textarea>
            <div class="workspace-actions">
              <button class="soft-btn" onclick="copyLLMGeneratedPrompt()">프롬프트 복사</button>
              <button class="good-btn" onclick="showHelp('llm_manual_flow')">사용순서</button>
            </div>
          </div>
        </div>
        <div class="workspace-grid">
          <div class="workspace-panel">
            <h4>LLM 반환 JSON</h4>
            <div class="subtle" style="margin-bottom:10px;">설명 없이 JSON만 붙여 넣으세요. 검증 후 기존 엔진에 연결됩니다.</div>
            <textarea id="llmJsonInput" class="workspace-textarea mono" placeholder='{"task":"condition_search","condition":{...}}'></textarea>
            <div class="workspace-actions">
              <button class="primary-btn" onclick="validateLLMJson()">JSON 검증</button>
              <button class="accent-btn" onclick="applyLLMJson(false)">폼 반영</button>
              <button class="good-btn" onclick="applyLLMJson(true)">검증 후 실행</button>
              <button class="soft-btn" onclick="openAdvancedFromLLMTask()">고급 화면 열기</button>
            </div>
            <div id="llmJsonSummary" class="result-box" style="margin-top:10px;">아직 JSON이 없습니다.</div>
            <div id="llmJsonWarnings" class="result-box" style="margin-top:10px;white-space:pre-line;">의미 검증 경고가 여기에 표시됩니다.</div>
          </div>
          <div class="workspace-panel">
            <h4>작업 가이드</h4>
            <div id="llmTaskGuide" class="workspace-hint"></div>
            <div class="workspace-kv" style="margin-top:12px;">
              <div>현재 작업</div><div id="llmTaskCurrent">조건검색</div>
              <div>실행 대상</div><div id="llmTaskTarget">Condition Search 폼</div>
              <div>실행 결과</div><div id="llmTaskResult">JSON 검증 후 기존 검증 엔진으로 연결됩니다.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div id="strategyHelpCard" class="dlgcard help-card" data-tab-group="help">
    <div class="card-head">
      <h4>Guide & Help</h4>
      <button class="help-btn" onclick="showHelp('overview')">?</button>
    </div>
    <div id="strategyHelpBody" class="subtle" style="white-space:pre-line;">이 화면은 전략 작성, 후보군 발굴, 자동 반복 탐색을 나누어 쓰도록 구성됩니다.</div>
  </div>
  <div id="strategyDefinitionCard" class="dlgcard" data-tab-group="strategy">
    <div class="card-head"><h4>Strategy Definition</h4><button class="help-btn" onclick="showHelp('strategy_definition')">?</button></div>
    <input type="hidden" id="smId">
    <input type="hidden" id="smParentId">
    <input type="hidden" id="smParentVersion">
    <div class="frow"><label>Saved</label><select id="smList" onchange="selectStrategy(this.value)" style="width:220px"></select></div>
    <div class="frow"><label>Name</label><input id="smName" type="text" style="width:220px" value="WISI_Base"></div>
    <div class="frow"><label>Version</label><input id="smVersion" type="text" style="width:120px" value="v0.2.0"></div>
    <div class="frow"><label>Stage</label><select id="smStage" style="width:160px"><option value="draft">draft</option><option value="candidate">candidate</option><option value="promoted">promoted</option><option value="archived">archived</option></select></div>
    <div class="frow"><label>Code</label><input id="smCode" type="text" style="width:120px" value="000660"></div>
    <div class="frow"><label>Benchmark</label><input id="smBenchmarkId" type="text" style="width:220px" placeholder="baseline strategy id"></div>
    <div class="frow"><label>Entry</label><input id="smEntry" class="mono" type="text" style="width:100%" value="(zigzag_turn_up or zigzag_trend > 0) and close > supertrend"></div>
    <div class="frow"><label>Exit</label><input id="smExit" class="mono" type="text" style="width:100%" value="zigzag_turn_down or zigzag_trend < 0 or close < supertrend"></div>
    <div class="frow"><label>Qty</label><input id="smQty" type="number" style="width:100px" value="100"></div>
    <div class="frow"><label>Stop %</label><input id="smStop" type="number" step="any" style="width:100px" value="0"></div>
    <div class="frow"><label>Take %</label><input id="smTake" type="number" step="any" style="width:100px" value="0"></div>
    <div class="frow" style="align-items:flex-start;"><label>Notes</label><textarea id="smNotes" style="width:100%;height:70px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:6px;color:#fff;padding:6px;"></textarea></div>
    <h4 style="margin-top:14px;">Parameter Overrides</h4>
    <div id="strategyParamFields" class="param-grid"></div>
  </div>
  <div id="managerRightCol" style="display:flex;flex-direction:column;gap:16px;">
    <div class="dlgcard" data-tab-group="strategy">
      <div class="card-head"><h4>Validation</h4><button class="help-btn" onclick="showHelp('validation')">?</button></div>
      <div class="subtle">Available trend gates now include <span class="mono">zigzag_trend</span>, <span class="mono">zigzag_turn_up</span>, and <span class="mono">zigzag_turn_down</span>. For exact crosses, use explicit previous values such as <span class="mono">prev_macd &lt;= prev_macd_signal</span>.</div>
      <div id="strategyValidateResult" class="result-box" style="margin-top:10px;">No validation yet.</div>
    </div>
    <div class="dlgcard" data-tab-group="discovery">
      <div class="card-head"><h4>Condition Search</h4><button class="help-btn" onclick="showHelp('condition_builder')">?</button></div>
      <input type="hidden" id="cvId">
      <input type="hidden" id="cvParentId">
      <input type="hidden" id="cvParentVersion">
      <div class="frow"><label>Saved</label><select id="cvList" onchange="selectCondition(this.value)" style="width:220px"></select></div>
      <div class="frow"><label>Name</label><input id="cvName" type="text" style="width:220px" value="박스권_기준봉_상승전환"></div>
      <div class="frow"><label>Version</label><input id="cvVersion" type="text" style="width:120px" value="v0.1.0"></div>
      <div class="frow"><label>Stage</label><select id="cvStage" style="width:160px"><option value="draft">draft</option><option value="candidate">candidate</option><option value="promoted">promoted</option><option value="archived">archived</option></select></div>
      <div class="frow"><label>TF</label><input id="cvTF" type="text" style="width:120px" value="m5"></div>
      <div class="frow"><label>Expr</label><input id="cvExpr" class="mono" type="text" style="width:100%" value="A and B and C"></div>
      <div class="frow"><label>Desc</label><input id="cvDesc" type="text" style="width:100%" value="박스권 압축 + 기준봉 + ZigZag 상승전환"></div>
      <div class="frow" style="align-items:flex-start;"><label>Rows JSON</label><textarea id="cvRows" class="mono" style="width:100%;height:160px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:6px;color:#fff;padding:6px;"></textarea></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="soft-btn" onclick="newConditionForm()">New</button>
        <button class="soft-btn" onclick="cloneConditionForm()">Clone New Ver</button>
        <button class="primary-btn" onclick="validateConditionForm()">Validate</button>
        <button class="accent-btn" onclick="saveConditionForm()">Save</button>
        <button class="danger-btn" onclick="deleteConditionForm()">Delete</button>
      </div>
      <div id="conditionValidateResult" class="result-box" style="margin-top:10px;">No condition validation yet.</div>
    </div>
    <div class="dlgcard" data-tab-group="discovery">
      <div class="card-head"><h4>Condition Validation</h4><button class="help-btn" onclick="showHelp('condition_validation')">?</button></div>
      <div class="frow"><label>Date</label><input id="condSearchDate" type="date" style="width:160px"></div>
      <div class="frow"><label>Time</label><input id="condSearchTime" type="time" style="width:120px" value="09:10"></div>
      <div class="frow"><label>TF</label><input id="condSearchTF" type="text" style="width:120px" value="m5"></div>
      <div class="frow"><label>Bars</label><input id="condBars" type="number" style="width:120px" value="500"></div>
      <div class="frow"><label>Top N</label><input id="condTopN" type="number" style="width:120px" value="15"></div>
      <div class="frow"><label>Symbols</label><input id="condSymbols" type="text" style="width:100%" placeholder="empty = latest universe or current code"></div>
      <div class="frow"><label>Strategy</label><select id="condStrategyId" style="width:220px"></select></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="primary-btn" onclick="runConditionValidation()">Run Validation</button>
        <button class="soft-btn" onclick="applyConditionValidationToWorker()">Use Top Codes</button>
      </div>
      <div id="conditionValidationSummary" class="result-box" style="margin-top:10px;">No condition validation yet.</div>
      <div class="compact-table" style="margin-top:10px;max-height:200px;">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Score</th><th>최고수익률</th><th>전략수익률</th><th>승률</th><th>거래수</th></tr></thead>
          <tbody id="conditionValidationBody"></tbody>
        </table>
      </div>
    </div>
    <div class="dlgcard" data-tab-group="discovery">
      <div class="card-head"><h4>Top Riser Study</h4><button class="help-btn" onclick="showHelp('top_riser_study')">?</button></div>
      <div class="frow"><label>Source Date</label><input id="trsSourceDate" type="date" style="width:160px"></div>
      <div class="frow"><label>Source Top N</label><input id="trsTopN" type="number" style="width:120px" value="10"></div>
      <div class="frow"><label>Target Date</label><input id="trsTargetDate" type="date" style="width:160px"></div>
      <div class="frow"><label>Candidate Limit</label><input id="trsCandidateLimit" type="number" style="width:120px" value="10"></div>
      <div class="frow"><label>Symbols</label><input id="trsSymbols" type="text" style="width:100%" placeholder="empty = target date universe snapshot"></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="primary-btn" onclick="runTopRiserStudy()">Analyze & Validate</button>
        <button class="soft-btn" onclick="applyTopRiserStudyToWorker()">Use Top Codes</button>
      </div>
      <div id="topRiserStudySummary" class="result-box" style="margin-top:10px;">No top riser study yet.</div>
      <div class="compact-table" style="margin-top:10px;max-height:220px;">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Score</th><th>등락률</th><th>OBV</th><th>MACD</th><th>VolRatio</th><th>Box%</th></tr></thead>
          <tbody id="topRiserStudyBody"></tbody>
        </table>
      </div>
    </div>
    <div class="dlgcard" data-tab-group="discovery">
      <div class="card-head"><h4>Universe Builder</h4><button class="help-btn" onclick="showHelp('universe')">?</button></div>
      <div class="frow"><label>Limit Each</label><input id="universeLimitEach" type="number" style="width:120px" value="30"></div>
      <div class="frow"><label>Top N</label><input id="universeTopN" type="number" style="width:120px" value="20"></div>
      <div class="frow"><label>Trade Value</label><input id="universeUseTV" type="checkbox" checked style="width:auto"></div>
      <div class="frow"><label>Change Rate</label><input id="universeUseCR" type="checkbox" checked style="width:auto"></div>
      <div class="frow"><label>Analyze Daily</label><input id="universeAnalyzeDaily" type="checkbox" checked style="width:auto"></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="primary-btn" onclick="runUniverseBuilder()">Build Universe</button>
        <button class="soft-btn" onclick="applyUniverseToWorker()">Use In Worker</button>
      </div>
      <div id="universeSummary" class="result-box" style="margin-top:10px;">No universe snapshot yet.</div>
      <div class="compact-table" style="margin-top:10px;max-height:180px;">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Tags</th><th>Score</th><th>Chg%</th></tr></thead>
          <tbody id="universeBody"></tbody>
        </table>
      </div>
    </div>
    <div class="dlgcard" data-tab-group="discovery">
      <div class="card-head"><h4>Recommendation Builder</h4><button class="help-btn" onclick="showHelp('recommendation')">?</button></div>
      <div class="frow"><label>TFs</label><input id="recoTFs" type="text" style="width:220px" value="t360,t720"></div>
      <div class="frow"><label>Top N</label><input id="recoTopN" type="number" style="width:120px" value="10"></div>
      <div class="frow"><label>Universe Limit</label><input id="recoUniverseLimit" type="number" style="width:120px" value="20"></div>
      <div class="frow"><label>Bars</label><input id="recoBars" type="number" style="width:120px" value="1000"></div>
      <div class="frow"><label>Window 1</label><input id="recoWindow1" type="number" style="width:120px" value="120"></div>
      <div class="frow"><label>Window 2</label><input id="recoWindow2" type="number" style="width:120px" value="240"></div>
      <div class="frow"><label>Window 3</label><input id="recoWindow3" type="number" style="width:120px" value="480"></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="primary-btn" onclick="runRecommendationBuilder()">Build Recommendations</button>
        <button class="soft-btn" onclick="applyRecommendationsToWorker()">Use In Worker</button>
      </div>
      <div id="recommendationSummary" class="result-box" style="margin-top:10px;">No recommendation snapshot yet.</div>
      <div class="compact-table" style="margin-top:10px;max-height:180px;">
        <table>
          <thead><tr><th>Code</th><th>Reco</th><th>Leader</th><th>Strategy</th><th>Win</th><th>전략수익률</th></tr></thead>
          <tbody id="recommendationBody"></tbody>
        </table>
      </div>
    </div>
    <div class="dlgcard" data-tab-group="strategy">
      <div class="card-head"><h4>Evaluation Summary</h4><button class="help-btn" onclick="showHelp('evaluation')">?</button></div>
      <div id="strategyEvalSummary" class="result-box">No evaluation yet.</div>
      <div class="compact-table" style="margin-top:10px;">
        <table>
          <thead><tr><th>Entry</th><th>Exit</th><th>Bars</th><th>Reason</th><th>Return</th></tr></thead>
          <tbody id="strategyEvalTrades"></tbody>
        </table>
      </div>
    </div>
    <div class="dlgcard" data-tab-group="strategy">
      <div class="card-head"><h4>Version Compare</h4><button class="help-btn" onclick="showHelp('compare')">?</button></div>
      <div class="frow"><label>Baseline</label><select id="cmpBase" style="width:220px"></select></div>
      <div class="frow"><label>Candidate</label><select id="cmpCand" style="width:220px"></select></div>
      <div id="strategyCompareSummary" class="result-box" style="margin-top:10px;">No comparison yet.</div>
    </div>
    <div class="dlgcard" data-tab-group="automation">
      <div class="card-head"><h4>Lab Worker</h4><button class="help-btn" onclick="showHelp('worker')">?</button></div>
      <div class="frow"><label>Symbols</label><input id="workerSymbols" type="text" style="width:220px" value="000660"></div>
      <div class="frow"><label>Timeframes</label><input id="workerTFs" type="text" style="width:220px" value="t360"></div>
      <div class="frow"><label>Windows</label><input id="workerWindows" type="text" style="width:220px" value="120,240,480"></div>
      <div class="frow"><label>Bars</label><input id="workerBars" type="number" style="width:120px" value="1000"></div>
      <div class="frow"><label>Interval Sec</label><input id="workerIntervalSec" type="number" style="width:120px" value="300"></div>
      <div class="frow"><label>Save Top N</label><input id="workerTopN" type="number" style="width:120px" value="3"></div>
      <div class="frow"><label>Auto Candidate</label><input id="workerAutoCandidate" type="checkbox" checked style="width:auto"></div>
      <div class="action-row" style="margin-top:10px;">
        <button class="primary-btn" onclick="startLabWorker(true)">Run Once</button>
        <button class="accent-btn" onclick="startLabWorker(false)">Start Loop</button>
        <button class="danger-btn" onclick="stopLabWorker()">Stop</button>
      </div>
      <div id="workerStatusSummary" class="result-box" style="margin-top:10px;">Worker idle.</div>
    </div>
    <div class="dlgcard" data-tab-group="automation">
      <div class="card-head"><h4>Lab Snapshot</h4><button class="help-btn" onclick="showHelp('snapshot')">?</button></div>
      <div id="strategyLabSummary" class="result-box">No lab snapshot yet.</div>
      <div class="compact-table" style="margin-top:10px;max-height:160px;">
        <table>
          <thead><tr><th>Stage</th><th>Version</th><th>Score</th><th>Total</th><th>Win</th></tr></thead>
          <tbody id="strategyExperimentBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
<div id="strategyDlgFoot" class="dlgfoot">
  <button class="soft-btn" data-footer-group="strategy" onclick="newStrategyForm()">New</button>
  <button class="soft-btn" data-footer-group="strategy" onclick="cloneStrategyForm()">Clone New Ver</button>
  <button class="primary-btn" data-footer-group="strategy" onclick="validateStrategyForm()">Validate</button>
  <button class="accent-btn" data-footer-group="strategy" onclick="saveStrategyForm()">Save</button>
  <button class="good-btn" data-footer-group="strategy" onclick="promoteStrategyForm()">Promote</button>
  <button class="danger-btn" data-footer-group="strategy" onclick="deleteStrategyForm()">Delete</button>
  <button class="primary-btn" data-footer-group="strategy" onclick="insertStrategyToChart()">Insert To Chart</button>
  <button class="good-btn" data-footer-group="strategy" onclick="runPreciseEvaluation()">Precise PnL</button>
  <button class="good-btn" data-footer-group="strategy" onclick="compareSelectedStrategies()">A/B Compare</button>
  <button class="accent-btn" data-footer-group="automation" onclick="runBatchSearch()">Batch Search</button>
</div>
</div></div>''')
    h.append('<script>')
    h.append(f"var activeParams = {jdump(DEFAULT_PARAMS)};")
    h.append(f"var conditionRowSample = {jdump(CONDITION_ROW_SAMPLE)};")
    h.append('''
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
var simRangeStart = null;     // 선택 구간 시작 인덱스 (null이면 전체)
var simRangeEnd = null;       // 선택 구간 끝 인덱스
var rangeSelectMode = false;  // 구간 선택 진행 중 여부
var rangeClickStage = 0;      // 0=대기, 1=시작점 찍음
var rangeMarkerHandle = null; // 구간 표시 마커 핸들
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
    body: "이 화면은 3개 축으로 분리됩니다.\\n\\n전략작성: 진입식, 청산식, 파라미터를 저장하고 버전별 성능을 비교합니다.\\n후보발굴: 오늘 시장에서 강했던 종목군을 자동 수집하고, 현재 전략들이 어떤 종목에 잘 맞는지 우선순위를 만듭니다.\\n자동탐색: 종목, 타임프레임, 구간을 반복 백테스트하여 성과가 좋은 후보 전략을 누적합니다.\\n\\n권장 사용 순서\\n1. 후보발굴 탭에서 Build Universe\\n2. 후보발굴 탭에서 Build Recommendations\\n3. 추천 상위 종목을 차트에 넣어 플레이로 진입 타이밍 확인\\n4. 전략작성 탭에서 Precise PnL과 A/B Compare로 버전 비교\\n5. 자동탐색 탭에서 장시간 반복 실행 후 candidate만 승급 검토"
  },
  strategy_definition: {
    title: "Strategy Definition",
    body: "무엇을 하나요\\n진입식, 청산식, 수량, 손절/익절, 파라미터를 하나의 버전으로 저장합니다. 원본은 남기고 Clone New Ver로 개선판을 만듭니다.\\n\\n해석 방법\\nEntry와 Exit은 실제 백테스트 수식입니다. zigzag_trend, zigzag_turn_up, supertrend, obv, macd, vwma, jma 등 현재 계산된 값을 그대로 사용할 수 있습니다.\\n\\n활용 팁\\n가급적 한 번에 많은 조건을 넣지 말고, 핵심 게이트 1개를 추가한 새 버전으로 저장한 뒤 비교하십시오. 예: v0.2.0 -> v0.2.1(+ zigzag gate)."
  },
  validation: {
    title: "Validation",
    body: "무엇을 검증하나요\\n수식 문법 오류, 사용 가능한 변수명, 전략 구조를 먼저 확인합니다.\\n\\n통과 후 의미\\n검증 통과는 '실행 가능한 수식'이라는 뜻입니다. 아직 수익 전략이라는 뜻은 아닙니다. 통과 후 Insert To Chart와 Precise PnL로 실제 성능을 확인해야 합니다.\\n\\n권장 절차\\nValidate -> Save -> Insert To Chart -> 플레이로 신호 시점 확인 -> Precise PnL 확인"
  },
  universe: {
    title: "Universe Builder",
    body: "무엇을 분석했나요\\n등락률 상위, 거래대금 상위에서 오늘 시장 주도 후보를 자동으로 모읍니다. 같은 종목이 여러 조건에 동시에 잡히면 더 높은 점수를 받습니다. 필요하면 일봉 기반 분석도 함께 붙습니다.\\n\\n화면 해석\\nCandidates: 모인 전체 후보 수\\nTV / CHG / Dual: 거래대금 상위, 등락률 상위, 두 조건 동시 충족 수\\nScore: 주도주 후보 점수\\nTags: tv_top, chg_top, dual_top 같은 출처 태그\\n\\n활용 방법\\n이 표는 '오늘 무엇을 더 깊게 볼지'를 정하는 1차 필터입니다. 점수와 태그가 강한 종목을 추천 생성이나 워커 탐색 대상으로 넘기십시오."
  },
  recommendation: {
    title: "Recommendation Builder",
    body: "무엇을 분석했나요\\nUniverse 후보 종목마다 여러 전략, 타임프레임, 윈도우 구간을 돌려 현재 데이터에서 가장 잘 맞는 조합을 찾습니다.\\n\\n화면 해석\\nReco: 종합 추천 점수. 주도주 점수와 전략 성과 점수를 합친 값입니다.\\nLeader: 종목 자체의 시장 주도 점수입니다.\\nStrategy: 그 종목에서 가장 잘 맞았던 전략 버전입니다.\\nWin: 선택된 조합의 승률입니다.\\n전략수익률: 선택된 전략 조합의 누적 백테스트 수익률입니다.\\n\\n활용 방법\\nReco 상위 종목부터 차트에 넣고, 실제 플레이로 진입 타이밍을 검증하십시오. 단순히 Win만 보지 말고 어떤 전략 버전이 반복적으로 상위에 뜨는지, 전략수익률이 실제로 플러스인지도 같이 보십시오."
  },
  condition_builder: {
    title: "Condition Search",
    body: "무엇을 하나요\\n키움 조건검색처럼 종목을 뽑는 검색식을 저장하는 영역입니다. 아직 1차 구현이므로 우측 조건행 대신 Rows JSON으로 조건 줄을 입력합니다.\\n\\n핵심 원칙\\n여기서는 매수/매도 판단을 하지 않습니다. 오직 특정 시점까지의 데이터로 후보 종목을 뽑는 조건만 정의합니다.\\n\\nRows JSON 예시\\nA: box_range_pct\\nB: base_candle\\nC: zigzag_turn_up\\nExpr: A and B and C"
  },
  /* llm_workspace: {
    title: "LLM Workspace",
    body: "무엇을 하나요\n일반 사용자가 JSON 구조를 직접 만지지 않고도 조건검색, 성과검증, 상승요인분석, 종목추천 작업을 시작하도록 돕는 반자동 작업화면입니다.\n\n동작 방식\n1. 자연어로 원하는 작업을 적습니다.\n2. 프롬프트 생성으로 외부 LLM에 붙일 문장을 만듭니다.\n3. 외부 LLM이 JSON만 반환하게 합니다.\n4. 반환 JSON을 다시 붙여 넣고 검증합니다.\n5. 검증 후 실행을 누르면 기존 고급 엔진에 연결됩니다.\n\n의미\n사용자는 쉬운 자연어 입력만 다루고, 내부적으로는 기존 Condition Search / Validation / Recommendation Builder 구조를 그대로 재사용합니다."
  },
  llm_manual_flow: {
    title: "수동 LLM 연동",
    body: "왜 수동인가요\n당분간 비용과 실패제어를 위해 외부 LLM API 자동연동 대신 사람 복붙 방식을 사용합니다.\n\n사용 순서\n1. 간편작업에서 메뉴를 고릅니다.\n2. 프리셋 버튼으로 예시 문장을 넣거나 직접 자연어를 적습니다.\n3. 프롬프트 생성을 누릅니다.\n4. 생성된 프롬프트를 복사해 외부 LLM에 붙입니다.\n5. 외부 LLM이 JSON만 반환하게 합니다.\n6. 그 JSON을 다시 붙이고 JSON 검증을 누릅니다.\n7. 검증 후 실행을 누르면 기존 화면 값이 채워지고 실제 검증/분석이 수행됩니다.\n\n권장\n처음에는 조건검색이나 성과검증부터 시작하고, 충분히 익숙해진 뒤 고급 화면에서 세부 JSON과 복합 타임프레임을 다루는 방식이 좋습니다."
  },
  }, */
  llm_workspace: {
    title: "LLM Workspace",
    body: "Purpose\\nA simplified workspace for users who should not edit raw JSON directly.\\n\\nFlow\\n1. Describe the job in natural language.\\n2. Generate a prompt for an external LLM.\\n3. Ask the LLM to return JSON only.\\n4. Paste the JSON back here and validate it.\\n5. Apply or run it through the existing advanced engines."
  },
  llm_manual_flow: {
    title: "Manual LLM Flow",
    body: "Why manual\\nThis avoids direct API cost and lets a human confirm each step.\\n\\nSequence\\n1. Choose a task in the simple workspace.\\n2. Use a preset or write your own request.\\n3. Generate the external prompt.\\n4. Paste it into an external LLM.\\n5. Ask for JSON only.\\n6. Paste the JSON back here.\\n7. Validate, apply, and run."
  },
  condition_validation: {
    title: "Condition Validation",
    body: "무엇을 검증하나요\\n지정한 날짜/시각 이전까지의 데이터만 사용해 조건검색을 실행하고, 선택된 종목들의 이후 성과를 측정합니다.\\n\\n현재 1차 지표\\n최고수익률: 검색 시점 이후 당일 고가 기준 최대 상승폭\\n전략수익률: 같은 구간에 선택 전략을 적용했을 때의 누적 수익률\\n\\n중요\\n이 검증기는 현재 OHLCV 재현형 조건식 기준입니다. 과거 거래대금 상위/프로그램/섹터 시점 재현은 별도 스냅샷 축적 기능이 추가되어야 완전해집니다."
  },
  top_riser_study: {
    title: "Top Riser Study",
    body: "무엇을 하나요\\n특정 일자의 급등 상위 종목들에서 공통 상승요인을 추출하고, 다른 날짜 후보군이 그 요인과 얼마나 비슷한지 점수화합니다.\\n\\n주의\\n현재 1차는 일봉 종가 확정 기준 유사도 분석입니다. 즉 '같은 날 종가 기준 구조가 반복되는가'를 보는 연구용 분석기이며, 장 시작 전 예측기로 쓰려면 추가로 시점 고정 intraday 스냅샷이 필요합니다.\\n\\n해석\\nSimilarity Score가 높고 실제 등락률도 높은 종목이 반복되면, 그 급등 구조가 재현 가능성이 있다는 뜻입니다."
  },
  evaluation: {
    title: "Evaluation Summary",
    body: "무엇을 보여주나요\\n현재 전략을 현재 종목/구간/타임프레임에 적용했을 때의 거래 목록과 총 성능입니다.\\n\\n화면 해석\\nTrades: 총 거래 수\\nWin: 승률\\nTotal: 전체 누적 수익률\\nAvg: 1회 평균 수익률\\nPF: Profit Factor, 총 이익 / 총 손실\\n\\n활용 방법\\n승률만 보지 말고 Total, PF, 거래 수를 같이 보십시오. 승률이 높아도 거래 수가 너무 적거나 PF가 낮으면 실전성이 약합니다."
  },
  compare: {
    title: "Version Compare",
    body: "무엇을 하나요\\n기준 전략과 개선 전략을 같은 데이터 구간에서 직접 비교합니다.\\n\\n활용 방법\\n원본 전략을 baseline으로 두고, 조건 1개만 추가한 버전을 candidate로 두십시오. 개선 여부는 Total, Win, PF, Max 손실 방어 관점에서 함께 판단해야 합니다.\\n\\n권장 원칙\\n원본은 보존하고, 개선판은 새 버전으로 저장합니다. 좋아진 버전만 candidate 또는 promoted로 올리십시오."
  },
  worker: {
    title: "Lab Worker",
    body: "무엇을 하나요\\n선택된 종목, 타임프레임, 구간 조합을 반복 실행하여 좋은 결과를 자동 적재합니다. 수동 검증 전에 후보를 많이 모으는 엔진입니다.\\n\\n버튼 의미\\nRun Once: 현재 설정으로 1회만 실행\\nStart Loop: interval 초마다 반복 실행\\nAuto Candidate: 성과가 좋은 결과를 candidate 전략으로 자동 저장\\n\\n활용 방법\\nUniverse/Recommendation 결과를 Use In Worker로 넘긴 뒤 루프를 돌리면, 주도 종목군 중심으로 전략 적합도를 계속 누적할 수 있습니다."
  },
  snapshot: {
    title: "Lab Snapshot",
    body: "무엇을 보여주나요\\n현재까지 누적된 전략 수, 실험 수, 그리고 상위 성과 실험 목록입니다.\\n\\n화면 해석\\nStage: draft / candidate / promoted 상태\\nScore: 내부 종합 점수\\nTotal: 누적 수익률\\nWin: 승률\\n\\n활용 방법\\n반복적으로 상위에 남는 전략만 골라 Strategy 탭에서 상세 검증하십시오. snapshot은 자동탐색의 결과판이며, 최종 의사결정은 차트 삽입과 플레이 검증까지 끝난 뒤에 하십시오."
  }
};
var llmTask = "condition_search";
var llmWorkspaceDefs = {
  condition_search: {
    title: "Condition Search",
    target: "Condition Search form",
    subtle: "Describe a candidate-discovery idea and generate JSON for the condition builder.",
    guide: "Goal\\nCreate a point-in-time candidate filter.\\n\\nUse it for\\nBox breakout, base candle, zigzag turn-up, OBV or MACD confirmation ideas.\\n\\nResult\\nThe payload is applied to Condition Search so you can refine rows in advanced mode if needed.",
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
    guide: "Goal\\nTest whether a condition really finds useful candidates.\\n\\nUse it for\\nFixing a date and time, searching only with prior data, then measuring max run-up and strategy return.\\n\\nResult\\nCondition Search and Condition Validation will both be filled and can run in one step.",
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
    guide: "Goal\\nStudy repeated rally structures.\\n\\nUse it for\\nComparing source-date top risers against another date's candidate list.\\n\\nResult\\nThe Top Riser Study panel will be filled and can run immediately.",
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
    guide: "Goal\\nNarrow the market first, then rank the best symbols with strategy fit.\\n\\nUse it for\\nTomorrow watchlist building, leader-stock filtering, and strategy-fit ranking.\\n\\nResult\\nUniverse Builder and Recommendation Builder will be filled and can run as one workflow.",
    presets: [
      { label: "Tomorrow Watchlist", text: "Create a recommendation JSON that builds a universe from top trade value and top change-rate stocks, then ranks the top 10 for tomorrow using t360 and t720." },
      { label: "Leader Focus", text: "Create a recommendation JSON focused on sector leaders with strong trade value and recent profitable strategy fit." },
      { label: "Conservative Top5", text: "Create a recommendation JSON that keeps a broad universe but recommends only the top 5 symbols with both good win rate and strategy return." }
    ]
  }
};
var strategyParamDefs = [
  { key: "ma_periods_0", label: "MA Fast", type: "int", value: activeParams.ma_periods[0] || 5 },
  { key: "ma_periods_1", label: "MA Mid", type: "int", value: activeParams.ma_periods[1] || 20 },
  { key: "ma_periods_2", label: "MA Slow", type: "int", value: activeParams.ma_periods[2] || 60 },
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
  if (menuHint) menuHint.innerText = "현재 작업: " + def.title + "\\n프리셋으로 예시 문장을 넣은 뒤 그대로 다듬어 사용하면 됩니다.";
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

/* legacy prompt builder disabled: replaced by the validated LLM workspace flow below
function extractTimeframeHints(text, task) {
  var raw = String(text || "");
  var hits = [];
  function push(tf, label, order) {
    if (!tf) return;
    if (hits.some(function(item) { return item.tf === tf && item.label === label; })) return;
    hits.push({ tf: tf, label: label, order: order });
  }
  raw.replace(/일봉/g, function(match, offset) { push("d1", "daily", offset); return match; });
  raw.replace(/주봉/g, function(match, offset) { push("w1", "weekly", offset); return match; });
  raw.replace(/월봉/g, function(match, offset) { push("mo1", "monthly", offset); return match; });
  raw.replace(/(\d+)\s*분봉/g, function(match, num, offset) { push("m" + num, num + "m", offset); return match; });
  raw.replace(/(\d+)\s*분/g, function(match, num, offset) { push("m" + num, num + "m", offset); return match; });
  raw.replace(/(\d+)\s*틱봉/g, function(match, num, offset) { push("t" + num, num + "tick", offset); return match; });
  raw.replace(/(\d+)\s*틱/g, function(match, num, offset) { push("t" + num, num + "tick", offset); return match; });
  raw.replace(/\bdaily\b/gi, function(match, offset) { push("d1", "daily", offset); return match; });
  raw.replace(/\bweekly\b/gi, function(match, offset) { push("w1", "weekly", offset); return match; });
  raw.replace(/\bmonthly\b/gi, function(match, offset) { push("mo1", "monthly", offset); return match; });
  raw.replace(/\b(\d+)\s*min(?:ute)?\b/gi, function(match, num, offset) { push("m" + num, num + "m", offset); return match; });
  raw.replace(/\b(\d+)\s*tick\b/gi, function(match, num, offset) { push("t" + num, num + "tick", offset); return match; });
  hits.sort(function(a, b) { return a.order - b.order; });

  function tfRank(tf) {
    if (/^t\d+$/i.test(tf)) return parseInt(tf.slice(1), 10);
    if (/^m\d+$/i.test(tf)) return 1000 + parseInt(tf.slice(1), 10);
    if (tf === "d1") return 100000;
    if (tf === "w1") return 200000;
    if (tf === "mo1") return 300000;
    return 999999;
  }

  var firstTf = hits.length ? hits[0].tf : "";
  var executionTf = "";
  hits.forEach(function(item) {
    if (!executionTf || tfRank(item.tf) < tfRank(executionTf)) executionTf = item.tf;
  });
  if (!executionTf) {
    executionTf = task === "stock_recommendation" ? "t360" : "m5";
  }
  var recoTfs = hits
    .map(function(item) { return item.tf; })
    .filter(function(tf) { return /^t\d+$/i.test(tf) || /^m\d+$/i.test(tf) || tf === "d1" || tf === "w1"; });
  if (!recoTfs.length && task === "stock_recommendation") recoTfs = ["t360", "t720"];
  return {
    requested: hits.map(function(item) { return item.tf; }),
    row_example_tf: firstTf || executionTf,
    execution_tf: executionTf,
    recommendation_tfs: recoTfs
  };
}

function buildLLMJsonSchema(task, hints) {
  hints = hints || {};
  var searchTf = hints.execution_tf || (task === "stock_recommendation" ? "t360" : "m5");
  var rowTf = hints.row_example_tf || searchTf;
  var recoTFs = (hints.recommendation_tfs && hints.recommendation_tfs.length ? hints.recommendation_tfs.join(",") : "t360,t720");
  if (task === "performance_validation") {
    return '{\\n'
      + '  "task": "performance_validation",\\n'
      + '  "condition": {\\n'
      + '    "name": "condition_name",\\n'
      + '    "version": "v0.1.0",\\n'
      + '    "stage": "draft",\\n'
      + '    "search_timeframe": "' + searchTf + '",\\n'
      + '    "description": "description",\\n'
      + '    "expression": "A and B and C",\\n'
      + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "' + rowTf + '", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\\n'
      + '  },\\n'
      + '  "validation": {\\n'
      + '    "search_date": "YYYY-MM-DD",\\n'
      + '    "search_time": "09:10",\\n'
      + '    "timeframe": "' + searchTf + '",\\n'
      + '    "bars": 500,\\n'
      + '    "top_n": 15,\\n'
      + '    "symbols": "",\\n'
      + '    "strategy_id": ""\\n'
      + '  }\\n'
      + '}';
  }
  if (task === "top_riser_study") {
    return '{\\n'
      + '  "task": "top_riser_study",\\n'
      + '  "config": {\\n'
      + '    "source_date": "YYYY-MM-DD",\\n'
      + '    "top_n": 10,\\n'
      + '    "target_date": "YYYY-MM-DD",\\n'
      + '    "candidate_limit": 10,\\n'
      + '    "symbols": ""\\n'
      + '  }\\n'
      + '}';
  }
  if (task === "stock_recommendation") {
    return '{\\n'
      + '  "task": "stock_recommendation",\\n'
      + '  "universe": {\\n'
      + '    "limit_each": 30,\\n'
      + '    "top_n": 20,\\n'
      + '    "include_trade_value": true,\\n'
      + '    "include_change_rate": true,\\n'
      + '    "analyze_daily": true\\n'
      + '  },\\n'
      + '  "recommendation": {\\n'
      + '    "timeframes": "' + recoTFs + '",\\n'
      + '    "top_n": 10,\\n'
      + '    "universe_limit": 20,\\n'
      + '    "bars": 1000,\\n'
      + '    "window_1": 120,\\n'
      + '    "window_2": 240,\\n'
      + '    "window_3": 480\\n'
      + '  }\\n'
      + '}';
  }
  return '{\\n'
    + '  "task": "condition_search",\\n'
    + '  "condition": {\\n'
    + '    "name": "condition_name",\\n'
    + '    "version": "v0.1.0",\\n'
    + '    "stage": "draft",\\n'
    + '    "search_timeframe": "' + searchTf + '",\\n'
    + '    "description": "description",\\n'
    + '    "expression": "A and B and C",\\n'
    + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "' + rowTf + '", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\\n'
    + '  }\\n'
    + '}';
}

function generateLLMPrompt() {
  var def = llmWorkspaceDefs[llmTask] || llmWorkspaceDefs.condition_search;
  var userText = (document.getElementById("llmUserPrompt").value || "").trim();
  if (!userText) {
    document.getElementById("llmJsonSummary").innerText = "먼저 자연어 작업 설명을 입력하거나 프리셋을 눌러주세요.";
    document.getElementById("llmJsonSummary").style.color = "#ffb74d";
    return;
  }
  var tfHints = extractTimeframeHints(userText, llmTask);
  var tfHintText = tfHints.requested.length ? tfHints.requested.join(", ") : "none";
  var multiTfRule = tfHints.requested.length > 1
    ? "- Multiple timeframe hints were detected. Keep timeframe per row aligned to the user's request. Do not collapse all rows into a single timeframe.\\n"
    : "";
  var prompt = ""
    + "You are assisting a Korean trading lab user.\\n"
    + "Convert the user's request into JSON only.\\n"
    + "Do not explain. Do not wrap in markdown. Return a single valid JSON object only.\\n"
    + "Preserve the user's intent and fill unspecified numeric defaults conservatively.\\n"
    + "Current task: " + llmTask + " (" + def.title + ").\\n"
    + "Detected timeframe hints from user: " + tfHintText + ".\\n"
    + "Output schema:\\n"
    + buildLLMJsonSchema(llmTask, tfHints) + "\\n\\n"
    + "Rules:\\n"
    + "- Use only fields from the schema.\\n"
    + "- Keep expression labels consistent with rows labels.\\n"
    + "- If the user mentions validation date or time, put it into validation/config.\\n"
    + "- Timeframe values should be like m5, m15, t360, t720, d1.\\n"
    + "- If the user explicitly asks for daily, weekly, monthly, minute, or tick timeframe, follow that request instead of any default example.\\n"
    + multiTfRule
    + "- If the user asks for stock recommendation, return both universe and recommendation blocks.\\n"
    + "- If unsure, keep symbols as an empty string.\\n\\n"
    + "User request in Korean:\\n"
    + userText;
  document.getElementById("llmGeneratedPrompt").value = prompt;
  document.getElementById("llmJsonSummary").innerText = "프롬프트가 생성되었습니다. 외부 LLM에 붙여 넣고 JSON만 다시 가져오세요.";
  document.getElementById("llmJsonSummary").style.color = "#53dfd0";
}

*/
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
  return lines.join("\\n");
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
  return lines.join("\\n");
}

function extractRequestedTimeframesFromText(text) {
  var raw = String(text || "").toLowerCase();
  var tfs = [];
  function push(tf) {
    if (tf && tfs.indexOf(tf) < 0) tfs.push(tf);
  }
  if (raw.indexOf("\uC77C\uBD09".toLowerCase()) >= 0 || raw.indexOf("daily") >= 0) push("d1");
  if (raw.indexOf("\uC8FC\uBD09".toLowerCase()) >= 0 || raw.indexOf("weekly") >= 0) push("w1");
  if (raw.indexOf("\uC6D4\uBD09".toLowerCase()) >= 0 || raw.indexOf("monthly") >= 0) push("mo1");
  raw.replace(/(\d+)\s*(?:\uBD84\uBD09|\uBD84|min(?:ute)?s?)/gi, function(_, num) { push("m" + num); return _; });
  raw.replace(/(\d+)\s*(?:\uD2F1\uBD09|\uD2F1|tick(?:s)?)/gi, function(_, num) { push("t" + num); return _; });
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
  box.innerText = lines.map(function(line) { return "- " + line; }).join("\\n");
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
    if (mentionsAny(["box", "\uBC15\uC2A4\uAD8C"]) && indicators.indexOf("box_range_pct") < 0) {
      lines.push("사용자 요청에 박스권/box가 있지만 rows에 box_range_pct가 없습니다. 현재 JSON은 박스권 정의 없이 단순 돌파일 수 있습니다.");
    }
    if (mentionsAny(["base candle", "\uAE30\uC900\uBD09"]) && indicators.indexOf("base_candle") < 0) {
      lines.push("사용자 요청에 기준봉/base candle이 있지만 rows에 base_candle이 없습니다.");
    }
    if (mentionsAny(["zigzag"]) && !hasAnyIndicator(["zigzag_turn_up", "zigzag_trend"])) {
      lines.push("사용자 요청에 zigzag가 있지만 rows에 zigzag_turn_up 또는 zigzag_trend가 없습니다.");
    }
    if (mentionsAny(["supertrend"]) && !(hasAnyIndicator(["supertrend_state"]) || String((cond || {}).expression || "").toLowerCase().indexOf("supertrend") >= 0)) {
      lines.push("사용자 요청에 supertrend가 있지만 JSON에 supertrend 관련 조건이 보이지 않습니다.");
    }
    if (mentionsAny(["09:00", "09:10", "09:20", "\uC624\uC804 09", "\uC774\uD6C4", "after 09"]) && payload.task === "condition_search") {
      lines.push("시간 제약이 자연어에 있으나 condition_search 스키마에는 시각 필드가 없습니다. 성과검증이나 별도 시간 row/notes 보완이 필요할 수 있습니다.");
    }
  }

  if ((requestLower.indexOf("daily only") >= 0 || requestLower.indexOf("\uC77C\uBD09") >= 0 && requestLower.indexOf("only") >= 0 || requestLower.indexOf("\uB9CC") >= 0) && jsonTfs.some(function(tf) { return tf && tf !== "d1"; })) {
    lines.push("사용자 요청은 일봉 only에 가깝지만 JSON에는 d1 외 timeframe이 포함되어 있습니다.");
  }

  if (reqTfSet.length === 1) {
    var only = reqTfSet[0];
    var unexpected = jsonTfs.filter(function(tf) { return tf && tf !== only; });
    if (unexpected.length) {
      lines.push("사용자 요청 timeframe은 " + only + " 하나로 보이는데 JSON에 추가 timeframe이 포함되어 있습니다: " + unexpected.join(", "));
    }
  }

  if ((payload.task === "condition_search" || payload.task === "performance_validation") && cond && rows.length === 1 && mentionsAny(["box", "\uBC15\uC2A4\uAD8C", "and", "\uADF8\uB9AC\uACE0"])) {
    lines.push("자연어 요청 대비 row 수가 매우 적습니다. 핵심 조건 일부가 누락되었는지 확인이 필요합니다.");
  }
  return lines;
}

function buildLLMJsonSchema(task) {
  if (task === "performance_validation") {
    return '{\\n'
      + '  "task": "performance_validation",\\n'
      + '  "condition": {\\n'
      + '    "name": "condition_name",\\n'
      + '    "version": "v0.1.0",\\n'
      + '    "stage": "draft",\\n'
      + '    "search_timeframe": "<requested_main_tf>",\\n'
      + '    "description": "description",\\n'
      + '    "expression": "A and B and C",\\n'
      + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "<requested_or_row_tf>", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\\n'
      + '  },\\n'
      + '  "validation": {\\n'
      + '    "search_date": "YYYY-MM-DD",\\n'
      + '    "search_time": "09:10",\\n'
      + '    "timeframe": "<execution_tf>",\\n'
      + '    "bars": 500,\\n'
      + '    "top_n": 15,\\n'
      + '    "symbols": "",\\n'
      + '    "strategy_id": ""\\n'
      + '  }\\n'
      + '}';
  }
  if (task === "top_riser_study") {
    return '{\\n'
      + '  "task": "top_riser_study",\\n'
      + '  "config": {\\n'
      + '    "source_date": "YYYY-MM-DD",\\n'
      + '    "top_n": 10,\\n'
      + '    "target_date": "YYYY-MM-DD",\\n'
      + '    "candidate_limit": 10,\\n'
      + '    "symbols": ""\\n'
      + '  }\\n'
      + '}';
  }
  if (task === "stock_recommendation") {
    return '{\\n'
      + '  "task": "stock_recommendation",\\n'
      + '  "universe": {\\n'
      + '    "limit_each": 30,\\n'
      + '    "top_n": 20,\\n'
      + '    "include_trade_value": true,\\n'
      + '    "include_change_rate": true,\\n'
      + '    "analyze_daily": true\\n'
      + '  },\\n'
      + '  "recommendation": {\\n'
      + '    "timeframes": "<requested_tf_list>",\\n'
      + '    "top_n": 10,\\n'
      + '    "universe_limit": 20,\\n'
      + '    "bars": 1000,\\n'
      + '    "window_1": 120,\\n'
      + '    "window_2": 240,\\n'
      + '    "window_3": 480\\n'
      + '  }\\n'
      + '}';
  }
  return '{\\n'
    + '  "task": "condition_search",\\n'
    + '  "condition": {\\n'
    + '    "name": "condition_name",\\n'
    + '    "version": "v0.1.0",\\n'
    + '    "stage": "draft",\\n'
    + '    "search_timeframe": "<requested_main_tf>",\\n'
    + '    "description": "description",\\n'
    + '    "expression": "A and B and C",\\n'
    + '    "rows": [ { "indicator": "base_candle", "label": "A", "timeframe": "<requested_or_row_tf>", "lookback": 20, "operator": "is_true", "value": 1, "params": {} } ]\\n'
    + '  }\\n'
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
    + "You are assisting a Korean trading lab user.\\n"
    + "Convert the user's request into JSON only.\\n"
    + "Do not explain. Do not wrap in markdown. Return a single valid JSON object only.\\n"
    + "Do not translate, summarize, simplify, or reinterpret the user's request.\\n"
    + "Treat the user's original text as the source of truth.\\n"
    + "Use the capability list and schema only as constraints and formatting rules.\\n"
    + "Current task: " + llmTask + " (" + def.title + ").\\n\\n"
    + "Capabilities:\\n"
    + buildLLMCapabilitySpec(llmTask) + "\\n\\n"
    + "Validation checklist:\\n"
    + buildLLMExternalValidationChecklist(llmTask) + "\\n\\n"
    + "Output schema:\\n"
    + buildLLMJsonSchema(llmTask) + "\\n\\n"
    + "Rules:\\n"
    + "- Use only fields from the schema.\\n"
    + "- Keep expression labels consistent with rows labels.\\n"
    + "- Preserve the user's timeframe request exactly.\\n"
    + "- If the user requests mixed timeframes, preserve mixed timeframes in rows or config where needed.\\n"
    + "- If the user mentions validation date or time, put it into validation/config.\\n"
    + "- If the request exceeds supported capability, stay within the schema and choose the closest valid structure without inventing unsupported indicators.\\n"
    + "- If the user asks for stock recommendation, return both universe and recommendation blocks.\\n"
    + "- If unsure, keep symbols as an empty string.\\n\\n"
    + "Process requirement:\\n"
    + "- Perform the internal validation checklist silently.\\n"
    + "- Revise the JSON until the checklist passes.\\n"
    + "- After it passes, output JSON only.\\n\\n"
    + "Original user request below. Preserve it semantically and do not rewrite it before converting to JSON.\\n"
    + "BEGIN_USER_REQUEST\\n"
    + userText + "\\n"
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
      + " || ΔTotal " + (deltaTotal >= 0 ? "+" : "") + deltaTotal + "%, ΔWin " + (deltaWin >= 0 ? "+" : "") + deltaWin + "%p, ΔTrades " + (deltaTrades >= 0 ? "+" : "") + deltaTrades;
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
  markerHandle = null; // reset markers on new chart
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
        // first bar of a day (00:00 ~ 00:09) or day-change tick: show date
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

  // 마우스 클릭으로 구간 시작/끝 봉 지정
  try { chart.subscribeClick(onChartClick); } catch (e) {}

  // 구간 선택: 차트 클릭으로 시작/끝 봉 지정 (시리즈 생성 후 연결)
  try {  } catch (e) { console.warn("subscribeClick", e); }

  try {
    var _panes = chart.panes();
    for (var _pi = 1; _pi < _panes.length; _pi++) { _panes[_pi].setHeight(120); }
  } catch (e) { console.warn("pane height", e); }

  window.addEventListener("resize", () => {
    chart.resize(container.clientWidth, container.clientHeight);
  });


}

function applyIndicatorsVisibility() {
  var maVisible = document.getElementById("show_ma").checked;
  maS.forEach(s => s.applyOptions({ visible: maVisible }));

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

// ── 시뮬레이션 구간 선택 (두 번 클릭: 시작 -> 끝) ──
function timeToIndex(t) {
  // simCandles 가 비어있으면 현재 로드된 캔들에서 가장 가까운 인덱스 탐색
  if (typeof t !== "number") return null;
  var best = null, bestDiff = Infinity;
  // cs 의 데이터를 직접 못 읽으므로 마지막 step 응답 캐시 사용
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
  // 구간 설정은 유지하되, 차트는 마지막 봉까지 전체 표시
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
    // 시작>끝이면 스왑
    if (simRangeStart > simRangeEnd) {
      var tmp = simRangeStart; simRangeStart = simRangeEnd; simRangeEnd = tmp;
    }
    // 최소 인덱스 보호 (지표 워밍업 60봉)
    if (simRangeStart < 60) simRangeStart = 60;
    rangeSelectMode = false;
    rangeClickStage = 0;
    var btn = document.getElementById("rangeBtn");
    if (btn) { btn.classList.remove("active"); btn.innerText = "구간선택"; }
    document.getElementById("chartSymbol").innerText = "구간 " + simRangeStart + " ~ " + simRangeEnd + "봉 선택됨";
    // 시작 지점으로 이동
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
    .then(r => r.json())
    .then(d => {
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
    .catch(e => {
      alert("네트워크 오류: " + e);
      document.getElementById("chartSymbol").innerText = "연결 실패";
    });
}

var _rendering = false;
function renderStep() {
  if (simTotal === 0 || _rendering) return;
  _rendering = true;

  fetch("/api/simulation_step?idx=" + simCurrentIdx)
    .then(r => r.json())
    .then(d => {
      _rendering = false;
      if (d.error) return;

      window._lastStepCandles = d.candles;
      cs.setData(d.candles);
      vs.setData(d.volumes);

      if (d.ma) {
        d.ma.forEach((m, idx) => {
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
    .catch(() => { _rendering = false; });
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
    .then(r => r.json())
    .then(d => {
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
  .then(r => r.json())
  .then(d => {
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
  .catch(e => { alert("통신 오류: " + e); });
}

window.onload = function() {
  var now = new Date();
  var yyyy = now.getFullYear();
  var mm = String(now.getMonth() + 1).padStart(2, '0');
  var dd = String(now.getDate()).padStart(2, '0');
  document.getElementById("simDate").value = yyyy + "-" + mm + "-" + dd;
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
};
''')
    h.append('</script></body></html>')
    return '\n'.join(h)


# ═══════════════════════════════════════════════════════════════
#  HTTP 서버 구현
# ═══════════════════════════════════════════════════════════════

def make_simulation_handler(cfg, rest, settings, session):
    store = StrategyStore()
    experiments = ExperimentStore()
    universes = UniverseStore()
    recommendations = RecommendationStore()
    conditions = ConditionStore()
    condition_runs = ConditionRunStore()
    condition_validations = ConditionValidationStore()
    worker = LabWorker(rest, store, experiments)

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
                    self._send(build_simulation_html(), "text/html; charset=utf-8")
                
                elif path == "/api/init_simulation":
                    code = q.get("code", [""])[0]
                    tf = q.get("tf", [""])[0]
                    date = q.get("date", [""])[0]
                    time_val = q.get("time", [""])[0]
                    
                    print(f"\n[시뮬레이션 데이터 조회] 종목코드: {code} | 타임프레임(원본매핑값): {tf}")
                    
                    # 🚨 성공한 원본 로직 규격 그대로 호출 (인자 변동 및 문자열 규격 우회 없음)
                    ohlcv_data = rest.ohlcv(code, tf, 1000)
                        
                    if not ohlcv_data:
                        self._send(jdump({"error": "다운로드된 데이터가 없습니다."}), code=400)
                        return

                    session.init_data(ohlcv_data, tf, date, time_val)
                    session.code = code
                    
                    self._send(jdump({
                        "msg": "OK",
                        "total_bars": len(session.candles),
                        "start_idx": session.start_idx
                    }))
                    
                elif path == "/api/simulation_step":
                    idx = int(q.get("idx", ["60"])[0])
                    if idx >= len(session.candles):
                        idx = len(session.candles) - 1
                    if idx < 0:
                        idx = 0
                        
                    res = session.get_slice(idx)
                    self._send(jdump(res))
                elif path == "/api/strategies":
                    self._send(jdump({"items": store.list()}))
                elif path == "/api/conditions":
                    self._send(jdump({"items": conditions.list()}))
                elif path == "/api/lab":
                    self._send(jdump(build_lab_snapshot(store.list(), experiments.list())))
                elif path == "/api/universe/latest":
                    self._send(jdump({"snapshot": universes.latest()}))
                elif path == "/api/recommendations/latest":
                    self._send(jdump({"snapshot": recommendations.latest()}))
                elif path == "/api/condition_runs/latest":
                    self._send(jdump({"snapshot": condition_runs.latest()}))
                elif path == "/api/condition_validations/latest":
                    self._send(jdump({"snapshot": condition_validations.latest()}))
                elif path == "/api/worker_status":
                    self._send(jdump(worker.status()))
                    
                else:
                    self._send("404 Not Found", "text/plain", 404)
                    
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send(jdump({"error": str(e)}), code=500)

        def do_POST(self):
            u = urlparse(self.path)
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
                
            try:
                if u.path == "/api/strategies":
                    strategy = normalize_strategy_payload(data, session.code)
                    
                    if False and not session.candles:
                        self._send(jdump({"error": "시뮬레이션 데이터가 없습니다."}), code=400)
                        return
                        
                    err_entry = validate_expression(strategy.get("entry_expr", ""))
                    err_exit = validate_expression(strategy.get("exit_expr", ""))
                    if err_entry:
                        self._send(jdump({"error": "entry validation failed: " + err_entry}), code=400)
                        return
                    if err_exit:
                        self._send(jdump({"error": "exit validation failed: " + err_exit}), code=400)
                        return
                    self._send(jdump(store.upsert(strategy)))
                elif u.path == "/api/conditions":
                    condition = normalize_condition_payload(data)
                    err_expr = validate_condition_expression(condition.get("expression", ""), condition.get("rows", []))
                    if err_expr:
                        self._send(jdump({"error": "condition validation failed: " + err_expr}), code=400)
                        return
                    self._send(jdump(conditions.upsert(condition)))
                elif u.path == "/api/conditions/delete":
                    cid = data.get("id")
                    if not cid:
                        self._send(jdump({"error": "id is required"}), code=400)
                        return
                    conditions.delete(cid)
                    self._send(jdump({"msg": "OK"}))
                elif u.path == "/api/conditions/validate":
                    condition = normalize_condition_payload(data)
                    err_expr = validate_condition_expression(condition.get("expression", ""), condition.get("rows", []))
                    if err_expr:
                        self._send(jdump({"valid": False, "error": err_expr}))
                    else:
                        self._send(jdump({"valid": True, "condition": condition}))
                elif u.path == "/api/strategies/delete":
                    sid = data.get("id")
                    if not sid:
                        self._send(jdump({"error": "id is required"}), code=400)
                        return
                    store.delete(sid)
                    self._send(jdump({"msg": "OK"}))
                elif u.path == "/api/strategies/promote":
                    strategy = normalize_strategy_payload(data, session.code)
                    if not strategy.get("id"):
                        self._send(jdump({"error": "id is required"}), code=400)
                        return
                    strategy["stage"] = "promoted"
                    self._send(jdump(store.upsert(strategy)))
                elif u.path == "/api/strategies/validate":
                    strategy = normalize_strategy_payload(data, session.code)
                    err_entry = validate_expression(strategy.get("entry_expr", ""))
                    err_exit = validate_expression(strategy.get("exit_expr", ""))
                    if err_entry:
                        self._send(jdump({"valid": False, "error": err_entry}))
                    elif err_exit:
                        self._send(jdump({"valid": False, "error": err_exit}))
                    else:
                        self._send(jdump({"valid": True, "strategy": strategy}))
                elif u.path == "/api/strategy_status":
                    if not session.candles:
                        self._send(jdump({"ready": False, "message": "no simulation data"}), code=400)
                        return
                    idx = int(data.get("idx", 0))
                    result = evaluate_strategy_status(rest, settings, session, data.get("strategy") or {}, idx)
                    self._send(jdump(result))
                elif u.path == "/api/backtest":
                    code = data.get("code", "")
                    tf = data.get("tf", "")
                    strategy = data.get("strategy")
                    range_start = data.get("range_start")
                    range_end = data.get("range_end")
                    apply_view_params = bool(data.get("apply_view_params"))

                    if not session.candles:
                        self._send(jdump({"error": "simulation data is empty"}), code=400)
                        return

                    res = run_session_backtest(
                        rest,
                        settings,
                        session,
                        code,
                        tf,
                        strategy,
                        range_start=range_start,
                        range_end=range_end,
                        apply_view_params=apply_view_params,
                    )
                    self._send(jdump(res))
                elif u.path == "/api/experiments/run":
                    strategy = normalize_strategy_payload(data.get("strategy") or {}, session.code)
                    range_start = data.get("range_start")
                    range_end = data.get("range_end")
                    limit = int(data.get("limit") or 12)
                    batch = run_batch_experiment(
                        rest,
                        settings,
                        session,
                        strategy,
                        range_start=range_start,
                        range_end=range_end,
                        limit=limit,
                    )
                    persisted = []
                    for item in batch.get("results", []):
                        persisted.append(experiments.append({
                            "type": "batch_search",
                            "code": strategy.get("code") or session.code,
                            "tf": session.tf,
                            "range_start": range_start,
                            "range_end": range_end,
                            "base_strategy": batch.get("base_strategy"),
                            "strategy": item.get("strategy"),
                            "summary": item.get("summary"),
                        }))
                    snapshot = build_lab_snapshot(store.list(), experiments.list())
                    self._send(jdump({
                        "msg": "OK",
                        "tested": batch.get("tested", 0),
                        "results": persisted,
                        "snapshot": snapshot,
                    }))
                elif u.path == "/api/universe/run":
                    snapshot = build_candidate_universe(rest, settings, data.get("config") or {})
                    stored = universes.append(snapshot)
                    self._send(jdump({"msg": "OK", "snapshot": stored}))
                elif u.path == "/api/recommendations/run":
                    universe_snapshot = universes.latest()
                    if not universe_snapshot:
                        self._send(jdump({"error": "universe snapshot is empty. build universe first."}), code=400)
                        return
                    snapshot = build_recommendations(
                        rest,
                        settings,
                        store.list(),
                        universe_snapshot,
                        data.get("config") or {},
                    )
                    stored = recommendations.append(snapshot)
                    self._send(jdump({"msg": "OK", "snapshot": stored}))
                elif u.path == "/api/condition_validations/run":
                    condition = normalize_condition_payload(data.get("condition") or {})
                    err_expr = validate_condition_expression(condition.get("expression", ""), condition.get("rows", []))
                    if err_expr:
                        self._send(jdump({"error": "condition validation failed: " + err_expr}), code=400)
                        return
                    cfg_payload = normalize_condition_validation_config(data.get("config") or {})
                    strategy_id = cfg_payload.get("strategy_id")
                    strategy = store.get(strategy_id) if strategy_id else {}
                    snapshot = run_condition_validation(
                        rest,
                        settings,
                        condition,
                        strategy or {},
                        cfg_payload,
                        latest_universe=universes.latest(),
                        fallback_code=session.code,
                    )
                    condition_runs.append(snapshot.get("run_snapshot") or {})
                    stored = condition_validations.append(snapshot)
                    self._send(jdump({"msg": "OK", "snapshot": stored}))
                elif u.path == "/api/top_riser_study/run":
                    cfg_payload = data.get("config") or {}
                    symbols = cfg_payload.get("symbols")
                    if isinstance(symbols, str):
                        symbols = [x.strip() for x in symbols.split(",") if x.strip()]
                    study = run_top_riser_study(
                        rest,
                        settings,
                        universes.list(),
                        str(cfg_payload.get("source_date") or "").strip(),
                        str(cfg_payload.get("target_date") or "").strip(),
                        top_n=int(cfg_payload.get("top_n") or 10),
                        candidate_limit=int(cfg_payload.get("candidate_limit") or 10),
                        symbols=symbols,
                    )
                    self._send(jdump({"msg": "OK", "study": study}))
                elif u.path == "/api/worker/start":
                    strategy = normalize_strategy_payload(data.get("strategy") or {}, session.code)
                    if not strategy.get("entry_expr") or not strategy.get("exit_expr"):
                        self._send(jdump({"error": "strategy entry/exit is required"}), code=400)
                        return
                    err_entry = validate_expression(strategy.get("entry_expr", ""))
                    err_exit = validate_expression(strategy.get("exit_expr", ""))
                    if err_entry:
                        self._send(jdump({"error": "entry validation failed: " + err_entry}), code=400)
                        return
                    if err_exit:
                        self._send(jdump({"error": "exit validation failed: " + err_exit}), code=400)
                        return
                    status = worker.start(data.get("config") or {}, strategy)
                    self._send(jdump(status))
                elif u.path == "/api/worker/stop":
                    self._send(jdump(worker.stop()))
                else:
                    self._send("404 Not Found", "text/plain", 404)
            except Exception as e:
                self._send(jdump({"error": str(e)}), code=500)

    return Handler


# ═══════════════════════════════════════════════════════════════
#  엔트리포인트
# ═══════════════════════════════════════════════════════════════

def main():
    cfg = Config()
    settings = Settings()
    rest = KiwoomREST(cfg)
    
    print("=" * 60)
    print("  What You See Is What You Trade - 시뮬레이션 서버 구동")
    print("=" * 60)
    
    try:
        rest.issue_token()
        print("[OK] Kiwoom API 토큰 발급 완료")
    except Exception as e:
        print("[경고] API 토큰 발급 실패:", e)
        
    session = SimulationSession()
    server = ThreadingHTTPServer(("localhost", 5000), make_simulation_handler(cfg, rest, settings, session))
    
    url = "http://localhost:5000"
    print("  시뮬레이터 접속 주소:", url)
    print("=" * 60)
    
    import webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n시뮬레이션 서버 종료 중...")
        server.shutdown()


if __name__ == "__main__":
    main()
