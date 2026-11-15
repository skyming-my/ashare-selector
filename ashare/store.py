# -*- coding: utf-8 -*-
"""SQLite 存储层。

日线按「每只股票一条二进制记录」保存：5 百万行逐行写入在本机磁盘上会产生
大量随机写，改为单行 BLOB 后写入量降到 5578 行，同步速度提升两个数量级。
"""
import json
import os
import sqlite3
import struct
import threading
from contextlib import contextmanager

from . import config

_init_lock = threading.Lock()
_initialized = False

# date(YYYYMMDD), open, high, low, close, volume(手), amount(亿元), turnover(%), pct(%)
_BAR = struct.Struct("<I8f")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks(
  code TEXT PRIMARY KEY,
  name TEXT,
  market INTEGER,
  board TEXT,
  is_st INTEGER,
  list_date TEXT,
  industry TEXT,
  data TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS spot(
  code TEXT PRIMARY KEY,
  data TEXT,
  ts TEXT
);
CREATE TABLE IF NOT EXISTS bars(
  code TEXT PRIMARY KEY,
  first_date TEXT,
  last_date TEXT,
  n INTEGER,
  data BLOB
);
CREATE TABLE IF NOT EXISTS index_daily(
  secid TEXT,
  date TEXT,
  close REAL,
  pct REAL,
  PRIMARY KEY(secid, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tech(
  code TEXT PRIMARY KEY,
  date TEXT,
  bars INTEGER,
  data TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS watchlist(
  code TEXT PRIMARY KEY,
  added_at TEXT
);
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def init():
    global _initialized
    with _init_lock:
        if _initialized:
            return
        os.makedirs(config.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            # 旧版逐行表：释放空间
            conn.execute("DROP TABLE IF EXISTS daily")
            conn.commit()
        finally:
            conn.close()
        _initialized = True


@contextmanager
def conn():
    init()
    c = sqlite3.connect(config.DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=60000")
    try:
        yield c
    finally:
        c.close()


def query(sql, args=(), one=False):
    with conn() as c:
        cur = c.execute(sql, args)
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount


def executemany(sql, seq):
    with conn() as c:
        c.executemany(sql, seq)
        c.commit()


# ---------------- 批量写入器 ----------------

class BulkWriter:
    """同步期间使用：单连接、关闭自动 checkpoint、按批提交。"""

    def __init__(self):
        init()
        self.c = sqlite3.connect(config.DB_PATH, timeout=60)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.execute("PRAGMA wal_autocheckpoint=0")
        self.n = 0

    def bars(self, items):
        if not items:
            return
        payload = []
        for code, bars in items:
            if not bars:
                continue
            payload.append((code, bars[0][0], bars[-1][0], len(bars), pack_bars(bars)))
        if not payload:
            return
        self.c.executemany(
            "INSERT INTO bars(code,first_date,last_date,n,data) VALUES(?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET first_date=excluded.first_date, last_date=excluded.last_date, "
            "n=excluded.n, data=excluded.data", payload)
        self.c.commit()
        self.n += len(payload)

    def tech(self, items):
        if not items:
            return
        import datetime as dt
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = [(code, date, n, json.dumps(data, ensure_ascii=False), now)
                   for code, date, n, data in items]
        self.c.executemany(
            "INSERT INTO tech(code,date,bars,data,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET date=excluded.date, bars=excluded.bars, "
            "data=excluded.data, updated_at=excluded.updated_at", payload)
        self.c.commit()

    def checkpoint(self):
        try:
            self.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

    def close(self):
        try:
            self.checkpoint()
        finally:
            self.c.close()


def pack_bars(bars):
    out = bytearray()
    for b in bars:
        try:
            d = int(str(b[0]).replace("-", ""))
        except ValueError:
            continue
        amount = b[6] or 0.0
        out += _BAR.pack(
            d, float(b[1]), float(b[2]), float(b[3]), float(b[4]),
            float(b[5] or 0.0), float(amount) / 1e8, float(b[7] or 0.0), float(b[8] or 0.0))
    return bytes(out)


def unpack_bars(blob):
    out = []
    if not blob:
        return out
    for rec in _BAR.iter_unpack(blob):
        d = rec[0]
        out.append({
            "date": "%04d-%02d-%02d" % (d // 10000, d // 100 % 100, d % 100),
            "open": rec[1], "high": rec[2], "low": rec[3], "close": rec[4],
            "volume": rec[5], "amount": rec[6] * 1e8, "turnover": rec[7], "pct": rec[8],
        })
    return out


# ---------------- 股票基础信息 ----------------

def upsert_stocks(rows):
    import datetime as dt
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = []
    for r in rows:
        payload.append((r["code"], r.get("name") or "", r.get("market"), r.get("board"),
                        r.get("is_st"), r.get("list_date") or "", r.get("industry") or "",
                        json.dumps(r, ensure_ascii=False), now))
    executemany(
        "INSERT INTO stocks(code,name,market,board,is_st,list_date,industry,data,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
        "name=excluded.name, market=excluded.market, board=excluded.board, is_st=excluded.is_st, "
        "list_date=excluded.list_date, industry=excluded.industry, data=excluded.data, updated_at=excluded.updated_at",
        payload)


def replace_spot(rows, ts):
    payload = [(r["code"], json.dumps(r, ensure_ascii=False), ts) for r in rows]
    with conn() as c:
        c.execute("DELETE FROM spot")
        c.executemany("INSERT INTO spot(code,data,ts) VALUES(?,?,?)", payload)
        c.commit()


def load_spot():
    with conn() as c:
        rows = c.execute("SELECT code,data,ts FROM spot").fetchall()
        ts = c.execute("SELECT MAX(ts) FROM spot").fetchone()[0]
    out = {}
    for r in rows:
        try:
            out[r["code"]] = json.loads(r["data"])
        except Exception:
            continue
    return out, ts


def load_stocks():
    with conn() as c:
        rows = c.execute("SELECT code,name,market,board,is_st,list_date,industry,data FROM stocks").fetchall()
    out = {}
    for r in rows:
        try:
            d = json.loads(r["data"])
        except Exception:
            d = {}
        d.update({"code": r["code"], "name": r["name"], "market": r["market"], "board": r["board"],
                  "is_st": bool(r["is_st"]), "list_date": r["list_date"], "industry": r["industry"]})
        out[r["code"]] = d
    return out


# ---------------- 日线 ----------------

def get_bars(code, start=None, end=None, limit=None):
    row = query("SELECT data FROM bars WHERE code=?", (code,), one=True)
    bars = unpack_bars(row["data"]) if row else []
    if start:
        bars = [b for b in bars if b["date"] >= start]
    if end:
        bars = [b for b in bars if b["date"] <= end]
    if limit:
        bars = bars[-int(limit):]
    return bars


def get_bars_many(codes, start=None):
    out = {c: [] for c in codes}
    codes = list(codes)
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        ph = ",".join("?" * len(chunk))
        rows = query("SELECT code,data FROM bars WHERE code IN (%s)" % ph, tuple(chunk))
        for r in rows:
            bars = unpack_bars(r["data"])
            if start:
                bars = [b for b in bars if b["date"] >= start]
            out[r["code"]] = bars
    return out


def save_index_bars(secid, bars):
    if not bars:
        return
    executemany(
        "INSERT INTO index_daily(secid,date,close,pct) VALUES(?,?,?,?) "
        "ON CONFLICT(secid,date) DO UPDATE SET close=excluded.close, pct=excluded.pct",
        [(secid, b[0], b[4], b[8]) for b in bars])


def load_tech():
    with conn() as c:
        rows = c.execute("SELECT code,data,bars,date FROM tech").fetchall()
    out = {}
    for r in rows:
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        d["bars"] = r["bars"]
        d["tech_date"] = r["date"]
        out[r["code"]] = d
    return out


def load_tech_codes(codes):
    """只加载指定股票的技术指标，供自选等小列表使用。"""
    out = {}
    codes = list(codes)
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in query("SELECT code,data,bars,date FROM tech WHERE code IN (%s)" % ph, tuple(chunk)):
            try:
                d = json.loads(r["data"])
            except Exception:
                continue
            d["bars"] = r["bars"]
            d["tech_date"] = r["date"]
            out[r["code"]] = d
    return out


def set_industries(mapping):
    with conn() as c:
        c.executemany("UPDATE stocks SET industry=? WHERE code=?",
                      [(name, code) for code, name in mapping.items()])
        c.commit()


def get_index_bars(secid, start=None, end=None):
    sql = "SELECT date,close,pct FROM index_daily WHERE secid=?"
    args = [secid]
    if start:
        sql += " AND date>=?"
        args.append(start)
    if end:
        sql += " AND date<=?"
        args.append(end)
    sql += " ORDER BY date"
    return [dict(r) for r in query(sql, tuple(args))]


# ---------------- 自选 / 元数据 ----------------

def watchlist():
    return [r["code"] for r in query("SELECT code FROM watchlist ORDER BY added_at DESC")]


def add_watch(code):
    import datetime as dt
    execute("INSERT OR IGNORE INTO watchlist(code,added_at) VALUES(?,?)",
            (code, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def remove_watch(code):
    execute("DELETE FROM watchlist WHERE code=?", (code,))


def meta_get(key, default=None):
    row = query("SELECT value FROM meta WHERE key=?", (key,), one=True)
    return row["value"] if row else default


def meta_set(key, value):
    execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))


def stats():
    with conn() as c:
        stocks = c.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        agg = c.execute("SELECT COUNT(*), SUM(n), MIN(first_date), MAX(last_date) FROM bars").fetchone()
        techs = c.execute("SELECT COUNT(*) FROM tech").fetchone()[0]
    return {
        "stocks": stocks,
        "bar_rows": agg[1] or 0,
        "bar_codes": agg[0] or 0,
        "bar_min_date": agg[2],
        "bar_max_date": agg[3],
        "tech_codes": techs,
        "spot_ts": meta_get("spot_ts") or "",
        "last_sync": meta_get("last_sync") or "",
        "sync_years": meta_get("sync_years") or "",
    }


def vacuum():
    with conn() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("VACUUM")
        c.commit()
