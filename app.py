# -*- coding: utf-8 -*-
"""A股智能选股 —— 启动入口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ashare import server

if __name__ == "__main__":
    server.serve(open_browser="--no-browser" not in sys.argv)
