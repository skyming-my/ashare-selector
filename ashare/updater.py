# -*- coding: utf-8 -*-
"""数据同步：行情快照、日线（收盘快照合并 / 全量）、技术指标缓存、指数、行业。"""
import datetime as dt
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, datasource, indicators, limits, store

TECH_TAIL = 320   # 指标只取最近 320 根，覆盖 MA60 / 一年涨幅
MAX_BARS = 900

_lock = threading.Lock()
_state = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "ok": 0,
    "failed": 0,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "mode": "",
    "years": 0,
    "elapsed": 0,
    "errors": [],
}
_thread = None


def _set(**kw):
    with _lock:
        _state.update(kw)


def _tick():
    with _lock:
        if _state["started_at"]:
            _state["elapsed"] = round(time.time() - _state["started_at"], 1)


def status():
    with _lock:
        st = dict(_state)
        st["errors"] = list(_state["errors"])[-12:]
    if st["started_at"] and st["running"]:
        st["elapsed"] = round(time.time() - st["started_at"], 1)
    return st


def is_running():
    with _lock:
        return _state["running"]


def start(years=None, mode="auto"):
    global _thread
    if is_running():
        return False
    years = max(1, min(3, int(years or config.DEFAULT_YEARS)))
    _set(running=True, phase="准备中", done=0, total=0, ok=0, failed=0,
         message="", started_at=time.time(), finished_at=None,
         mode=mode, years=years, elapsed=0, errors=[])
    _thread = threading.Thread(target=_run, args=(years, mode), daemon=True)
    _thread.start()
    return True


def start_spot_refresh():
    global _thread
    if is_running():
        return False
    _set(running=True, phase="刷新行情快照", done=0, total=0, ok=0, failed=0,
         message="", started_at=time.time(), finished_at=None,
         mode="spot", years=0, elapsed=0, errors=[])
    _thread = threading.Thread(target=_run_spot_only, daemon=True)
    _thread.start()
    return True


def _run_spot_only():
    try:
        _sync_spot()
        _set(phase="完成", message="行情快照已更新")
    except Exception as exc:
        traceback.print_exc()
        _set(phase="失败", message=str(exc), errors=[str(exc)])
    finally:
        _set(running=False, finished_at=time.time())
        _tick()


def _sync_spot():
    _set(phase="行情快照", done=0, total=0, message="正在获取全市场行情")

    def progress(done, total):
        _set(done=done, total=total, message="已获取 %d / %d 只" % (done, total))
        _tick()

    rows = datasource.fetch_all_spot(progress=progress)
    if not rows:
        raise RuntimeError("行情接口返回为空")
    old = store.load_stocks()
    for r in rows:
        prev = old.get(r["code"]) or {}
        if not r.get("industry") and prev.get("industry"):
            r["industry"] = prev["industry"]
        if not r.get("list_date") and prev.get("list_date"):
            r["list_date"] = prev["list_date"]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store.replace_spot(rows, now)
    store.upsert_stocks(rows)
    store.meta_set("spot_ts", now)
    _set(ok=len(rows), total=len(rows), message="行情快照完成：%d 只" % len(rows))
    _tick()
    return rows


# ---------------- 技术指标 ----------------

def _tech_for(bars, meta, total_n):
    """由最近若干根日线计算指标快照。"""
    if not bars or len(bars) < 20:
        return None
    s = indicators.compute(bars)
    idx = len(bars) - 1
    t = indicators.snapshot_at(s, idx)
    c = s["close"][idx]
    t["breakout60"] = bool(t.get("high60") and c >= t["high60"] * 0.999)
    t["breakout20"] = bool(t.get("high20") and c >= t["high20"] * 0.999)
    if idx >= 60:
        t["pct_60d"] = round((c / s["close"][idx - 60] - 1) * 100, 2)
    year = bars[-1]["date"][:4]
    for j in range(idx, -1, -1):
        if bars[j]["date"][:4] != year:
            break
        t["pct_ytd"] = round((c / s["close"][j] - 1) * 100, 2)
    board, is_st = meta.get("board"), meta.get("is_st")
    lim = limits.limit_pct(board, is_st)
    st = limits.streak(bars, board, is_st)
    t["limit_pct"] = lim
    t["limit_streak"] = st
    t["limit_up"] = st > 0
    t["limit_down"] = limits.is_limit_down(bars[-1].get("pct"), board, is_st)
    t["limit_count20"] = limits.count_recent(bars, board, is_st, 20)
    t["limit_count5"] = limits.count_recent(bars, board, is_st, 5)
    prev = bars[-2] if len(bars) >= 2 else None
    t["prev_limit_up"] = bool(prev and limits.is_limit_up(prev.get("pct"), board, is_st))
    t["prev_pct"] = prev.get("pct") if prev else None
    t["bars_count"] = total_n
    return t


def _tech_payload(code, t, date, total_n, now):
    return (code, date, total_n, json.dumps(t, ensure_ascii=False), now)


_TECH_SQL = ("INSERT INTO tech(code,date,bars,data,updated_at) VALUES(?,?,?,?,?) "
             "ON CONFLICT(code) DO UPDATE SET date=excluded.date, bars=excluded.bars, "
             "data=excluded.data, updated_at=excluded.updated_at")


# ---------------- 收盘快照合并（快速增量） ----------------

def _closed_market(trade_date, trade_time):
    """仅当指数日期是今天且已收盘时，才可用快照合并日线。"""
    today = dt.date.today().isoformat()
    return bool(trade_date == today and trade_time and trade_time >= "15:00:00")


def _fast_update(trade_date):
    """用收盘快照自带的 OHLC 追加当日日线，免去逐只 K 线请求。"""
    spot, _ = store.load_spot()
    stocks = store.load_stocks()
    codes = [c for c, sp in spot.items() if sp.get("price") and sp.get("volume")]
    total = len(codes)
    _set(phase="日线合并", done=0, total=total, ok=0, failed=0,
         message="用收盘快照更新 %d 只股票的日线" % total)

    updated = 0
    skipped = 0
    fallback = []
    errors = []
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = 300
    c = store.bulk_connect()
    try:
        for start in range(0, total, batch):
            chunk = codes[start:start + batch]
            ph = ",".join("?" * len(chunk))
            rows = c.execute(
                "SELECT code,n,first_date,last_date,data FROM bars WHERE code IN (%s)" % ph,
                tuple(chunk)).fetchall()
            existing = {r["code"]: r for r in rows}
            bar_payload = []
            tech_payload = []
            for code in chunk:
                sp = spot[code]
                row = existing.get(code)
                if not row:
                    fallback.append(code)
                    continue
                if row["last_date"] >= trade_date:
                    skipped += 1
                    continue
                tail_bars = store.unpack_bars(row["data"][-TECH_TAIL * store.BAR_SIZE:])
                last = tail_bars[-1]
                prev = sp.get("prev_close")
                if not prev or not last.get("close") or abs(prev / last["close"] - 1) > 0.06:
                    # 除权/送转或数据异常：回退到完整 K 线
                    fallback.append(code)
                    continue
                o, h, lo, cl = sp.get("open"), sp.get("high"), sp.get("low"), sp.get("price")
                if None in (o, h, lo, cl):
                    fallback.append(code)
                    continue
                bar = (trade_date, o, h, lo, cl, sp.get("volume") or 0.0,
                       sp.get("amount") or 0.0, sp.get("turnover"), sp.get("pct"))
                blob = row["data"] + store.pack_bars([bar])
                n = row["n"] + 1
                first = row["first_date"]
                if n > MAX_BARS:
                    blob = blob[store.BAR_SIZE:]
                    n = MAX_BARS
                    first = store.unpack_bars(blob[:store.BAR_SIZE])[0]["date"]
                new_bar = {"date": trade_date, "open": o, "high": h, "low": lo, "close": cl,
                           "volume": sp.get("volume") or 0.0, "amount": sp.get("amount") or 0.0,
                           "turnover": sp.get("turnover"), "pct": sp.get("pct")}
                tail_bars = (tail_bars[1:] if len(tail_bars) >= TECH_TAIL else tail_bars) + [new_bar]
                t = _tech_for(tail_bars, stocks.get(code) or {}, n)
                if t:
                    tech_payload.append(_tech_payload(code, t, trade_date, n, now))
                bar_payload.append((blob, first, trade_date, n, code))
                updated += 1
            if bar_payload:
                c.executemany(
                    "UPDATE bars SET data=?, first_date=?, last_date=?, n=? WHERE code=?", bar_payload)
            if tech_payload:
                c.executemany(_TECH_SQL, tech_payload)
            c.commit()
            done = min(start + batch, total)
            _set(done=done, ok=updated, failed=len(fallback),
                 message="已合并 %d / %d" % (done, total), errors=errors)
            _tick()
    finally:
        store.bulk_close(c)
    _set(ok=updated, failed=len(fallback))
    return updated, fallback


# ---------------- 全量/回退 K 线 ----------------

def _sync_klines(codes=None):
    stocks = store.load_stocks()
    if codes is None:
        tasks = list(stocks.items())
    else:
        tasks = [(c, stocks.get(c) or {}) for c in codes]
    if not tasks:
        return
    _set(phase="日线数据", done=0, total=len(tasks), ok=0, failed=0,
         message="准备抓取 %d 只股票" % len(tasks))

    done = ok = failed = 0
    errors = []
    buffer = []
    tech_buffer = []
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def fetch(task):
        code, s = task
        bars = datasource.fetch_kline(code, s.get("board"), float_shares=s.get("float_shares"))
        return code, bars

    writer = store.BulkWriter()
    try:
        with ThreadPoolExecutor(max_workers=config.SYNC_WORKERS) as pool:
            futures = {pool.submit(fetch, t): t[0] for t in tasks}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    _, bars = fut.result()
                    if bars:
                        buffer.append((code, bars))
                        t = _tech_for(bars[-TECH_TAIL:], stocks.get(code) or {}, len(bars))
                        if t:
                            tech_buffer.append(_tech_payload(code, t, bars[-1]["date"], len(bars), now))
                        ok += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    if len(errors) < 10:
                        errors.append("%s: %s" % (code, exc))
                if len(buffer) >= 40:
                    writer.bars(buffer)
                    buffer = []
                    if tech_buffer:
                        writer.tech_raw(tech_buffer)
                        tech_buffer = []
                done += 1
                if done % 10 == 0 or done == len(tasks):
                    _set(done=done, ok=ok, failed=failed,
                         message="已更新 %d / %d（含指标）" % (done, len(tasks)), errors=errors)
                    _tick()
        if buffer:
            writer.bars(buffer)
        if tech_buffer:
            writer.tech_raw(tech_buffer)
    finally:
        writer.close()


# ---------------- 指数 / 行业 ----------------

def _index_stamp():
    try:
        q = datasource.fetch_index_quote("sh000001")
        if q:
            return q.get("date"), q.get("time")
    except Exception:
        pass
    return None, None


def _sync_indexes(years):
    _set(phase="指数数据", done=0, total=len(config.INDEXES), message="更新指数")
    beg = (dt.date.today() - dt.timedelta(days=years * 365 + 100)).strftime("%Y%m%d")
    ok = 0
    with ThreadPoolExecutor(max_workers=len(config.INDEXES)) as pool:
        futs = {pool.submit(datasource.fetch_index_kline, sym, beg): (sym, name)
                for sym, name in config.INDEXES}
        for fut in as_completed(futs):
            sym, name = futs[fut]
            try:
                bars = fut.result()
                if bars:
                    store.save_index_bars(sym, bars)
                ok += 1
            except Exception as exc:
                print("[index] %s %s failed: %s" % (name, sym, exc))
            _set(done=ok, total=len(config.INDEXES))
            _tick()


def _sync_industry(mode):
    stocks = store.load_stocks()
    known = sum(1 for s in stocks.values() if s.get("industry"))
    # 行业分类变化很慢：只在全量重建时刷新
    if mode != "full" and known > 0:
        return
    _set(phase="行业分类", done=0, total=0, message="获取行业分类")
    try:
        mapping = datasource.fetch_industry_map(
            progress=lambda d, t: (_set(done=d, total=t, message="行业 %d / %d" % (d, t)), _tick()))
    except Exception as exc:
        print("[industry] failed:", exc)
        return
    if mapping:
        store.set_industries(mapping)
        _set(message="行业分类完成：%d 只" % len(mapping))


# ---------------- 主流程 ----------------

def _run(years, mode):
    try:
        _sync_spot()
        trade_date, trade_time = _index_stamp()
        last = store.meta_get("bar_max_date") or ""

        fast_done = False
        if (mode != "full" and trade_date and trade_date > last
                and _closed_market(trade_date, trade_time)):
            updated, fallback = _fast_update(trade_date)
            if fallback:
                _sync_klines(fallback)
            fast_done = True
            _set(message="收盘快照合并完成：%d 只" % updated)

        if not fast_done and mode != "full" and trade_date and last and trade_date <= last:
            _set(message="日线已是最新（%s），跳过抓取" % last)
        elif not fast_done:
            _sync_klines()

        maxd = store.query("SELECT MAX(last_date) AS d FROM bars", one=True)
        if maxd and maxd["d"]:
            store.meta_set("bar_max_date", maxd["d"])
        _sync_indexes(years)
        _sync_industry(mode)

        st = store.stats()
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store.meta_set("last_sync", now)
        store.meta_set("sync_years", years)
        if st["bar_max_date"]:
            store.meta_set("bar_max_date", st["bar_max_date"])
        _set(phase="完成", message="同步完成：%d 只股票，K线至 %s" % (st["bar_codes"], st["bar_max_date"] or "-"))
    except Exception as exc:
        traceback.print_exc()
        _set(phase="失败", message=str(exc), errors=[str(exc)])
    finally:
        _set(running=False, finished_at=time.time())
        _tick()
