#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""读 App 侧的真实视频剩余次数（唯一可信来源）。

2026-11-16 决定性发现：
  同一账号同一时刻，App UI 显示「剩余9次机会」，
  而 HTTP /zycl/welfare/video/status 恒返 53002（watchCount=0）。
  → HTTP 接口对非 H5 会话不可信，额度只能从 UI 读。

用法：
    python src/video_quota.py            # 读一次
    python src/video_quota.py --watch 3  # 每 3 秒读一次，观察点击前后的变化
"""
import argparse
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ADB = os.environ.get("ADB_PATH", r"D:\platform-tools\adb.exe")
SERIAL = os.environ.get("IREADER_ADB_SERIAL", "127.0.0.1:16384")

RE_REMAIN = re.compile(r"剩余(\d+)次机会")
RE_USED_UP = re.compile("今日机会已用完")
RE_CARD = "观看视频，送奖励"


def sh(args, timeout=60):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    try:
        p = subprocess.run([ADB, "-s", SERIAL] + args, capture_output=True,
                           timeout=timeout, encoding="utf-8", errors="ignore",
                           env=env)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return -2, "", "超时"


def dump_ui(retries=2, gap=1.5):
    for _ in range(retries):
        code, out, err = sh(["shell", "uiautomator", "dump", "/sdcard/vq.xml"], 30)
        if "dumped" in (out + err).lower() or code == 0:
            _, xml, _ = sh(["shell", "cat", "/sdcard/vq.xml"], 30)
            if "<hierarchy" in xml:
                return xml
        time.sleep(gap)
    return ""


def parse_quota(xml):
    """返回 (剩余次数 or None, 状态串)。"""
    texts = re.findall(r'text="([^"]*)"', xml)
    if any(RE_USED_UP.search(t) for t in texts):
        return 0, "已用完"
    has_card = any(RE_CARD in t for t in texts)
    for t in texts:
        m = RE_REMAIN.search(t)
        if m:
            return int(m.group(1)), "正常"
    if has_card:
        return None, "有卡片但无剩余次数文案（可能不在视野内）"
    return None, "未找到视频卡片（先滚到卡片位置）"


def scroll_to_card(max_scrolls=4):
    """把视频卡片滚进视野。"""
    for i in range(max_scrolls + 1):
        xml = dump_ui(retries=1, gap=1)
        if xml and RE_CARD in xml and 'bounds="[0,0][0,0]"' not in _card_bounds(xml):
            return xml
        sh(["shell", "input", "swipe", "450", "1200", "450", "600", "400"])
        time.sleep(2)
    return dump_ui()


def _card_bounds(xml):
    m = re.search(r'text="' + RE_CARD + r'"[^>]*bounds="([^"]*)"', xml)
    return m.group(1) if m else ""


def read_once(verbose=True):
    xml = scroll_to_card()
    if not xml:
        if verbose:
            print("  ❌ 无法 dump UI（设备离线或不在福利页）")
        return None
    remain, state = parse_quota(xml)
    if verbose:
        if remain is None:
            print(f"  次数: ? ({state})")
        else:
            print(f"  剩余: {remain} 次  ({state})")
    return remain


def main():
    global ADB, SERIAL
    ap = argparse.ArgumentParser(description="读 App 侧视频剩余次数")
    ap.add_argument("--adb", default=ADB)
    ap.add_argument("--serial", default=SERIAL)
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒轮询")
    ap.add_argument("--times", type=int, default=10, help="轮询次数")
    a = ap.parse_args()
    ADB, SERIAL = a.adb, a.serial

    code, _, _ = sh(["get-state"])
    if code != 0:
        print(f"❌ 设备 {SERIAL} 不在线")
        return 1

    if not a.watch:
        read_once()
        return 0

    print(f"每 {a.watch}s 读一次，共 {a.times} 次（Ctrl+C 退出）")
    prev = None
    for i in range(a.times):
        remain = read_once(verbose=False)
        mark = ""
        if prev is not None and remain is not None and remain != prev:
            mark = f"   ← 变化 {prev} → {remain}"
        print(f"  [{i+1:02d}] 剩余 {remain} 次{mark}")
        prev = remain if remain is not None else prev
        time.sleep(a.watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
