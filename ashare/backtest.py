# -*- coding: utf-8 -*-
"""回测引擎：按日调仓、等权持仓、次日开盘买入、固定持有期卖出。"""
import datetime as dt
import math
import time

from . import indicators, limits, screener, store

FEE_BUY = 0.0003
FEE_SELL = 0.0008

UNSUPPORTED = {"pe": "市盈率", "pb": "市净率", "roe": "ROE"}
APPROX = {"volume_ratio": "vol_ratio5"}


def _prepare_conditions(conds):
    warnings = []
    dropped = []
    mapped = []
    approximated = []
    for c in list(conds or []):
        f = c.get("field")
        if f in UNSUPPORTED:
            dropped.append(UNSUPPORTED[f])
            continue
        if f in APPROX:
            approximated.append(APPROX[f])
            c = dict(c, field=APPROX[f])
        mapped.append(c)
    if dropped:
        warnings.append("回测不含历史财务数据，已忽略条件：%s" % "、".join(sorted(set(dropped))))
    if approximated:
        warnings.append("当日量比缺少历史数据，已用5日量能比近似")
    return mapped, warnings


def _cmp(op, val, v, v2):
    if op == "between":
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


def _cross_array(fast, slow, look=20):
    n = len(fast)
    out = [None] * n
    last = None
    for i in range(1, n):
        f, s0 = fast[i], slow[i]
        fp, sp = fast[i - 1], slow[i - 1]
        if f is None or s0 is None or fp is None or sp is None:
            continue
        if f > s0 and fp <= sp:
            last = i
        if f > s0 and last is not None and i - last <= look:
            out[i] = i - last
    return out


def _build_arrays(s, bars, meta, shares, fshares, needed):
    n = len(bars)
    closes = s["close"]
    highs = s["high"]
    lows = s["low"]
    vols = s["volume"]
    a = {}
    if "price" in needed:
        a["price"] = closes
    if "pct" in needed:
        a["pct"] = [b.get("pct") for b in bars]
    if "turnover" in needed:
        a["turnover"] = [b.get("turnover") for b in bars]
    if "amount" in needed:
        a["amount"] = [((b.get("amount") or 0) / 1e8) for b in bars]
    if "volume" in needed:
        a["volume"] = vols
    for k in ("ma5", "ma10", "ma20", "ma60", "rsi6", "rsi12", "dif", "dea", "hist"):
        if k in needed:
            a[k] = s[k]
    if "kdj_k" in needed:
        a["kdj_k"] = s["k"]
    if "macd_cross" in needed:
        a["macd_cross"] = _cross_array(s["dif"], s["dea"])
    if "kdj_cross" in needed:
        a["kdj_cross"] = _cross_array(s["k"], s["d"])
    if "vol_ratio5" in needed or "volume_ratio" in needed:
        a["vol_ratio5"] = [None] * n
    if "up_days" in needed:
        a["up_days"] = [0] * n
    if "bias20" in needed:
        a["bias20"] = [None] * n
    if "dist_high60" in needed:
        a["dist_high60"] = [None] * n
    if "breakout60" in needed:
        a["breakout60"] = [False] * n
    if "breakout20" in needed:
        a["breakout20"] = [False] * n
    if "ma_bull" in needed:
        a["ma_bull"] = [None] * n
    for k in ("above_ma5", "above_ma20", "above_ma60"):
        if k in needed:
            a[k] = [None] * n
    if "boll_pos" in needed:
        a["boll_pos"] = [None] * n
    if "pct_60d" in needed:
        a["pct_60d"] = [None] * n
    if "pct_ytd" in needed:
        a["pct_ytd"] = [None] * n
    if "amplitude" in needed:
        a["amplitude"] = [None] * n
    if "mktcap" in needed and shares:
        a["mktcap"] = [(shares * c / 1e8) if c else None for c in closes]
    if "float_mktcap" in needed and fshares:
        a["float_mktcap"] = [(fshares * c / 1e8) if c else None for c in closes]

    if any(k in needed for k in ("limit_up", "limit_streak", "limit_count20")):
        lim = limits.limit_pct(meta.get("board"), meta.get("is_st"))
        flags = []
        for b in bars:
            p = b.get("pct")
            flags.append(bool(p is not None and p >= lim - 0.3))
        if "limit_up" in needed:
            a["limit_up"] = flags
        if "limit_streak" in needed:
            st = []
            run = 0
            for f in flags:
                run = run + 1 if f else 0
                st.append(run)
            a["limit_streak"] = st
        if "limit_count20" in needed:
            cnt = []
            win = 0
            for i, f in enumerate(flags):
                win += 1 if f else 0
                if i >= 20:
                    win -= 1 if flags[i - 20] else 0
                cnt.append(win)
            a["limit_count20"] = cnt

    need_year = "pct_ytd" in needed
    year = None
    year_base = None
    for i in range(n):
        c = closes[i]
        if "above_ma5" in a and s["ma5"][i] is not None:
            a["above_ma5"][i] = c > s["ma5"][i]
        if "above_ma20" in a and s["ma20"][i] is not None:
            a["above_ma20"][i] = c > s["ma20"][i]
        if "bias20" in a and s["ma20"][i] is not None:
            a["bias20"][i] = (c / s["ma20"][i] - 1) * 100
        if "above_ma60" in a and s["ma60"][i] is not None:
            a["above_ma60"][i] = c > s["ma60"][i]
        if "ma_bull" in a and s["ma60"][i] is not None:
            a["ma_bull"][i] = bool(s["ma5"][i] > s["ma10"][i] > s["ma20"][i] > s["ma60"][i])
        if "dist_high60" in a:
            hi60 = s["high60"][i]
            if hi60:
                a["dist_high60"][i] = (c / hi60 - 1) * 100
                if "breakout60" in a:
                    a["breakout60"][i] = c >= hi60 * 0.999
        if "breakout20" in a:
            hi20 = s["high20"][i]
            if hi20:
                a["breakout20"][i] = c >= hi20 * 0.999
        if "boll_pos" in a:
            up, lo = s["bup"][i], s["blow"][i]
            if up is not None and lo is not None and up > lo:
                a["boll_pos"][i] = (c - lo) / (up - lo)
        if "vol_ratio5" in a:
            vma = s["volma5"][i - 1] if i >= 1 else None
            if vma:
                a["vol_ratio5"][i] = vols[i] / vma
        if "up_days" in a and i >= 1 and c > closes[i - 1]:
            a["up_days"][i] = a["up_days"][i - 1] + 1
        if "pct_60d" in a and i >= 60 and closes[i - 60]:
            a["pct_60d"][i] = (c / closes[i - 60] - 1) * 100
        if "amplitude" in a:
            prev = closes[i - 1] if i >= 1 else None
            if prev:
                a["amplitude"][i] = (highs[i] - lows[i]) / prev * 100
        if need_year:
            y = bars[i]["date"][:4]
            if y != year:
                year = y
                year_base = c
            if year_base:
                a["pct_ytd"][i] = (c / year_base - 1) * 100
    return a


def _load_calendar(start, end):
    idx = store.get_index_bars("sh000300", start, end)
    return [b["date"] for b in idx], idx


def run(conditions=None, universe=None, start=None, end=None,
        hold_days=5, max_positions=10, capital=1000000, preset_name=""):
    t0 = time.time()
    conds, warnings = _prepare_conditions(conditions)
    if not conds:
        return {"ok": False, "error": "没有可用的筛选条件"}
    universe = universe or {}
    stocks = store.load_stocks()
    if not stocks:
        return {"ok": False, "error": "尚未同步数据，请先初始化"}

    stats0 = store.stats()
    if not stats0["bar_max_date"]:
        return {"ok": False, "error": "尚无K线数据，请先在「数据管理」初始化"}

    end = end or stats0["bar_max_date"]
    start = start or (dt.datetime.strptime(end, "%Y-%m-%d").date() - dt.timedelta(days=365)).strftime("%Y-%m-%d")
    if start >= end:
        return {"ok": False, "error": "开始日期需早于结束日期"}

    dates, index_bars = _load_calendar(start, end)
    if not dates:
        return {"ok": False, "error": "缺少沪深300指数数据，请先在「数据管理」同步"}
    if len(dates) < 20:
        return {"ok": False, "error": "区间内交易日过少，请扩大回测区间"}

    universe_codes = []
    for code, meta in stocks.items():
        board = meta.get("board") or ""
        if universe.get("boards") and board not in universe["boards"]:
            continue
        if universe.get("exclude_st", True) and meta.get("is_st"):
            continue
        universe_codes.append(code)

    load_start = (dt.datetime.strptime(start, "%Y-%m-%d").date() - dt.timedelta(days=300)).strftime("%Y-%m-%d")
    by_date = {}
    signals = 0
    limit_skipped = 0
    max_per_day = max(50, max_positions * 5)
    fields = [c["field"] for c in conds]
    needed = set(fields) | {"pct", "pct_60d", "turnover", "ma_bull", "above_ma20",
                            "dist_high60", "rsi6", "vol_ratio5", "volume_ratio"}

    BATCH = 200
    for bi in range(0, len(universe_codes), BATCH):
        chunk = universe_codes[bi:bi + BATCH]
        bars_map = store.get_bars_many(chunk, start=load_start)
        for code in chunk:
            bars = bars_map.get(code) or []
            if len(bars) < 70:
                continue
            meta = stocks.get(code) or {}
            shares = (meta.get("total_shares") or 0) or 0
            fshares = (meta.get("float_shares") or 0) or 0
            s = indicators.compute(bars)
            a = _build_arrays(s, bars, meta, shares, fshares, needed)
            n = len(bars)
            for i in range(60, n - 1):
                d = bars[i]["date"]
                if d < start or d > end:
                    continue
                ok = True
                for c in conds:
                    arr = a.get(c["field"])
                    if arr is None:
                        ok = False
                        break
                    v = arr[i]
                    if v is None or not _cmp(c.get("op", "gte"), v, c.get("value"), c.get("value2")):
                        ok = False
                        break
                if not ok:
                    continue
                buy = bars[i + 1]
                if buy["date"] <= d or not buy.get("volume"):
                    continue
                lim = limits.limit_pct(meta.get("board"), meta.get("is_st"))
                if buy["open"] >= bars[i]["close"] * (1 + lim - 0.002):
                    limit_skipped += 1
                    continue
                sell_i = min(i + 1 + hold_days, n - 1)
                sell = bars[sell_i]
                if sell["date"] <= buy["date"]:
                    continue
                score = screener.compute_score({
                    "pct": a["pct"][i], "pct_60d": a["pct_60d"][i],
                    "turnover": a["turnover"][i], "volume_ratio": a["vol_ratio5"][i],
                    "ma_bull": a["ma_bull"][i], "above_ma20": a["above_ma20"][i],
                    "dist_high60": a["dist_high60"][i], "rsi6": a["rsi6"][i],
                    "vol_ratio5": a["vol_ratio5"][i], "pe": None, "pb": None,
                })
                cand = {
                    "code": code, "name": meta.get("name") or "",
                    "score": score, "buy_date": buy["date"], "buy_price": buy["open"],
                    "sell_date": sell["date"],
                    "sell_price": sell["open"] if sell_i < n - 1 else sell["close"],
                    "closes": [bars[k]["close"] for k in range(i + 1, sell_i + 1)],
                }
                lst = by_date.get(buy["date"])
                if lst is None:
                    by_date[buy["date"]] = [cand]
                else:
                    lst.append(cand)
                signals += 1
            # 控制内存：及时裁剪
            for dkey in list(by_date.keys()):
                if len(by_date[dkey]) > max_per_day * 4:
                    by_date[dkey].sort(key=lambda x: x["score"], reverse=True)
                    del by_date[dkey][max_per_day:]

    if signals == 0:
        return {"ok": False, "error": "该区间没有股票满足条件，请放宽筛选条件或调整区间"}

    for d in by_date:
        by_date[d].sort(key=lambda x: x["score"], reverse=True)
        by_date[d] = by_date[d][:max_per_day]

    cash = float(capital)
    positions = []
    equity_curve = []
    trades = []
    idx_close = {b["date"]: b["close"] for b in index_bars}
    bench_base = None
    for b in index_bars:
        if b["date"] >= start:
            bench_base = b["close"]
            break

    max_pos = max(1, int(max_positions))
    hold = max(1, int(hold_days))

    for di, date in enumerate(dates):
        still = []
        for p in positions:
            if p["sell_idx"] <= di:
                proceeds = p["shares"] * p["sell_price"] * (1 - FEE_SELL)
                cash += proceeds
                ret = (p["sell_price"] * (1 - FEE_SELL)) / (p["buy_price"] * (1 + FEE_BUY)) - 1
                trades.append({
                    "code": p["code"], "name": p["name"],
                    "buy_date": p["buy_date"], "sell_date": p["sell_date"],
                    "buy_price": round(p["buy_price"], 3), "sell_price": round(p["sell_price"], 3),
                    "return_pct": round(ret * 100, 2), "days": p["days"], "score": p["score"],
                })
            else:
                still.append(p)
        positions = still

        cands = by_date.get(date) or []
        slots = max_pos - len(positions)
        if cands and slots > 0 and cash > 0:
            equity_prev = equity_curve[-1]["equity"] if equity_curve else capital
            target = equity_prev / max_pos
            for cand in cands:
                if slots <= 0:
                    break
                price = cand["buy_price"]
                budget = min(target, cash)
                shares_n = int(budget / (price * 100)) * 100
                if shares_n < 100:
                    continue
                cost = shares_n * price * (1 + FEE_BUY)
                if cost > cash:
                    shares_n = int(cash / (price * 100 * (1 + FEE_BUY))) * 100
                    if shares_n < 100:
                        continue
                    cost = shares_n * price * (1 + FEE_BUY)
                cash -= cost
                sell_idx = di + hold
                if sell_idx >= len(dates):
                    sell_idx = len(dates) - 1
                positions.append({
                    "code": cand["code"], "name": cand["name"], "shares": shares_n,
                    "buy_price": price, "buy_date": date, "buy_idx": di,
                    "sell_idx": sell_idx, "sell_price": cand["sell_price"],
                    "sell_date": cand["sell_date"], "closes": cand["closes"],
                    "days": hold, "score": cand["score"],
                })
                slots -= 1

        mv = 0.0
        for p in positions:
            off = di - p["buy_idx"]
            path = p["closes"]
            px = path[off] if 0 <= off < len(path) else (path[-1] if path else p["buy_price"])
            mv += p["shares"] * px
        equity = cash + mv
        equity_curve.append({"date": date, "equity": round(equity, 2),
                             "positions": len(positions), "cash": round(cash, 2)})

    final_equity = equity_curve[-1]["equity"]
    total_ret = final_equity / capital - 1
    n_days = len(dates)
    annual = (1 + total_ret) ** (252.0 / n_days) - 1 if total_ret > -1 else -1

    peak = -1e18
    max_dd = 0.0
    dd_series = []
    rets = []
    prev_eq = capital
    for pt in equity_curve:
        eq = pt["equity"]
        peak = max(peak, eq)
        dd = eq / peak - 1
        max_dd = min(max_dd, dd)
        dd_series.append({"date": pt["date"], "dd": round(dd * 100, 2)})
        rets.append(eq / prev_eq - 1 if prev_eq else 0)
        prev_eq = eq

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0

    wins = [t for t in trades if t["return_pct"] > 0]
    losses = [t for t in trades if t["return_pct"] <= 0]
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = -sum(t["return_pct"] for t in losses)
    avg_pos = sum(p["positions"] for p in equity_curve) / len(equity_curve)

    bench_curve = []
    if index_bars and bench_base:
        for d in dates:
            b = idx_close.get(d)
            if b:
                bench_curve.append({"date": d, "equity": round(capital * b / bench_base, 2)})

    stats = {
        "total_return": round(total_ret * 100, 2),
        "annual_return": round(annual * 100, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "trades": len(trades),
        "avg_trade": round(sum(t["return_pct"] for t in trades) / len(trades), 2) if trades else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_positions": round(avg_pos, 1),
        "final_equity": round(final_equity, 2),
        "signals": signals,
        "limit_skipped": limit_skipped,
        "days": n_days,
        "start": dates[0],
        "end": dates[-1],
        "benchmark_return": round((bench_curve[-1]["equity"] / capital - 1) * 100, 2) if bench_curve else None,
    }

    return {
        "ok": True,
        "stats": stats,
        "curve": equity_curve,
        "benchmark": bench_curve,
        "drawdown": dd_series,
        "trades": list(reversed(trades))[:100],
        "warnings": warnings,
        "elapsed": round(time.time() - t0, 1),
        "preset": preset_name,
        "hold_days": hold,
        "max_positions": max_pos,
    }
