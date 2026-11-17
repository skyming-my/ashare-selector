# -*- coding: utf-8 -*-
"""数据源：新浪（全市场快照 / 行业 / 备用K线） + 腾讯（日K线）。

东财接口在部分网络环境会限流甚至临时封 IP，因此不作为主数据源。
"""
import datetime as dt
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import net

SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page")
SINA_COUNT = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "Market_Center.getHQNodeStockCount?node=hs_a")
SINA_HY = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
SINA_KLINE = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
              "symbol=%s&scale=240&ma=no&datalen=%d")
TENCENT_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                 "param=%s,day,%s,%s,640,qfq")


def _num(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_board(code):
    c = str(code)
    if c.startswith("68"):
        return "科创板"
    if c.startswith("60"):
        return "沪市主板"
    if c.startswith("30"):
        return "创业板"
    if c.startswith(("8", "4", "92")):
        return "北交所"
    if c.startswith(("00", "001", "002", "003")):
        return "深市主板"
    return "其他"


def is_st_name(name):
    n = (name or "").upper().replace(" ", "")
    return ("ST" in n) or ("退" in n)


def tencent_symbol(code, board=None):
    c = str(code)
    if c.startswith(("4", "8", "92")) or board == "北交所":
        return "bj" + c
    if c.startswith(("6", "900")):
        return "sh" + c
    return "sz" + c


# ---------------- 全市场快照（新浪） ----------------

def _normalize_sina(row):
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    name = row.get("name") or ""
    price = _num(row.get("trade"))
    prev = _num(row.get("settlement"))
    high = _num(row.get("high"))
    low = _num(row.get("low"))
    vol = _num(row.get("volume"))       # 股
    amount = _num(row.get("amount"))   # 元
    mktcap_w = _num(row.get("mktcap"))  # 万元
    nmc_w = _num(row.get("nmc"))        # 万元
    mktcap = mktcap_w * 1e4 if mktcap_w is not None else None
    fmc = nmc_w * 1e4 if nmc_w is not None else None
    total_shares = (mktcap / price) if (mktcap and price) else None
    float_shares = (fmc / price) if (fmc and price) else None
    amplitude = ((high - low) / prev * 100) if (high is not None and low is not None and prev) else None
    return {
        "code": code,
        "name": name,
        "market": 1 if code.startswith("6") else 0,
        "board": classify_board(code),
        "is_st": is_st_name(name),
        "price": price,
        "pct": _num(row.get("changepercent")),
        "change": _num(row.get("pricechange")),
        "volume": (vol / 100.0) if vol is not None else None,   # 手
        "amount": amount,
        "amplitude": amplitude,
        "turnover": _num(row.get("turnoverratio")),
        "pe": _num(row.get("per")),
        "volume_ratio": None,
        "high": high,
        "low": low,
        "open": _num(row.get("open")),
        "prev_close": prev,
        "mktcap": mktcap,
        "float_mktcap": fmc,
        "speed": None,
        "pb": _num(row.get("pb")),
        "pct_60d": None,
        "pct_ytd": None,
        "list_date": "",
        "roe": None,
        "total_shares": total_shares,
        "float_shares": float_shares,
        "industry": "",
        "area": "",
        "concepts": "",
        "sina_symbol": row.get("symbol") or "",
        "ticktime": row.get("ticktime") or "",
    }


def fetch_spot_page(page):
    raw = net.fetch(SINA_LIST % page, timeout=20, retries=4)
    text = raw.decode("gbk", "replace").strip()
    if not text or text == "null" or text == "[]":
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [r for r in (_normalize_sina(x) for x in rows) if r]


def fetch_all_spot(progress=None):
    total = None
    try:
        total = int(json.loads(net.fetch(SINA_COUNT, timeout=15, retries=3).decode("utf-8")).strip('"'))
    except Exception:
        total = None

    out = []
    seen = set()

    def add(rows):
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            out.append(r)

    first = fetch_spot_page(1)
    add(first)
    if progress:
        progress(len(out), total or len(out))

    if total:
        pages = (total + 99) // 100 + 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_spot_page, p): p for p in range(2, pages + 1)}
            for fut in as_completed(futures):
                page = futures[fut]
                try:
                    rows = fut.result()
                except Exception:
                    try:
                        rows = fetch_spot_page(page)
                    except Exception:
                        rows = []
                add(rows)
                if progress:
                    progress(len(out), total)
    else:
        # 总数未知时退回顺序翻页
        page = 2
        while page <= 120:
            rows = fetch_spot_page(page)
            if not rows:
                break
            add(rows)
            if progress:
                progress(len(out), len(out))
            if len(rows) < 100:
                break
            page += 1
            time.sleep(0.03)
    return out


def fetch_index_quote(sym="sh000001"):
    """指数快照，用于确认最近交易日与收盘时间。"""
    raw = net.fetch("https://hq.sinajs.cn/list=" + sym, timeout=8, retries=2,
                    headers={"Referer": "https://finance.sina.com.cn"})
    text = raw.decode("gbk", "replace")
    m = re.search(r'var hq_str_\w+="(.*)";', text)
    if not m:
        return None
    parts = m.group(1).split(",")
    if len(parts) < 32:
        return None
    return {
        "price": _num(parts[3]),
        "date": parts[30],
        "time": parts[31],
    }


# ---------------- 日K线（腾讯，北交所回退新浪） ----------------

def _bars_from_tencent(entries, float_shares=None):
    bars = []
    prev = None
    for e in entries:
        if len(e) < 6:
            continue
        d = e[0]
        o, c, h, lo = _num(e[1]), _num(e[2]), _num(e[3]), _num(e[4])
        vol = _num(e[5]) or 0.0   # 手
        if None in (o, c, h, lo):
            continue
        pct = ((c / prev - 1) * 100) if prev else None
        amount = vol * 100 * c
        turnover = (vol * 100 / float_shares * 100) if float_shares else None
        bars.append((d, o, h, lo, c, vol, amount, turnover, pct))
        prev = c
    return bars


def _limit_for(board):
    if board == "创业板" or board == "科创板":
        return 20.0
    if board == "北交所":
        return 30.0
    return 10.0


def _adjust_actions(bars, limit_pct):
    """用涨跌幅上限识别送转/拆股，做近似前复权。"""
    n = len(bars)
    if n < 2:
        return bars
    fac = [1.0] * n
    mult = 1.0
    for i in range(n - 1, 0, -1):
        fac[i] = mult
        prev = bars[i - 1][4]
        cur = bars[i][4]
        if not prev or not cur:
            continue
        ratio = cur / prev
        if abs(ratio - 1) * 100 > limit_pct + 1.0:
            mult *= ratio
    fac[0] = mult
    out = []
    prev_close = None
    for i, b in enumerate(bars):
        f = fac[i]
        d, o, h, lo, c, vol, amt, turn, _ = b
        if f == 1.0:
            pct = ((c / prev_close - 1) * 100) if prev_close else None
            out.append((d, o, h, lo, c, vol, amt, turn, pct))
            prev_close = c
            continue
        o2, h2, lo2, c2 = o * f, h * f, lo * f, c * f
        v2 = vol / f if f else vol
        pct = ((c2 / prev_close - 1) * 100) if prev_close else None
        out.append((d, o2, h2, lo2, c2, v2, amt, turn, pct))
        prev_close = c2
    return out


def _tencent_once(sym, beg, end):
    url = TENCENT_KLINE % (sym, beg, end)
    data = net.get_json(url, timeout=25, retries=4)
    node = (data or {}).get("data", {}).get(sym) or {}
    return node.get("qfqday") or node.get("day") or []


def fetch_kline_tencent(sym, beg, end=None, float_shares=None):
    beg_d = dt.datetime.strptime(beg, "%Y%m%d").date()
    end_d = dt.datetime.strptime(end, "%Y%m%d").date() if end else dt.date.today()
    all_entries = []
    cur_end = end_d
    for _ in range(5):
        entries = _tencent_once(sym, beg_d.strftime("%Y-%m-%d"), cur_end.strftime("%Y-%m-%d"))
        if not entries:
            break
        all_entries = entries + all_entries
        first = entries[0][0]
        try:
            first_d = dt.datetime.strptime(first, "%Y-%m-%d").date()
        except ValueError:
            break
        if first_d <= beg_d or len(entries) < 640:
            break
        cur_end = first_d - dt.timedelta(days=1)
        time.sleep(0.05)
    return _bars_from_tencent(all_entries, float_shares)


def fetch_kline_sina(sym, datalen=900, float_shares=None, board=None):
    url = SINA_KLINE % (sym, datalen)
    raw = net.fetch(url, timeout=25, retries=4)
    try:
        rows = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return []
    bars = []
    prev = None
    for r in rows or []:
        d = r.get("day")
        o, c, h, lo = _num(r.get("open")), _num(r.get("close")), _num(r.get("high")), _num(r.get("low"))
        vol = (_num(r.get("volume")) or 0.0) / 100.0
        if not d or None in (o, c, h, lo):
            continue
        pct = ((c / prev - 1) * 100) if prev else None
        bars.append((d, o, h, lo, c, vol, vol * 100 * c,
                     (vol * 100 / float_shares * 100) if float_shares else None, pct))
        prev = c
    if board:
        bars = _adjust_actions(bars, _limit_for(board))
    return bars


_tencent_ok = False  # 腾讯前复权接口当前返回 501，默认直接使用新浪源


def fetch_kline(code, board=None, beg="20200101", end=None, float_shares=None):
    """优先腾讯前复权；接口不可用时回退新浪并做拆股近似复权。"""
    global _tencent_ok
    sym = tencent_symbol(code, board)
    if _tencent_ok is not False:
        try:
            bars = fetch_kline_tencent(sym, beg, end, float_shares)
            if bars:
                _tencent_ok = True
                return bars
        except Exception:
            pass
        _tencent_ok = False
    try:
        return fetch_kline_sina(sym, 900, float_shares, board)
    except Exception:
        return []


def fetch_index_kline(sym, beg="20200101", end=None):
    try:
        return fetch_kline_sina(sym, 900)
    except Exception:
        return []


def fetch_quotes(codes):
    """新浪批量实时行情（一次请求可查多只）。"""
    out = {}
    codes = [str(c) for c in codes if c]
    for i in range(0, len(codes), 80):
        chunk = codes[i:i + 80]
        syms = ",".join(tencent_symbol(c) for c in chunk)
        try:
            raw = net.fetch("https://hq.sinajs.cn/list=" + syms, timeout=6, retries=1,
                            headers={"Referer": "https://finance.sina.com.cn"})
        except Exception:
            continue
        text = raw.decode("gbk", "replace")
        for line in text.splitlines():
            m = re.match(r'var hq_str_([a-z]{2}\d+)="(.*)";?', line.strip())
            if not m:
                continue
            sym, payload = m.group(1), m.group(2)
            parts = payload.split(",")
            if len(parts) < 32:
                continue

            def _f(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            code = sym[2:]
            price, prev = _f(parts[3]), _f(parts[2])
            out[code] = {
                "code": code,
                "name": parts[0],
                "price": price,
                "prev_close": prev,
                "open": _f(parts[1]),
                "high": _f(parts[4]),
                "low": _f(parts[5]),
                "volume": (_f(parts[8]) or 0) / 100,
                "amount": _f(parts[9]),
                "pct": ((price / prev - 1) * 100) if price and prev else None,
                "date": parts[30] if len(parts) > 30 else "",
                "time": parts[31] if len(parts) > 31 else "",
            }
    return out


# ---------------- 行业分类（新浪） ----------------

def fetch_industry_map(progress=None):
    raw = net.fetch(SINA_HY, timeout=25, retries=3)
    text = raw.decode("gbk", "replace")
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S)
    if not m:
        m = re.search(r"=\s*(\{.*\})", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    nodes = []
    for node, val in data.items():
        parts = str(val).split(",")
        if len(parts) >= 2 and parts[0] and parts[1]:
            nodes.append((parts[0], parts[1]))
    mapping = {}
    total = len(nodes)
    for i, (node, name) in enumerate(nodes):
        page = 1
        while page <= 20:
            url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=%s&symbol=&_s_r_a=page"
                   % (page, node))
            try:
                raw = net.fetch(url, timeout=20, retries=3)
                rows = json.loads(raw.decode("gbk", "replace"))
            except Exception:
                rows = []
            if not rows:
                break
            for r in rows:
                code = str(r.get("code") or "")
                if code:
                    mapping[code] = name
            if len(rows) < 100:
                break
            page += 1
        if progress:
            progress(i + 1, total)
        time.sleep(0.02)
    return mapping
