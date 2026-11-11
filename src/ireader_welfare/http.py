#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HTTP 基础封装：统一 UA 与 JSON 解析。"""
import json
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Linux; Android 12; zh_cn) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/100.0 Mobile Safari/537.36"
)

# PC 网页版（pc.ireader.com）接口要桌面 UA，用移动 UA 容易被拒
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20


def post_form(url: str, data: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """表单 POST，返回解析后的 JSON（解析失败时放在 _raw）。"""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(txt)
    except ValueError:
        return {"_raw": txt}


def get_json(url: str, headers: dict = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET 请求，返回解析后的 JSON。"""
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(txt)
    except ValueError:
        return {"_raw": txt}


def post_json(url: str, data: dict, headers: dict = None,
              timeout: int = DEFAULT_TIMEOUT) -> dict:
    """JSON POST，返回解析后的 JSON。

    PC 网页版接口（api-pc.zhangyue.com）用这种形式，
    与 App 侧的表单 POST + RSA 签名不同。
    """
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    h = {
        "User-Agent": DESKTOP_UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(txt)
    except ValueError:
        return {"_raw": txt}