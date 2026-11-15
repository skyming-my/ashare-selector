# -*- coding: utf-8 -*-
"""数据同步：行情快照、日线（全量/增量）、技术指标缓存、指数、行业。"""
import datetime as dt
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, datasource, indicators, limits, store

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


def _run(years, mode):
    try:
        _sync_spot()
        _sync_klines()
        maxd = store.query("SELECT MAX(last_date) AS d FROM bars", one=True)
        if maxd and maxd["d"]:
            store.meta_set("bar_max_date", maxd["d"])
        _compute_tech()
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


def _sync_klines():
    stocks = store.load_stocks()
    if not stocks:
        raise RuntimeError("股票列表为空")

    tasks = list(stocks.items())
    _set(phase="日线数据", done=0, total=len(tasks), ok=0, failed=0,
         message="准备抓取 %d 只股票" % len(tasks))

    done = ok = failed = 0
    errors = []
    buffer = []

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
                done += 1
                if done % 10 == 0 or done == len(tasks):
                    _set(done=done, ok=ok, failed=failed,
                         message="已更新 %d / %d" % (done, len(tasks)), errors=errors)
                    _tick()
        if buffer:
            writer.bars(buffer)
    finally:
        writer.close()


def _compute_tech():
    stocks = store.load_stocks()
    codes = list(stocks.keys())
    _set(phase="技术指标", done=0, total=len(codes), message="计算全市场技术指标")
    max_date = store.meta_get("bar_max_date") or ""
    if max_date:
        start = (dt.datetime.strptime(max_date, "%Y-%m-%d").date() - dt.timedelta(days=400)).strftime("%Y-%m-%d")
    else:
        start = (dt.date.today() - dt.timedelta(days=400)).strftime("%Y-%m-%d")

    done = 0
    batch_size = 200
    writer = store.BulkWriter()
    try:
        for i in range(0, len(codes), batch_size):
            chunk = codes[i:i + batch_size]
            bars_map = store.get_bars_many(chunk, start=start)
            items = []
            for code in chunk:
                bars = bars_map.get(code) or []
                if len(bars) < 20:
                    continue
                meta = stocks.get(code) or {}
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
                lim = limits.limit_pct(meta.get("board"), meta.get("is_st"))
                st = limits.streak(bars, meta.get("board"), meta.get("is_st"))
                t["limit_pct"] = lim
                t["limit_streak"] = st
                t["limit_up"] = st > 0
                t["limit_down"] = limits.is_limit_down(bars[-1].get("pct"), meta.get("board"), meta.get("is_st"))
                t["limit_count20"] = limits.count_recent(bars, meta.get("board"), meta.get("is_st"), 20)
                t["limit_count5"] = limits.count_recent(bars, meta.get("board"), meta.get("is_st"), 5)
                prev = bars[-2] if len(bars) >= 2 else None
                t["prev_limit_up"] = bool(prev and limits.is_limit_up(
                    prev.get("pct"), meta.get("board"), meta.get("is_st")))
                t["prev_pct"] = prev.get("pct") if prev else None
                items.append((code, bars[-1]["date"], len(bars), t))
            writer.tech(items)
            done += len(chunk)
            _set(done=done, message="已计算 %d / %d" % (done, len(codes)))
            _tick()
    finally:
        writer.close()


def _sync_indexes(years):
    _set(phase="指数数据", done=0, total=len(config.INDEXES), message="更新指数")
    beg = (dt.date.today() - dt.timedelta(days=years * 365 + 100)).strftime("%Y%m%d")
    ok = 0
    for sym, name in config.INDEXES:
        try:
            bars = datasource.fetch_index_kline(sym, beg=beg)
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
    # 行业分类变化很慢：只在全量重建时刷新，避免每次同步多花约 1 分钟
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
