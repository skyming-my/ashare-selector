# -*- coding: utf-8 -*-
"""首次启动时从仓库内置的压缩分卷还原行情数据库。"""
import glob
import lzma
import os
import shutil

from . import config


def _sources():
    single = os.path.join(config.DATA_DIR, "ashare.db.xz")
    parts = sorted(glob.glob(os.path.join(config.DATA_DIR, "ashare.db.xz.part*")))
    return single, parts


def ensure_database():
    """数据库不存在时，用 data/ashare.db.xz.part* 还原。返回是否执行了还原。"""
    db = config.DB_PATH
    try:
        if os.path.exists(db) and os.path.getsize(db) > 1024 * 1024:
            return False
    except OSError:
        pass
    single, parts = _sources()
    if not parts and not os.path.exists(single):
        return False

    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp_xz = db + ".restore.xz"
    tmp_db = db + ".restore"
    print("首次启动：正在从内置压缩包还原行情数据库（约 190MB，首次约需 20 秒）…", flush=True)
    try:
        if parts:
            with open(tmp_xz, "wb") as out:
                for p in parts:
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, out, 1024 * 1024)
            src = tmp_xz
        else:
            src = single
        with lzma.open(src, "rb") as f, open(tmp_db, "wb") as out:
            shutil.copyfileobj(f, out, 1024 * 1024)
        os.replace(tmp_db, db)
        print("数据库还原完成：%s" % db, flush=True)
        return True
    finally:
        for p in (tmp_xz, tmp_db):
            try:
                os.remove(p)
            except OSError:
                pass
