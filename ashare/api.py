# -*- coding: utf-8 -*-
"""API 聚合层。"""
import datetime as dt

from . import backtest, config, datasource, limits, picks, screener, store, updater


def _r(v, n=2):
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def bootstrap():
    st = store.stats()
    return {
        "stats": st,
        "sync": updater.status(),
        "presets": screener.presets(),
        "fields": screener.FIELDS,
        "ops": screener.OPS,
        "boards": config.BOARDS,
        "indexes": [{"code": c, "name": n} for c, n in config.INDEXES],
    }


def _index_cards():
    out = []
    for sym, name in config.INDEXES:
        bars = store.get_index_bars(sym)
        if not bars:
            out.append({"secid": sym, "name": name, "price": None, "pct": None, "spark": []})
            continue
        last = bars[-1]
        spark = [b["close"] for b in bars[-40:]]
        out.append({
            "secid": sym,
            "name": name,
            "price": _r(last["close"], 2),
            "pct": _r(last["pct"], 2),
            "date": last["date"],
            "spark": spark,
        })
    return out


def market_overview():
    spot, ts = store.load_spot()
    stocks = store.load_stocks()
    rows = list(spot.values())
    if not rows:
        return {"ok": False, "error": "尚无行情数据，请先在「数据管理」初始化", "indices": _index_cards()}

    up = down = flat = 0
    limit_up = limit_down = 0
    total_amount = 0.0
    pct_sum = 0.0
    dist = {"涨停": 0, "涨超5%": 0, "涨0~5%": 0, "平盘": 0, "跌0~5%": 0, "跌超5%": 0, "跌停": 0}
    industries = {}
    for r in rows:
        pct = r.get("pct")
        amount = r.get("amount") or 0
        total_amount += amount
        if pct is None:
            flat += 1
            continue
        pct_sum += pct
        lim = limits.limit_pct(r.get("board"), r.get("is_st"))
        if pct >= lim - 0.3:
            limit_up += 1
            dist["涨停"] += 1
        elif pct <= -lim + 0.3:
            limit_down += 1
            dist["跌停"] += 1
        elif pct > 5:
            dist["涨超5%"] += 1
        elif pct > 0:
            dist["涨0~5%"] += 1
        elif pct == 0:
            dist["平盘"] += 1
        elif pct > -5:
            dist["跌0~5%"] += 1
        else:
            dist["跌超5%"] += 1
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1

        ind = r.get("industry") or (stocks.get(r["code"]) or {}).get("industry") or "其他"
        it = industries.setdefault(ind, {"name": ind, "sum": 0.0, "n": 0, "up": 0, "amount": 0.0})
        it["sum"] += pct
        it["n"] += 1
        it["amount"] += amount
        if pct > 0:
            it["up"] += 1

    ind_list = []
    for it in industries.values():
        if it["n"] < 2:
            continue
        ind_list.append({
            "name": it["name"],
            "pct": round(it["sum"] / it["n"], 2),
            "count": it["n"],
            "up": it["up"],
            "amount": round(it["amount"] / 1e8, 1),
        })
    ind_list.sort(key=lambda x: x["pct"], reverse=True)

    def brief(r):
        return {
            "code": r["code"], "name": r["name"], "price": _r(r.get("price"), 2),
            "pct": _r(r.get("pct"), 2), "amount": _r((r.get("amount") or 0) / 1e8, 1),
            "turnover": _r(r.get("turnover"), 2), "board": r.get("board"),
            "mktcap": _r((r.get("mktcap") or 0) / 1e8, 1),
        }

    valid = [r for r in rows if r.get("price") and r.get("pct") is not None]
    gainers = sorted(valid, key=lambda x: x["pct"], reverse=True)[:10]
    losers = sorted(valid, key=lambda x: x["pct"])[:10]
    actives = sorted(valid, key=lambda x: x.get("amount") or 0, reverse=True)[:10]

    return {
        "ok": True,
        "indices": _index_cards(),
        "breadth": {
            "up": up, "down": down, "flat": flat,
            "limit_up": limit_up, "limit_down": limit_down,
            "total": len(rows),
            "amount": round(total_amount / 1e8, 1),
            "avg_pct": round(pct_sum / max(1, up + down + flat), 2),
        },
        "dist": [{"name": k, "value": v} for k, v in dist.items()],
        "industries": ind_list[:10],
        "industries_bottom": ind_list[-5:][::-1],
        "gainers": [brief(r) for r in gainers],
        "losers": [brief(r) for r in losers],
        "actives": [brief(r) for r in actives],
        "spot_ts": ts or "",
        "trade_date": store.meta_get("bar_max_date") or "",
    }


def _row_from_spot(r):
    return {
        "code": r["code"], "name": r.get("name", ""), "board": r.get("board", ""),
        "industry": r.get("industry", ""), "is_st": bool(r.get("is_st")),
        "price": _r(r.get("price"), 2), "pct": _r(r.get("pct"), 2),
        "turnover": _r(r.get("turnover"), 2), "volume_ratio": _r(r.get("volume_ratio"), 2),
        "amount": _r((r.get("amount") or 0) / 1e8, 2),
        "mktcap": _r((r.get("mktcap") or 0) / 1e8, 2),
        "float_mktcap": _r((r.get("float_mktcap") or 0) / 1e8, 2),
        "pe": _r(r.get("pe"), 2), "pb": _r(r.get("pb"), 2),
        "pct_60d": _r(r.get("pct_60d"), 2), "pct_ytd": _r(r.get("pct_ytd"), 2),
        "amplitude": _r(r.get("amplitude"), 2), "high": _r(r.get("high"), 2),
        "low": _r(r.get("low"), 2), "open": _r(r.get("open"), 2),
        "prev_close": _r(r.get("prev_close"), 2), "volume": r.get("volume"),
        "list_date": r.get("list_date", ""),
    }


def stock_list(q="", board="", page=1, size=20, sort_by="pct", order="desc", exclude_st=False):
    spot, ts = store.load_spot()
    tech = store.load_tech()
    stocks = store.load_stocks()
    rows = []
    for sp in spot.values():
        r = _row_from_spot(sp)
        t = tech.get(sp["code"]) or {}
        meta = stocks.get(sp["code"]) or {}
        r["pct_60d"] = _r(t.get("pct_60d"), 2)
        r["pct_ytd"] = _r(t.get("pct_ytd"), 2)
        r["volume_ratio"] = _r(t.get("vol_ratio5"), 2)
        r["ma_bull"] = t.get("ma_bull")
        r["rsi6"] = _r(t.get("rsi6"), 1)
        r["industry"] = r.get("industry") or meta.get("industry") or t.get("industry") or ""
        rows.append(r)
    if q:
        q = q.strip().lower()
        rows = [r for r in rows if q in r["code"].lower() or q in r["name"].lower()]
    if board:
        boards = [b for b in board.split(",") if b]
        rows = [r for r in rows if r["board"] in boards]
    if exclude_st:
        rows = [r for r in rows if not r["is_st"]]
    rows = [r for r in rows if r["price"]]
    key = sort_by if sort_by in ("price", "pct", "turnover", "amount", "mktcap", "pe", "pb", "pct_60d", "pct_ytd") else "pct"
    reverse = order != "asc"
    rows.sort(key=lambda x: ((x.get(key) is None), x.get(key)), reverse=reverse)
    total = len(rows)
    page = max(1, int(page))
    size = max(1, min(200, int(size)))
    start = (page - 1) * size
    return {"rows": rows[start:start + size], "total": total, "page": page, "size": size, "spot_ts": ts or ""}


def search(q, limit=10):
    q = (q or "").strip().lower()
    if not q:
        return []
    spot, _ = store.load_spot()
    rows = []
    for r in spot.values():
        name = (r.get("name") or "")
        code = r.get("code") or ""
        if q in code.lower() or q in name.lower():
            rows.append({"code": code, "name": name, "price": _r(r.get("price"), 2),
                         "pct": _r(r.get("pct"), 2), "board": r.get("board")})
    rows.sort(key=lambda x: (not x["code"].lower().startswith(q), x["code"]))
    return rows[:limit]


def stock_detail(code):
    stocks = store.load_stocks()
    meta = stocks.get(code) or {}
    spot, ts = store.load_spot()
    sp = spot.get(code) or {}
    bars = store.get_bars(code, limit=320)
    tech = None
    series = None
    if bars:
        from . import indicators
        s = indicators.compute(bars)
        i = len(bars) - 1
        tech = indicators.snapshot_at(s, i)
        c = s["close"][i]
        tech["breakout60"] = bool(tech.get("high60") and c >= tech["high60"] * 0.999)
        span = min(250, len(bars))
        off = len(bars) - span
        series = {
            "dates": [b["date"] for b in bars[off:]],
            "kline": [[b["open"], b["close"], b["low"], b["high"]] for b in bars[off:]],
            "volume": [b["volume"] for b in bars[off:]],
            "pct": [b["pct"] for b in bars[off:]],
            "ma5": [_r(v) for v in s["ma5"][off:]],
            "ma10": [_r(v) for v in s["ma10"][off:]],
            "ma20": [_r(v) for v in s["ma20"][off:]],
            "ma60": [_r(v) for v in s["ma60"][off:]],
            "dif": [_r(v, 3) for v in s["dif"][off:]],
            "dea": [_r(v, 3) for v in s["dea"][off:]],
            "hist": [_r(v, 3) for v in s["hist"][off:]],
            "k": [_r(v, 2) for v in s["k"][off:]],
            "d": [_r(v, 2) for v in s["d"][off:]],
            "j": [_r(v, 2) for v in s["j"][off:]],
            "rsi6": [_r(v, 2) for v in s["rsi6"][off:]],
        }
    row = _row_from_spot(sp) if sp else {
        "code": code, "name": meta.get("name", ""), "board": meta.get("board", ""),
        "industry": meta.get("industry", ""), "is_st": bool(meta.get("is_st")),
    }
    if tech:
        row.update({k: _r(v, 3) for k, v in tech.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        row["ma_bull"] = tech.get("ma_bull")
        if row.get("volume_ratio") is None:
            row["volume_ratio"] = _r(tech.get("vol_ratio5"), 2)
        if len(bars) > 60 and bars[-61]["close"]:
            row["pct_60d"] = _r((bars[-1]["close"] / bars[-61]["close"] - 1) * 100, 2)
    row["concepts"] = (sp.get("concepts") or "")
    row["watch"] = code in store.watchlist()
    return {"stock": row, "series": series, "tech": tech, "spot_ts": ts or ""}


def do_screen(payload):
    payload = payload or {}
    conds = payload.get("conditions") or []
    universe = payload.get("universe") or {}
    limit = int(payload.get("limit") or 100)
    sort_by = payload.get("sort_by") or "score"
    order = payload.get("order") or "desc"
    res = screener.screen(conds, universe, limit=limit, sort_by=sort_by, order=order)
    out_rows = []
    watch = set(store.watchlist())
    for r in res["rows"]:
        item = {k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()}
        item["watch"] = r["code"] in watch
        out_rows.append(item)
    res["rows"] = out_rows
    return res


def do_backtest(payload):
    payload = payload or {}
    preset_id = payload.get("preset_id")
    if preset_id and preset_id in screener.PRESET_MAP:
        p = screener.PRESET_MAP[preset_id]
        conds = p["conds"]
        name = p["name"]
        universe = payload.get("universe") or {"exclude_st": True, "exclude_new": True}
    else:
        conds = payload.get("conditions") or []
        name = "自定义策略"
        universe = payload.get("universe") or {"exclude_st": True, "exclude_new": True}
    return backtest.run(
        conditions=conds,
        universe=universe,
        start=payload.get("start"),
        end=payload.get("end"),
        hold_days=int(payload.get("hold_days") or 5),
        max_positions=int(payload.get("max_positions") or 10),
        capital=float(payload.get("capital") or 1000000),
        preset_name=name,
    )


def _quote_stamp(quotes):
    """取最新报价时间；盘后异常时间（>15:30）按 15:00 收盘计。"""
    qtime = ""
    qdate = ""
    for q in quotes.values():
        t = q.get("time") or ""
        if t:
            if t > "15:30:00":
                t = "15:00:00"
            if t > qtime:
                qtime = t
        d = q.get("date") or ""
        if d > qdate:
            qdate = d
    return qtime, qdate


def watchlist_payload():
    codes = store.watchlist()
    spot, ts = store.load_spot()
    tech = store.load_tech_codes(codes)
    stocks = store.load_stocks()
    live = {}
    try:
        live = datasource.fetch_quotes(codes) if codes else {}
    except Exception:
        live = {}
    rows = []
    for c in codes:
        sp = spot.get(c)
        q = live.get(c) or {}
        meta = stocks.get(c) or {}
        if sp:
            r = _row_from_spot(sp)
        else:
            if not meta and not q:
                continue
            r = {
                "code": c,
                "name": meta.get("name") or q.get("name") or c,
                "board": meta.get("board", "") or "",
                "industry": meta.get("industry", "") or "",
                "is_st": bool(meta.get("is_st")),
                "price": None, "pct": None, "turnover": None,
                "volume_ratio": None, "amount": None, "mktcap": None,
                "float_mktcap": None, "pe": None, "pb": None,
                "pct_60d": None, "pct_ytd": None,
            }
        t = tech.get(c) or {}
        r["pct_60d"] = _r(t.get("pct_60d"), 2)
        r["volume_ratio"] = _r(t.get("vol_ratio5"), 2)
        if q.get("price"):
            r["price"] = _r(q["price"], 2)
        if q.get("pct") is not None:
            r["pct"] = _r(q["pct"], 2)
        if q.get("amount"):
            r["amount"] = _r(q["amount"] / 1e8, 2)
        rows.append(r)
    quote_time, quote_date = _quote_stamp(live)
    return {
        "rows": rows,
        "spot_ts": ts or "",
        "live": bool(live),
        "quote_time": quote_time,
        "quote_date": quote_date,
    }


def live_quotes(codes=None):
    codes = [str(c) for c in (codes or []) if str(c).isdigit() and len(str(c)) == 6]
    if not codes:
        codes = store.watchlist()
    quotes = datasource.fetch_quotes(codes[:80])
    quote_time, quote_date = _quote_stamp(quotes)
    return {"ok": True, "quotes": quotes, "quote_time": quote_time,
            "quote_date": quote_date, "server_time": dt.datetime.now().strftime("%H:%M:%S")}


def limitup_payload(kind="up"):
    return picks.limit_board(kind)


def recommend_payload(limit=12):
    return picks.recommend(limit)
