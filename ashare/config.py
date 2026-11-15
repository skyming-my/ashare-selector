# -*- coding: utf-8 -*-
"""全局配置。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "ashare.db")
WEB_DIR = os.path.join(BASE_DIR, "web")

HOST = os.environ.get("ASHARE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASHARE_PORT", "8765"))

DEFAULT_YEARS = 2
SYNC_WORKERS = 6

# 指数（腾讯证券代码）
INDEXES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
    ("sh000688", "科创50"),
    ("bj899050", "北证50"),
]

BOARDS = ["沪市主板", "深市主板", "创业板", "科创板", "北交所"]
