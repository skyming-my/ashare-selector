# -*- coding: utf-8 -*-
"""技术指标计算（纯 Python，无第三方依赖）。"""
from collections import deque


def sma(values, n):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values, n):
    out = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(closes):
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


def rsi(values, n=6):
    out = [None] * len(values)
    if len(values) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        ch = values[i] - values[i - 1]
        gain += ch if ch > 0 else 0.0
        loss += -ch if ch < 0 else 0.0
    ag, al = gain / n, loss / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(values)):
        ch = values[i] - values[i - 1]
        ag = (ag * (n - 1) + (ch if ch > 0 else 0.0)) / n
        al = (al * (n - 1) + (-ch if ch < 0 else 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def kdj(highs, lows, closes, n=9):
    n_data = len(closes)
    k_out, d_out, j_out = [None] * n_data, [None] * n_data, [None] * n_data
    k = d = 50.0
    hq, lq = deque(), deque()
    for i in range(n_data):
        hi, lo = highs[i], lows[i]
        while hq and highs[hq[-1]] <= hi:
            hq.pop()
        hq.append(i)
        while lq and lows[lq[-1]] >= lo:
            lq.pop()
        lq.append(i)
        if hq[0] <= i - n:
            hq.popleft()
        if lq[0] <= i - n:
            lq.popleft()
        hh, ll = highs[hq[0]], lows[lq[0]]
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
        k_out[i], d_out[i], j_out[i] = k, d, 3 * k - 2 * d
    return k_out, d_out, j_out


def boll(closes, n=20, mult=2.0):
    mid = [None] * len(closes)
    up, low = [None] * len(closes), [None] * len(closes)
    s = s2 = 0.0
    for i, c in enumerate(closes):
        s += c
        s2 += c * c
        if i >= n:
            old = closes[i - n]
            s -= old
            s2 -= old * old
        if i >= n - 1:
            mean = s / n
            var = s2 / n - mean * mean
            sd = var ** 0.5 if var > 0 else 0.0
            mid[i] = mean
            up[i] = mean + mult * sd
            low[i] = mean - mult * sd
    return mid, up, low


def rolling_max(values, n):
    out = [None] * len(values)
    dq = deque()
    for i, v in enumerate(values):
        while dq and values[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
        if i >= n - 1:
            out[i] = values[dq[0]]
    return out


def cross_days(fast, slow, i, look=30):
    """fast 上穿 slow 距今天数（0 表示今天金叉）；未交叉或已死叉返回 None。"""
    if i <= 0 or fast[i] is None or slow[i] is None or fast[i - 1] is None or slow[i - 1] is None:
        return None
    if fast[i] <= slow[i]:
        return None
    lo = max(1, i - look + 1)
    for j in range(i, lo - 1, -1):
        if fast[j] is None or slow[j] is None or fast[j - 1] is None or slow[j - 1] is None:
            continue
        if fast[j] > slow[j] and fast[j - 1] <= slow[j - 1]:
            return i - j
    return None


def compute(bars):
    """把日线列表转换为指标数组。bars 元素需含 open/high/low/close/volume。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["volume"] or 0.0 for b in bars]
    dif, dea, hist = macd(closes)
    k, d, j = kdj(highs, lows, closes)
    bmid, bup, blow = boll(closes)
    return {
        "bars": bars,
        "close": closes,
        "high": highs,
        "low": lows,
        "volume": vols,
        "ma5": sma(closes, 5),
        "ma10": sma(closes, 10),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "dif": dif, "dea": dea, "hist": hist,
        "k": k, "d": d, "j": j,
        "rsi6": rsi(closes, 6),
        "rsi12": rsi(closes, 12),
        "bmid": bmid, "bup": bup, "blow": blow,
        "high60": rolling_max(highs, 60),
        "high20": rolling_max(highs, 20),
        "volma5": sma(vols, 5),
    }


def snapshot_at(s, i):
    """取第 i 根 K 线的技术指标快照。"""
    c = s["close"][i]
    ma5, ma10, ma20, ma60 = s["ma5"][i], s["ma10"][i], s["ma20"][i], s["ma60"][i]
    dif, dea, hist = s["dif"][i], s["dea"][i], s["hist"][i]
    k, d, j = s["k"][i], s["d"][i], s["j"][i]
    r6, r12 = s["rsi6"][i], s["rsi12"][i]
    up, lo, mid = s["bup"][i], s["blow"][i], s["bmid"][i]
    h60 = s["high60"][i]
    vol = s["volume"][i]
    vma5 = s["volma5"][i - 1] if i >= 1 else None

    up_days = 0
    for t in range(i, 0, -1):
        if s["close"][t] > s["close"][t - 1]:
            up_days += 1
        else:
            break

    cross = cross_days(s["dif"], s["dea"], i, look=20)
    kdj_cross = cross_days(s["k"], s["d"], i, look=20)
    bull = all(x is not None for x in (ma5, ma10, ma20, ma60)) and ma5 > ma10 > ma20 > ma60
    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "ma_bull": bull,
        "above_ma5": (c > ma5) if ma5 else None,
        "above_ma20": (c > ma20) if ma20 else None,
        "above_ma60": (c > ma60) if ma60 else None,
        "dif": dif, "dea": dea, "hist": hist,
        "macd_cross": cross,
        "kdj_k": k, "kdj_d": d, "kdj_j": j,
        "kdj_cross": kdj_cross,
        "rsi6": r6, "rsi12": r12,
        "boll_up": up, "boll_low": lo,
        "boll_pos": ((c - lo) / (up - lo)) if (up is not None and lo is not None and up > lo) else None,
        "high60": h60,
        "high20": s["high20"][i],
        "dist_high60": ((c / h60 - 1) * 100) if h60 else None,
        "vol_ratio5": (vol / vma5) if vma5 else None,
        "up_days": up_days,
        "bias20": ((c / ma20 - 1) * 100) if ma20 else None,
    }
