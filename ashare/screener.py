# -*- coding: utf-8 -*-
"""选股引擎：条件定义、预设策略、打分与筛选。"""
import datetime as dt
import time

from . import store

# 字段元数据：前端据此生成条件行
FIELDS = [
    # 基础
    {"key": "price", "label": "最新价", "unit": "元", "group": "行情", "type": "number", "step": 0.1},
    {"key": "pct", "label": "涨跌幅", "unit": "%", "group": "行情", "type": "number", "step": 0.1},
    {"key": "turnover", "label": "换手率", "unit": "%", "group": "行情", "type": "number", "step": 0.1},
    {"key": "volume_ratio", "label": "量比", "unit": "", "group": "行情", "type": "number", "step": 0.1},
    {"key": "amount", "label": "成交额", "unit": "亿元", "group": "行情", "type": "number", "step": 0.5},
    {"key": "amplitude", "label": "振幅", "unit": "%", "group": "行情", "type": "number", "step": 0.1},
    {"key": "pct_60d", "label": "60日涨跌幅", "unit": "%", "group": "行情", "type": "number", "step": 1},
    {"key": "pct_ytd", "label": "年初至今", "unit": "%", "group": "行情", "type": "number", "step": 1},
    {"key": "mktcap", "label": "总市值", "unit": "亿元", "group": "估值", "type": "number", "step": 10},
    {"key": "float_mktcap", "label": "流通市值", "unit": "亿元", "group": "估值", "type": "number", "step": 10},
    {"key": "pe", "label": "市盈率(动)", "unit": "", "group": "估值", "type": "number", "step": 1},
    {"key": "pb", "label": "市净率", "unit": "", "group": "估值", "type": "number", "step": 0.1},
    {"key": "roe", "label": "ROE", "unit": "%", "group": "估值", "type": "number", "step": 0.5},
    # 技术
    {"key": "ma_bull", "label": "均线多头排列", "unit": "", "group": "技术", "type": "bool"},
    {"key": "above_ma5", "label": "站上5日线", "unit": "", "group": "技术", "type": "bool"},
    {"key": "above_ma20", "label": "站上20日线", "unit": "", "group": "技术", "type": "bool"},
    {"key": "above_ma60", "label": "站上60日线", "unit": "", "group": "技术", "type": "bool"},
    {"key": "breakout60", "label": "创60日新高", "unit": "", "group": "技术", "type": "bool"},
    {"key": "breakout20", "label": "创20日新高", "unit": "", "group": "技术", "type": "bool"},
    {"key": "limit_up", "label": "涨停封板", "unit": "", "group": "技术", "type": "bool"},
    {"key": "limit_streak", "label": "连板数", "unit": "板", "group": "技术", "type": "number", "step": 1},
    {"key": "limit_count20", "label": "20日涨停次数", "unit": "次", "group": "技术", "type": "number", "step": 1},
    {"key": "macd_cross", "label": "MACD金叉(距今天数)", "unit": "天", "group": "技术", "type": "number", "step": 1},
    {"key": "kdj_cross", "label": "KDJ金叉(距今天数)", "unit": "天", "group": "技术", "type": "number", "step": 1},
    {"key": "rsi6", "label": "RSI(6)", "unit": "", "group": "技术", "type": "number", "step": 1},
    {"key": "rsi12", "label": "RSI(12)", "unit": "", "group": "技术", "type": "number", "step": 1},
    {"key": "vol_ratio5", "label": "量能比(5日)", "unit": "倍", "group": "技术", "type": "number", "step": 0.1},
    {"key": "up_days", "label": "连涨天数", "unit": "天", "group": "技术", "type": "number", "step": 1},
    {"key": "dist_high60", "label": "距60日高点", "unit": "%", "group": "技术", "type": "number", "step": 1},
    {"key": "bias20", "label": "20日乖离率", "unit": "%", "group": "技术", "type": "number", "step": 1},
    {"key": "boll_pos", "label": "布林带位置", "unit": "", "group": "技术", "type": "number", "step": 0.05},
    {"key": "kdj_k", "label": "KDJ-K值", "unit": "", "group": "技术", "type": "number", "step": 1},
]

FIELD_MAP = {f["key"]: f for f in FIELDS}
TECH_KEYS = {f["key"] for f in FIELDS if f["group"] == "技术"}

OPS = [
    {"key": "gte", "label": "≥"},
    {"key": "lte", "label": "≤"},
    {"key": "between", "label": "区间"},
    {"key": "eq", "label": "="},
]

PRESETS = [
    {
        "id": "ma_bull", "name": "均线多头", "icon": "📈",
        "desc": "MA5>MA10>MA20>MA60，收盘站上20日线，趋势向上且不过热",
        "conds": [
            {"field": "ma_bull", "op": "eq", "value": True},
            {"field": "above_ma20", "op": "eq", "value": True},
            {"field": "pct", "op": "between", "value": -2, "value2": 7},
            {"field": "turnover", "op": "between", "value": 1, "value2": 15},
            {"field": "mktcap", "op": "gte", "value": 30},
        ],
    },
    {
        "id": "macd_gold", "name": "MACD金叉放量", "icon": "⚡",
        "desc": "近3日MACD金叉，5日量能放大1.3倍以上，今日红盘",
        "conds": [
            {"field": "macd_cross", "op": "lte", "value": 3},
            {"field": "vol_ratio5", "op": "gte", "value": 1.3},
            {"field": "pct", "op": "between", "value": 0, "value2": 8},
            {"field": "turnover", "op": "gte", "value": 2},
        ],
    },
    {
        "id": "breakout", "name": "放量突破", "icon": "🚀",
        "desc": "创60日新高，涨幅1%~19.5%，量能放大1.2倍以上，换手不失控",
        "conds": [
            {"field": "breakout60", "op": "eq", "value": True},
            {"field": "pct", "op": "between", "value": 1, "value2": 19.5},
            {"field": "volume_ratio", "op": "gte", "value": 1.2},
            {"field": "turnover", "op": "lte", "value": 25},
            {"field": "mktcap", "op": "gte", "value": 20},
        ],
    },
    {
        "id": "relay", "name": "涨停接力", "icon": "🔥",
        "desc": "最新交易日涨停，1~2 连板，成交额 1 亿以上、换手 2%~20%，适合低位接力",
        "conds": [
            {"field": "limit_up", "op": "eq", "value": True},
            {"field": "limit_streak", "op": "lte", "value": 2},
            {"field": "amount", "op": "gte", "value": 1},
            {"field": "turnover", "op": "between", "value": 2, "value2": 20},
            {"field": "mktcap", "op": "gte", "value": 20},
        ],
    },
    {
        "id": "oversold", "name": "超跌反弹", "icon": "🔄",
        "desc": "距60日高点回撤20%以上，RSI6低于40，今日红盘企稳",
        "conds": [
            {"field": "dist_high60", "op": "lte", "value": -20},
            {"field": "rsi6", "op": "lte", "value": 40},
            {"field": "pct", "op": "gte", "value": 0.5},
        ],
    },
    {
        "id": "value", "name": "低估值蓝筹", "icon": "🏛",
        "desc": "PE 0~15、PB 0~2、总市值200亿以上，换手低于8%",
        "conds": [
            {"field": "pe", "op": "between", "value": 0, "value2": 15},
            {"field": "pb", "op": "between", "value": 0, "value2": 2},
            {"field": "mktcap", "op": "gte", "value": 200},
            {"field": "pct", "op": "gte", "value": -3},
            {"field": "turnover", "op": "lte", "value": 8},
        ],
    },
    {
        "id": "pullback", "name": "强势回踩", "icon": "🎯",
        "desc": "60日涨幅超15%，回踩20日线附近（乖离-3%~2%），缩量红盘",
        "conds": [
            {"field": "pct_60d", "op": "gte", "value": 15},
            {"field": "bias20", "op": "between", "value": -3, "value2": 2},
            {"field": "pct", "op": "gte", "value": 0},
            {"field": "turnover", "op": "lte", "value": 10},
            {"field": "mktcap", "op": "gte", "value": 30},
        ],
    },
]

PRESET_MAP = {p["id"]: p for p in PRESETS}


# ---------------- 条件评估 ----------------

def _cmp(op, val, v, v2):
    if op == "between":
        if v is None and v2 is None:
            return True
        lo = v if v is not None else float("-inf")
        hi = v2 if v2 is not None else float("inf")
        return lo <= val <= hi
    if v is None:
        return True
    if op == "gte":
        return val >= v
    if op == "lte":
        return val <= v
    if op == "eq":
        return val == v
    if op == "neq":
        return val != v
    return False


def eval_cond(cond, metrics):
    key = cond.get("field")
    val = metrics.get(key)
    if val is None:
        return False
    op = cond.get("op", "gte")
    if isinstance(val, bool):
        want = bool(cond.get("value"))
        return val == want if op in ("eq", "is") else val != want
    return _cmp(op, val, cond.get("value"), cond.get("value2"))


def eval_all(conds, metrics):
    for c in conds or []:
        if c.get("field") not in FIELD_MAP:
            continue
        if not eval_cond(c, metrics):
            return False
    return True


def compute_score(m):
    def c(x, lo, hi):
        if x is None:
            return 0.0
        return max(0.0, min(1.0, (x - lo) / (hi - lo)))

    s = 0.0
    s += c(m.get("pct"), -4, 8) * 14
    s += c(m.get("pct_60d"), -25, 50) * 14
    s += c(m.get("turnover"), 0.5, 15) * 8
    s += c(m.get("volume_ratio"), 0.5, 3.5) * 8
    s += 10 if m.get("ma_bull") else 0
    s += 8 if m.get("above_ma20") else 0
    s += c(m.get("dist_high60"), -45, 0) * 12
    r6 = m.get("rsi6")
    if r6 is not None:
        s += max(0.0, 1 - abs(r6 - 62) / 38) * 8
    s += c(m.get("vol_ratio5"), 0.6, 2.0) * 6
    pe = m.get("pe")
    if pe is not None and 0 < pe <= 60:
        s += (1 - pe / 60) * 6
    pb = m.get("pb")
    if pb is not None and 0 < pb <= 8:
        s += (1 - pb / 8) * 4
    return round(min(100.0, s), 1)


# ---------------- 数据组装 ----------------

def _parse_date(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def base_metrics(meta, spot):
    """合并基础信息与快照，金额统一为亿元。"""
    price = spot.get("price")
    amount = spot.get("amount")
    mktcap = spot.get("mktcap")
    fmc = spot.get("float_mktcap")
    return {
        "code": spot["code"],
        "name": spot.get("name") or meta.get("name") or "",
        "board": spot.get("board") or meta.get("board") or "",
        "industry": spot.get("industry") or meta.get("industry") or "",
        "is_st": bool(spot.get("is_st") or meta.get("is_st")),
        "price": price,
        "pct": spot.get("pct"),
        "change": spot.get("change"),
        "turnover": spot.get("turnover"),
        "volume_ratio": spot.get("volume_ratio"),
        "amount": (amount / 1e8) if amount is not None else None,
        "amplitude": spot.get("amplitude"),
        "pct_60d": spot.get("pct_60d"),
        "pct_ytd": spot.get("pct_ytd"),
        "mktcap": (mktcap / 1e8) if mktcap is not None else None,
        "float_mktcap": (fmc / 1e8) if fmc is not None else None,
        "pe": spot.get("pe"),
        "pb": spot.get("pb"),
        "roe": spot.get("roe"),
        "volume": spot.get("volume"),
        "high": spot.get("high"),
        "low": spot.get("low"),
        "open": spot.get("open"),
        "prev_close": spot.get("prev_close"),
        "list_date": spot.get("list_date") or meta.get("list_date") or "",
        "concepts": spot.get("concepts") or "",
    }


def merge_tech(m, t):
    if not t:
        return m
    for k, v in t.items():
        if k in ("bars", "tech_date"):
            continue
        m[k] = v
    if m.get("volume_ratio") is None:
        m["volume_ratio"] = t.get("vol_ratio5")
    m["bars_count"] = t.get("bars")
    return m


def _in_universe(m, meta, universe, today):
    boards = universe.get("boards") or []
    if boards and m["board"] not in boards:
        return False
    if universe.get("exclude_st", True) and m["is_st"]:
        return False
    if universe.get("exclude_suspended", True):
        if not m.get("volume") or not m.get("price"):
            return False
    if universe.get("exclude_new", True):
        d = _parse_date(m.get("list_date"))
        if d and (today - d).days < 60:
            return False
        bars = m.get("bars_count")
        if d is None and bars is not None and bars < 60:
            return False
    return True


def screen(conditions=None, universe=None, limit=200, sort_by="score", order="desc"):
    t0 = time.time()
    conditions = [c for c in (conditions or []) if c.get("field") in FIELD_MAP]
    universe = universe or {}
    today = dt.date.today()
    spot, spot_ts = store.load_spot()
    stocks = store.load_stocks()
    tech = store.load_tech()
    warnings = []
    if not spot:
        return {"rows": [], "total": 0, "scanned": 0, "elapsed": 0,
                "spot_ts": "", "warnings": ["尚未同步行情数据，请先到「数据管理」初始化数据"]}

    candidates = []
    scanned = 0
    missing_tech = 0
    for code, sp in spot.items():
        meta = stocks.get(code) or {}
        m = base_metrics(meta, sp)
        scanned += 1
        t = tech.get(code)
        if t:
            merge_tech(m, t)
        else:
            missing_tech += 1
        if not _in_universe(m, meta, universe, today):
            continue
        if not eval_all(conditions, m):
            continue
        candidates.append(m)

    if missing_tech:
        warnings.append("%d 只股票缺少技术指标缓存，可能未参与技术面筛选" % missing_tech)

    for m in candidates:
        m.setdefault("ma_bull", None)
        m.setdefault("ma5", None)
        m["score"] = compute_score(m)

    reverse = (order != "asc")
    if sort_by == "score" or sort_by not in FIELD_MAP:
        candidates.sort(key=lambda x: x.get("score") or 0, reverse=reverse)
    else:
        candidates.sort(key=lambda x: ((x.get(sort_by) is None), x.get(sort_by)), reverse=reverse)

    total = len(candidates)
    rows = candidates[:limit] if limit and limit > 0 else candidates

    if not spot_ts:
        warnings.append("行情快照时间未知，请刷新行情")
    return {
        "rows": rows,
        "total": total,
        "scanned": scanned,
        "elapsed": round(time.time() - t0, 2),
        "spot_ts": spot_ts or "",
        "warnings": warnings,
    }


def presets():
    return PRESETS
