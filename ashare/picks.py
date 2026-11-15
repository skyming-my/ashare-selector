# -*- coding: utf-8 -*-
"""涨停板复盘与今日推荐。"""
import datetime as dt

from . import limits, screener, store

STRATEGY_BY_REGIME = {
    "进攻": ["relay", "breakout", "ma_bull"],
    "防守": ["relay", "value", "pullback"],
    "震荡": ["relay", "pullback", "value"],
}


# ---------------- 涨停板 ----------------

def _shape(bars, vol_ratio):
    if not bars:
        return "—"
    b = bars[-1]
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    if h and l and abs(h - l) / max(h, 1e-9) < 0.001:
        return "一字板"
    if h and c and o and abs(h - c) / max(h, 1e-9) < 0.001 and abs(o - c) / max(c, 1e-9) < 0.001 and l < c:
        return "T字板"
    if vol_ratio is not None and vol_ratio >= 2:
        return "放量板"
    if vol_ratio is not None and vol_ratio <= 0.8:
        return "缩量板"
    return "普通板"


def limit_board(kind="up"):
    spot, ts = store.load_spot()
    tech = store.load_tech()
    stocks = store.load_stocks()
    date = store.meta_get("bar_max_date") or ""
    watch = set(store.watchlist())
    if not spot:
        return {"ok": False, "error": "尚未同步行情数据"}

    up_total = down_total = 0
    for sp in spot.values():
        if limits.is_limit_up(sp.get("pct"), sp.get("board"), sp.get("is_st")):
            up_total += 1
        if limits.is_limit_down(sp.get("pct"), sp.get("board"), sp.get("is_st")):
            down_total += 1

    rows = []
    dist = {}
    one_word = 0
    amount_sum = 0.0
    max_streak = 0

    for code, sp in spot.items():
        t = tech.get(code)
        if not t or (date and t.get("tech_date") != date):
            continue
        flag = t.get("limit_up") if kind == "up" else t.get("limit_down")
        if not flag:
            continue
        meta = stocks.get(code) or {}
        m = screener.base_metrics(meta, sp)
        bars = store.get_bars(code, limit=2)
        vr = t.get("vol_ratio5")
        shape = _shape(bars, vr)
        st = t.get("limit_streak") or 0
        is_one = shape == "一字板"
        if is_one:
            one_word += 1
        amount_sum += m.get("amount") or 0
        if kind == "up":
            max_streak = max(max_streak, st)
            tier = 4 if st >= 4 else st
            dist[tier] = dist.get(tier, 0) + 1
        rows.append({
            "code": code,
            "name": m["name"],
            "board": m["board"],
            "industry": m["industry"],
            "is_st": m["is_st"],
            "price": m["price"],
            "pct": m["pct"],
            "turnover": m["turnover"],
            "amount": m["amount"],
            "float_mktcap": m["float_mktcap"],
            "streak": st if kind == "up" else 0,
            "count20": t.get("limit_count20") or 0,
            "shape": shape,
            "one_word": is_one,
            "vol_ratio5": vr,
            "limit_label": limits.label(m["board"], m["is_st"]),
            "watch": code in watch,
        })

    if kind == "up":
        rows.sort(key=lambda x: (-x["streak"], 0 if x["one_word"] else 1, -(x["amount"] or 0)))
    else:
        rows.sort(key=lambda x: -(x["amount"] or 0))

    return {
        "ok": True,
        "kind": kind,
        "trade_date": date,
        "spot_ts": ts or "",
        "stats": {
            "count": len(rows),
            "max_streak": max_streak,
            "one_word": one_word,
            "amount": round(amount_sum, 1),
            "dist": dist,
            "up_total": up_total,
            "down_total": down_total,
        },
        "rows": rows,
    }


# ---------------- 今日推荐 ----------------

def _regime(up, down, flat, lu, ld):
    total = max(1, up + down + flat)
    up_ratio = round(up / total * 100, 1)
    if up_ratio >= 55 and lu >= 30 and lu >= ld:
        label = "进攻"
        advice = "普涨格局，可跟随强势主线，优先均线多头与放量突破，控制单票仓位。"
    elif up_ratio <= 40 or ld > lu:
        label = "防守"
        advice = "赚钱效应偏弱，建议控制仓位；涨停接力只看低位首板与 2 连板，回避高位连板。"
    else:
        label = "震荡"
        advice = "结构性行情，轻指数重个股，优先回踩均线与低估值品种，快进快出。"
    return label, advice, up_ratio


def _risks(m):
    out = []
    if (m.get("turnover") or 0) > 15:
        out.append("换手偏高")
    if m.get("bias20") is not None and m["bias20"] > 8:
        out.append("短期乖离大")
    pe = m.get("pe")
    if pe is None or pe <= 0:
        out.append("亏损")
    elif pe > 60:
        out.append("估值偏高")
    if m.get("rsi6") is not None and m["rsi6"] > 75:
        out.append("超买")
    if m.get("dist_high60") is not None and m["dist_high60"] > -1:
        out.append("接近前高")
    if m.get("pct_60d") is not None and m["pct_60d"] < -10:
        out.append("中期弱势")
    return out[:3]


def _score(m, ind_pct, heat=0):
    base = screener.compute_score(m)
    bonus = 0.0
    reasons = []
    if m.get("ma_bull"):
        bonus += 5
        reasons.append("均线多头")
    if m.get("above_ma20"):
        bonus += 2
    bias = m.get("bias20")
    if bias is not None and -5 <= bias <= 2:
        bonus += 4
        reasons.append("回踩20日线")
    vr = m.get("vol_ratio5")
    if vr is not None and 1.1 <= vr <= 2.8:
        bonus += 4
        reasons.append("温和放量")
    if m.get("breakout60"):
        bonus += 4
        reasons.append("创60日新高")
    p60 = m.get("pct_60d")
    if p60 is not None and 8 <= p60 <= 45:
        bonus += 3
        reasons.append("中期强势")
    pe = m.get("pe")
    if pe is not None and 0 < pe <= 22:
        bonus += 3
        reasons.append("低估值")
    r6 = m.get("rsi6")
    if r6 is not None and 45 <= r6 <= 72:
        bonus += 2
        reasons.append("动能健康")
    if ind_pct is not None and ind_pct > 0.3:
        bonus += 3
        reasons.append("行业强势")
    if heat >= 3:
        bonus += 5
        reasons.append("涨停板块")
    if bias is not None and bias > 8:
        bonus -= 6
    if (m.get("turnover") or 0) > 15:
        bonus -= 3
    total = max(0.0, min(100.0, base * 0.72 + bonus * 1.6))
    return round(total, 1), reasons[:4]


STREAK_SCORE = {
    "进攻": {1: 10, 2: 20, 3: 30, 4: 40, 5: 50},
    "震荡": {1: 8, 2: 14, 3: 20, 4: 22, 5: 22},
    "防守": {1: 20, 2: 16, 3: 8, 4: 2, 5: 0},
}


def _pool_score(p, regime="震荡"):
    table = STREAK_SCORE.get(regime, STREAK_SCORE["震荡"])
    s = float(table.get(min(p["streak"], 5), 0))
    shape = p["shape"]
    if shape == "一字板":
        s += 8
    elif shape == "T字板":
        s += 6
    elif shape == "放量板":
        s += 4
    amt = p["amount"] or 0
    if 1 <= amt <= 25:
        s += 8
    elif amt < 0.5:
        s -= 6
    elif amt > 40:
        s -= 4
    turn = p["turnover"] or 0
    if 3 <= turn <= 15:
        s += 5
    elif turn > 25:
        s -= 8
    fmc = p["float_mktcap"] or 0
    if 15 <= fmc <= 150:
        s += 6
    elif fmc > 400:
        s -= 3
    s += min(p["count20"], 5) * 2
    ic = p["industry_count"]
    if ic >= 3:
        s += 8
    elif ic == 2:
        s += 4
    if p["streak"] >= 4:
        s -= 6
    if shape == "缩量板":
        s -= 3
    return round(max(10.0, min(99.0, 45 + s * 0.7)), 1)


def _pool_reasons(p):
    r = ["%d连板" % p["streak"] if p["streak"] >= 2 else "首板"]
    if p["shape"] in ("一字板", "T字板", "放量板"):
        r.append(p["shape"])
    if p["industry_count"] >= 3:
        r.append("板块共振")
    return r[:4]


def _pool_risks(p):
    r = []
    if p["streak"] >= 4:
        r.append("高位连板")
    if p["shape"] == "一字板":
        r.append("一字难买")
    if p["shape"] == "缩量板":
        r.append("缩量板")
    if (p["turnover"] or 0) > 25:
        r.append("换手过高")
    if (p["amount"] or 0) < 0.5:
        r.append("成交额偏小")
    if (p["float_mktcap"] or 0) > 400:
        r.append("盘子偏大")
    return r[:3]


def _pool_candidates(spot, tech, stocks, date, watch, regime="震荡"):
    pool = []
    prev_total = 0
    for code, sp in spot.items():
        t = tech.get(code)
        if not t or (date and t.get("tech_date") != date):
            continue
        if t.get("prev_limit_up"):
            prev_total += 1
        if not t.get("limit_up"):
            continue
        meta = stocks.get(code) or {}
        m = screener.base_metrics(meta, sp)
        shape = _shape(store.get_bars(code, limit=1), t.get("vol_ratio5"))
        ind = m.get("industry") or "其他"
        eligible = (not m["is_st"]) and bool(m["price"]) and m["price"] >= 2
        pool.append({
            "code": code,
            "name": m["name"],
            "board": m["board"],
            "industry": "" if ind == "其他" else ind,
            "price": m["price"],
            "pct": m["pct"],
            "turnover": m["turnover"],
            "amount": m["amount"],
            "float_mktcap": m["float_mktcap"],
            "streak": t.get("limit_streak") or 0,
            "count20": t.get("limit_count20") or 0,
            "shape": shape,
            "one_word": shape == "一字板",
            "vol_ratio5": t.get("vol_ratio5"),
            "is_st": m["is_st"],
            "eligible": eligible,
            "pool": "limit",
            "_ind": ind,
        })
    cnt = {}
    for p in pool:
        cnt[p["_ind"]] = cnt.get(p["_ind"], 0) + 1
    for p in pool:
        p["industry_count"] = cnt.get(p["_ind"], 0)
        p["score"] = _pool_score(p, regime)
        p["heat_score"] = _pool_score(p, "进攻")
        p["display_score"] = p["heat_score"]
        p["reasons"] = _pool_reasons(p)
        p["risks"] = _pool_risks(p)
        p["watch"] = p["code"] in watch
        del p["_ind"]
    return pool, prev_total


def recommend(limit=12):
    limit = max(1, min(20, int(limit or 12)))
    spot, ts = store.load_spot()
    tech = store.load_tech()
    stocks = store.load_stocks()
    date = store.meta_get("bar_max_date") or ""
    watch = set(store.watchlist())
    if not spot:
        return {"ok": False, "error": "尚未同步行情数据"}

    up = down = flat = lu = ld = 0
    pct_sum = 0.0
    ind_sum = {}
    for code, sp in spot.items():
        p = sp.get("pct")
        ind = sp.get("industry") or (stocks.get(code) or {}).get("industry") or "其他"
        if p is None:
            flat += 1
            continue
        pct_sum += p
        if p > 0:
            up += 1
        elif p < 0:
            down += 1
        else:
            flat += 1
        if limits.is_limit_up(p, sp.get("board"), sp.get("is_st")):
            lu += 1
        if limits.is_limit_down(p, sp.get("board"), sp.get("is_st")):
            ld += 1
        it = ind_sum.setdefault(ind, [0.0, 0])
        it[0] += p
        it[1] += 1
    avg_pct = pct_sum / max(1, up + down + flat)
    label, advice, up_ratio = _regime(up, down, flat, lu, ld)
    ind_avg = {k: v[0] / v[1] for k, v in ind_sum.items() if v[1] >= 3}
    industries = sorted(
        [{"name": k, "pct": round(v[0] / v[1], 2), "count": v[1]} for k, v in ind_sum.items() if v[1] >= 3],
        key=lambda x: -x["pct"])[:6]

    ids = STRATEGY_BY_REGIME.get(label, ["pullback", "macd_gold", "value"])
    strategies = []
    for sid in ids:
        p = screener.PRESET_MAP.get(sid)
        if p:
            strategies.append({"id": p["id"], "name": p["name"], "desc": p["desc"]})

    pool, prev_total = _pool_candidates(spot, tech, stocks, date, watch, label)
    strength = sorted([p for p in pool if p.get("eligible")],
                      key=lambda p: (-p["streak"], 0 if p["one_word"] else 1, -(p["amount"] or 0)))
    pool.sort(key=lambda x: -x["score"])
    heat = {}
    for p in pool:
        key = p["industry"] or "其他"
        heat[key] = heat.get(key, 0) + 1

    candidates = []
    for code, sp in spot.items():
        t = tech.get(code)
        if not t or (date and t.get("tech_date") != date):
            continue
        meta = stocks.get(code) or {}
        m = screener.base_metrics(meta, sp)
        m.update({k: v for k, v in t.items() if k not in ("bars", "tech_date")})
        if m["is_st"] or t.get("limit_up") or t.get("limit_down"):
            continue
        if not m.get("price") or m["price"] < 2:
            continue
        if (m.get("amount") or 0) < 0.8:
            continue
        if (t.get("bars") or 0) < 120:
            continue
        pct = m.get("pct")
        if pct is None or pct < -4 or pct > 7:
            continue
        if (m.get("turnover") or 0) > 18:
            continue
        if m.get("dist_high60") is not None and m["dist_high60"] < -18:
            continue
        if (m.get("mktcap") or 0) < 20:
            continue
        ind = m.get("industry") or "其他"
        total, reasons = _score(m, ind_avg.get(ind), heat.get(ind, 0))
        candidates.append({
            "code": code,
            "name": m["name"],
            "board": m["board"],
            "industry": "" if ind == "其他" else ind,
            "price": m["price"],
            "pct": m["pct"],
            "turnover": m["turnover"],
            "amount": m["amount"],
            "mktcap": m["mktcap"],
            "pe": m.get("pe"),
            "pb": m.get("pb"),
            "pct_60d": m.get("pct_60d"),
            "vol_ratio5": m.get("vol_ratio5"),
            "rsi6": m.get("rsi6"),
            "bias20": m.get("bias20"),
            "dist_high60": m.get("dist_high60"),
            "score": total,
            "reasons": reasons,
            "risks": _risks(m),
            "watch": code in watch,
            "pool": "general",
        })

    candidates.sort(key=lambda x: -x["score"])
    picked = []
    used = set()
    ind_used = {}
    n_limit = min(6, len(pool), max(2, int(round(limit * 0.5))))
    pool_by_strength = sorted(
        [p for p in pool if p.get("eligible")],
        key=lambda p: (-p["streak"], -p.get("heat_score", 0)))
    for p in pool_by_strength:
        if len(picked) >= n_limit:
            break
        if ind_used.get(p["industry"], 0) >= 2:
            continue
        ind_used[p["industry"]] = ind_used.get(p["industry"], 0) + 1
        picked.append(p)
        used.add(p["code"])
    for c in candidates:
        if len(picked) >= limit:
            break
        if c["code"] in used:
            continue
        n = ind_used.get(c["industry"], 0)
        if n >= 3:
            continue
        ind_used[c["industry"]] = n + 1
        picked.append(c)

    streak_count = sum(1 for p in pool if p["streak"] >= 2)
    top_ind = sorted(
        [{"name": k, "count": v} for k, v in heat.items() if k != "其他" and v >= 2],
        key=lambda x: -x["count"])[:4]
    leaders = [{
        "code": p["code"], "name": p["name"], "streak": p["streak"], "shape": p["shape"],
        "score": p.get("heat_score", p["score"]), "industry": p.get("industry", ""), "pct": p["pct"],
    } for p in strength[:5]]

    return {
        "ok": True,
        "trade_date": date,
        "spot_ts": ts or "",
        "scanned": len(spot),
        "rows": picked,
        "limit_pool": {
            "date": date,
            "count": len(pool),
            "streak_count": streak_count,
            "one_word": sum(1 for p in pool if p["one_word"]),
            "prev_count": prev_total,
            "promotion_rate": round(streak_count / prev_total * 100, 1) if prev_total else None,
            "top_industries": top_ind,
            "leaders": leaders,
        },
        "regime": {
            "label": label,
            "advice": advice,
            "breadth": {
                "up": up, "down": down, "flat": flat,
                "limit_up": lu, "limit_down": ld,
                "up_ratio": up_ratio,
                "avg_pct": round(avg_pct, 2),
            },
            "industries": industries,
        },
        "strategies": strategies,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
