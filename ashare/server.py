# -*- coding: utf-8 -*-
"""HTTP 服务：标准库实现的本地 Web 应用。"""
import json
import mimetypes
import os
import posixpath
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, config, store, updater

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AShareSelector/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- 基础响应 ----------
    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status=200):
        self._send(status, _json_bytes(obj))

    def _error(self, message, status=400):
        self._json({"ok": False, "error": str(message)}, status)

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api_get(path, query)
            else:
                self._static(path)
        except Exception as exc:
            traceback.print_exc()
            self._error(exc, 500)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        try:
            if not path.startswith("/api/"):
                self._error("not found", 404)
                return
            self._api_post(path, payload)
        except Exception as exc:
            traceback.print_exc()
            self._error(exc, 500)

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path.startswith("/api/watchlist/"):
                code = path.rsplit("/", 1)[-1]
                store.remove_watch(code)
                self._json({"ok": True})
            else:
                self._error("not found", 404)
        except Exception as exc:
            self._error(exc, 500)

    # ---------- API ----------
    def _api_get(self, path, q):
        def arg(name, default=""):
            return (q.get(name) or [default])[0]

        if path == "/api/health":
            self._json({"ok": True, "time": store.meta_get("last_sync") or ""})
        elif path == "/api/bootstrap":
            self._json(api.bootstrap())
        elif path == "/api/market/overview":
            self._json(api.market_overview())
        elif path == "/api/stocks":
            self._json(api.stock_list(
                q=arg("q"), board=arg("board"), page=int(arg("page", "1") or 1),
                size=int(arg("size", "20") or 20), sort_by=arg("sort", "pct"),
                order=arg("order", "desc"), exclude_st=arg("exclude_st", "0") == "1"))
        elif path == "/api/search":
            self._json({"rows": api.search(arg("q"))})
        elif path.startswith("/api/stock/"):
            self._json(api.stock_detail(path.rsplit("/", 1)[-1]))
        elif path == "/api/watchlist":
            self._json(api.watchlist_payload())
        elif path == "/api/quote":
            codes = [c for c in arg("codes", "").split(",") if c]
            self._json(api.live_quotes(codes))
        elif path == "/api/limitup":
            self._json(api.limitup_payload(arg("type", "up")))
        elif path == "/api/recommend":
            try:
                lim = int(arg("limit", "12") or 12)
            except ValueError:
                lim = 12
            self._json(api.recommend_payload(lim))
        elif path == "/api/sync/status":
            self._json(updater.status())
        elif path == "/api/index":
            self._json({"indices": api._index_cards()})
        else:
            self._error("未知接口: %s" % path, 404)

    def _api_post(self, path, payload):
        if path == "/api/screen":
            self._json(api.do_screen(payload))
        elif path == "/api/backtest":
            self._json(api.do_backtest(payload))
        elif path == "/api/sync":
            started = updater.start(payload.get("years"), payload.get("mode") or "auto")
            self._json({"ok": True, "started": started, "status": updater.status()})
        elif path == "/api/spot/refresh":
            started = updater.start_spot_refresh()
            self._json({"ok": True, "started": started})
        elif path == "/api/db/vacuum":
            store.vacuum()
            self._json({"ok": True})
        elif path == "/api/watchlist":
            code = (payload or {}).get("code")
            if not code:
                self._error("缺少代码")
                return
            store.add_watch(code)
            self._json({"ok": True})
        else:
            self._error("未知接口: %s" % path, 404)

    # ---------- 静态文件 ----------
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = posixpath.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith("..") or ":" in rel:
            self._error("forbidden", 403)
            return
        full = os.path.join(config.WEB_DIR, *rel.split("/"))
        if not os.path.isfile(full):
            self._error("not found", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, ctype)


def _pick_port(start):
    import socket
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((config.HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有可用端口")


def serve(open_browser=True):
    store.init()
    port = _pick_port(config.PORT)
    httpd = ThreadingHTTPServer((config.HOST, port), Handler)
    httpd.daemon_threads = True
    url = "http://%s:%d" % (config.HOST, port)
    print("=" * 56)
    print("  A股智能选股  已启动")
    print("  访问地址: %s" % url)
    print("  数据目录: %s" % config.DATA_DIR)
    print("  关闭窗口或按 Ctrl+C 退出")
    print("=" * 56)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
