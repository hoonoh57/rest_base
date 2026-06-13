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
            "trendline": 995.0,
            "prev_trendline": 994.0,
            "trendline_slope": 0.5,
            "prev_trendline_slope": 0.4,
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
        ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma, zigzag_trend, zigzag_turn_up, zigzag_turn_down, trendline, trendline_slope = _series_vars(closes, vols, highs, lows, settings.params)
        prev_ctx = {}
        if idx > 0:
            prev_ctx = engine._ctx_at(
                idx - 1, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
                zigzag_trend, zigzag_turn_up, zigzag_turn_down, {},
                trendline, trendline_slope
            )
        cur_ctx = engine._ctx_at(
            idx, closes, ma, obv, obv_sig, macd, supertrend, supertrend_trend, jma, vwma,
            zigzag_trend, zigzag_turn_up, zigzag_turn_down, prev_ctx,
            trendline, trendline_slope
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
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def build_simulation_html():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()

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

                elif path.startswith("/static/"):
                    rel = path[len("/static/"):]
                    full = os.path.normpath(os.path.join(STATIC_DIR, rel))
                    if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
                        self._send("404 Not Found", "text/plain", 404)
                    else:
                        ext = os.path.splitext(full)[1].lower()
                        ctype = {
                            ".js": "application/javascript; charset=utf-8",
                            ".css": "text/css; charset=utf-8",
                            ".html": "text/html; charset=utf-8",
                        }.get(ext, "application/octet-stream")
                        with open(full, "rb") as f:
                            self._send(f.read(), ctype)

                elif path == "/api/bootstrap":
                    self._send(jdump({
                        "default_params": DEFAULT_PARAMS,
                        "condition_row_sample": CONDITION_ROW_SAMPLE,
                        "timeframes": TIMEFRAMES,
                    }))


                
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
