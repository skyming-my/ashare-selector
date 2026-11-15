# -*- coding: utf-8 -*-
"""涨停判定与连板统计的公共规则。"""

LIMIT_MAIN = 9.8
LIMIT_GROWTH = 19.8
LIMIT_BJ = 29.8
LIMIT_ST = 4.8


def limit_pct(board, is_st=False):
    if is_st:
        return LIMIT_ST
    if board in ("创业板", "科创板"):
        return LIMIT_GROWTH
    if board == "北交所":
        return LIMIT_BJ
    return LIMIT_MAIN


def label(board, is_st=False):
    if is_st:
        return "ST 5%"
    if board in ("创业板", "科创板"):
        return "20cm"
    if board == "北交所":
        return "30cm"
    return "10cm"


def is_limit_up(pct, board, is_st=False):
    if pct is None:
        return False
    return pct >= limit_pct(board, is_st) - 0.3


def is_limit_down(pct, board, is_st=False):
    if pct is None:
        return False
    return pct <= -(limit_pct(board, is_st) - 0.3)


def streak(bars, board, is_st=False):
    """截至最后一根 K 线的连续涨停天数。"""
    lim = limit_pct(board, is_st)
    n = 0
    for k in range(len(bars) - 1, -1, -1):
        p = bars[k].get("pct")
        if p is not None and p >= lim - 0.3:
            n += 1
        else:
            break
    return n


def count_recent(bars, board, is_st=False, days=20):
    lim = limit_pct(board, is_st)
    start = max(0, len(bars) - days)
    c = 0
    for k in range(start, len(bars)):
        p = bars[k].get("pct")
        if p is not None and p >= lim - 0.3:
            c += 1
    return c
