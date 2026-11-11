#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PC 网页版阅读时长上报（纯 HTTP，不需要手机）。

为什么能走这条路
----------------
掌阅 PC 网页版（https://pc.ireader.com）的阅读器是 Nuxt SPA，
它每 60 秒向 `/bookstore/open/read/time` POST 一次阅读区间：

    {"book_id": "...", "local_bid": "", "start_time": <ms>, "end_time": <ms>,
     "res": "R", "time_stamp": <ms>, "date": <YYYYMMDD>,
     "switch": 0|1, "app_id": "zye5b814", "user": "<usr>"}

`switch` 取 0 是「读的过程中定时上报」，取 1 是「结束上报」。
这个接口挂在 api-pc.zhangyue.com，**不要求 RSA 签名**，只校验 zysid，
字段里也没有手机端那套 BASE64 分钟位图。

所以只要拿到 usr + zysid，就能用纯 Python 复现这条上报，
不需要 root、不需要运行时 Hook、不需要 adb 挂机。

与服务端数据的关系（2026-11-10 实测）
------------------------------------
这个接口**就是**福利中心「阅读赚代金券」10 档的计数来源。当天实测：
报完分钟数后 `current` 立即跟随，10 档全部达标、505 代金券当日到账。

服务端按「分钟」去重：同一时段重复上报只返回 code:0 而 current 不变。
所以用 output/read_pc_state.json 记录当天已报时段，避免白报。

注意它**不驱动**日常任务「阅读品质好书满5分钟」「收听品质好书满10分钟」，
那两条是独立的计数器（实测阅读档位满 180 分钟时它们仍是 0）。

用法
----
不需要单独运行——`main.py run` 会在领奖前自动调用本模块（先报时长再领奖）。
单独调时长用 `python src/main.py run --read-pc-minutes N`，跳过用 `--no-read-pc`。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

from .http import DESKTOP_UA, post_json

# PC 网页版的 API 网关（与 App 的 ah2.zhangyue.com 不是同一套）
PC_API = "https://api-pc.zhangyue.com"
REPORT_PATH = "/bookstore/open/read/time"

# 从 pc.ireader.com 的 Nuxt 运行时配置里抄出来的固定值
APP_ID = "zye5b814"
CHANNEL_ID = "104583"
P3 = "101010010"      # PC 端自带的值，与 App 的 18070403 不同
P4 = "501603"
P33 = "com.chaozh.iReaderFree"

SWITCH_DURING = 0     # 阅读过程中定时上报
SWITCH_END = 1        # 结束上报

# 上报用的默认书籍（真实存在的云端书，服务端对 book_id 几乎不校验）
DEFAULT_BOOK_ID = "10121980"

STATE_FILE = os.path.join("output", "read_pc_state.json")


def pc_query(usr: str, zysid: str) -> str:
    """拼 PC 网页版每个请求都带的公共参数。"""
    q = {
        "usr": usr,
        "zysid": zysid,
        "p29": APP_ID,
        "p2": CHANNEL_ID,
        "p3": P3,
        "p4": P4,
        "p33": P33,
    }
    return urllib.parse.urlencode(q)


def report_once(usr: str, zysid: str, book_id: str,
                start_ms: int, end_ms: int, switch: int = SWITCH_END) -> dict:
    """上报一段阅读区间。返回服务端 JSON。"""
    now = int(time.time() * 1000)
    body = {
        "book_id": str(book_id),
        # 本地书才填 local_bid，云端书留空（客户端对 64 位 bookId 才填）
        "local_bid": "",
        "start_time": int(start_ms),
        "end_time": int(end_ms),
        "res": "R",
        "time_stamp": now,
        "date": int(time.strftime("%Y%m%d")),
        "switch": int(switch),
        "app_id": APP_ID,
        "user": usr,
    }
    url = f"{PC_API}{REPORT_PATH}?{pc_query(usr, zysid)}"
    return post_json(url, body, headers={
        "Origin": "https://pc.ireader.com",
        "Referer": "https://pc.ireader.com/",
        "User-Agent": DESKTOP_UA,
    })


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    d = os.path.dirname(STATE_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _today_ranges(state: dict) -> list:
    """取今天的已上报区间，跨天自动清零。"""
    today = time.strftime("%Y%m%d")
    if state.get("date") != today:
        return []
    return [tuple(x) for x in state.get("ranges") or []]


def plan_windows(minutes: float, step: int, used: list) -> list:
    """规划出若干「今天之内、互不重叠、不落在未来」的时间窗。

    为什么必须去重：实测服务端按「分钟」去重，同一天里重复上报同一段
    只会返回 code:0 而 current 不变（6 分钟只计了 4 分钟就是这个原因）。
    所以要把窗口铺在还没用过的时段上。
    """
    now_ms = int(time.time() * 1000)
    lt = time.localtime()
    day_start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)
    step_ms = int(step * 60 * 1000)
    need = int(minutes * 60 * 1000)

    busy = sorted(used)
    windows = []
    # 从「当前时刻往前」倒着找空闲段，这样不会碰到未来时间
    cursor = now_ms
    while need > 0 and cursor - step_ms >= day_start:
        start = cursor - min(step_ms, need)
        # 标准区间相交：start < 已有结束 且 cursor > 已有开始
        hit = any(start < b_end and cursor > b_start for b_start, b_end in busy)
        if not hit:
            windows.append((start, cursor))
            busy.append((start, cursor))
            busy.sort()
            need -= cursor - start
        cursor = start
    return windows


def report_minutes(usr: str, zysid: str, book_id: str, minutes: float,
                   step: int = 5, interval: float = 0.0, verbose: bool = True) -> list:
    """把 `minutes` 分钟铺到今天还没用过的时段上报。

    服务端只按分钟去重，不限制单次区间长度（实测一次报 142 分钟也照收），
    但仍然按 step 切分，节奏更接近真实阅读器的 60 秒心跳。
    """
    state = _load_state()
    used = _today_ranges(state)
    if verbose and used:
        print(f"  今天已上报 {len(used)} 段，将跳过这些时段")

    windows = plan_windows(minutes, step, used)
    if not windows:
        if verbose:
            print("  今天没有可用的新时段了（都已上报过，或当前时刻距零点不足）")
        return []

    out = []
    for s, e in windows:
        r = report_once(usr, zysid, book_id, s, e, SWITCH_END)
        ok = r.get("code") == 0
        out.append({"from": s, "to": e, "code": r.get("code"), "msg": r.get("msg"), "ok": ok})
        if verbose:
            mark = "OK " if ok else "失败"
            print(f"  {mark} 上报 {(e - s) // 60000:>3} 分钟  "
                  f"({time.strftime('%H:%M', time.localtime(s / 1000))}"
                  f"→{time.strftime('%H:%M', time.localtime(e / 1000))})"
                  f"  {r.get('msg') or ''}")
        if not ok:
            break
        used.append((s, e))
        if interval:
            time.sleep(interval)

    _save_state({"date": time.strftime("%Y%m%d"),
                 "ranges": [[s, e] for s, e in used]})
    return out
