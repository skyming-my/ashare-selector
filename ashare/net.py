# -*- coding: utf-8 -*-
"""网络层：仅使用标准库。

东方财富接口在部分网络环境下 IPv6 会直接断连，这里统一强制 IPv4；
接口偶发 RemoteDisconnected，所有请求带指数退避重试。
"""
import gzip
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://quote.eastmoney.com/",
}

_orig_getaddrinfo = socket.getaddrinfo
_patched = False

# 东财部分节点会直接断开 OpenSSL 的 TLS1.3 握手，强制 TLS1.2 可稳定连接
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.maximum_version = ssl.TLSVersion.TLSv1_2


def force_ipv4():
    """让所有 DNS 解析只返回 IPv4 地址。"""
    global _patched
    if _patched:
        return
    def _ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return [x for x in _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                if x[0] == socket.AF_INET]
    socket.getaddrinfo = _ipv4
    _patched = True


_HOST_ALIASES = {
    "push2.eastmoney.com": ["push2.eastmoney.com", "82.push2.eastmoney.com", "23.push2.eastmoney.com"],
    "push2his.eastmoney.com": ["push2his.eastmoney.com", "push2his.eastmoney.com"],
}


def _rotate(url, attempt):
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    aliases = _HOST_ALIASES.get(p.hostname or "")
    if not aliases:
        return url
    host = aliases[attempt % len(aliases)]
    return urllib.parse.urlunsplit((p.scheme, host + ((":" + str(p.port)) if p.port else ""),
                                    p.path, p.query, p.fragment))


def fetch(url, timeout=20, retries=5, encoding="utf-8", headers=None):
    """抓取 URL，返回 bytes，失败自动重试。"""
    force_ipv4()
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries):
        target = _rotate(url, attempt)
        try:
            req = urllib.request.Request(target, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError,
                socket.timeout, OSError, ValueError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(0.3 * (attempt + 1) + random.random() * 0.25)
    raise RuntimeError("请求失败(%d次): %s | %s" % (retries, url[:120], last))


def get_json(url, timeout=20, retries=5):
    raw = fetch(url, timeout=timeout, retries=retries)
    return json.loads(raw.decode("utf-8", "replace"))
