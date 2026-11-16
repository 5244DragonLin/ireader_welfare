#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把掌阅 App 从冷启动带到福利页（模拟器 adb，无 root）。

2026-11-16 实测跑通的完整流程（详见 docs/掌阅逆向笔记.md 16.10.8）：
  清数据 → 启动 → 点「同意并继续」→ 点「跳过」阅读偏好 → deeplink 开福利页

坑位备忘：
  1. 清过数据后必然重弹「隐私保护说明」，App 看起来像卡在闪屏，
     实际是在等这个弹窗（日志到 "Displayed WelcomeActivity" 就断）
  2. uiautomator 的 bounds 必须实拿：正文文本和按钮都有 bounds，
     要挑 clickable=true 或 text 精确匹配的那个
  3. Git Bash 会把 /sdcard/x.xml 转成 Windows 路径，所有 adb shell
     路径参数都要加 MSYS_NO_PATHCONV=1（本脚本用 os.environ 注入）
  4. 福利页的弹窗（如「签到成功 翻牌领奖」）会挡住下层，先点 ✕ 关掉

用法：
    python src/app_bootstrap.py --check     # 只看当前状态
    python src/app_bootstrap.py --run       # 一路带到福利页
    python src/app_bootstrap.py --run --no-clear   # 不清数据
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
PKG = "com.chaozh.iReaderFree"
WELFARE_URL = "https://ah2.zhangyue.com/activity/welfareIreader.html"

RE_ACTIVITY = re.compile(r"topResumedActivity=ActivityRecord\{\S+ u0 ([^ \t]+)")
RE_NODE = re.compile(r"<node[^>]*>")


def sh(args, timeout=60):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    try:
        p = subprocess.run([ADB, "-s", SERIAL] + args, capture_output=True,
                           timeout=timeout, encoding="utf-8", errors="ignore",
                           env=env)
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return -1, "", f"找不到 adb: {ADB}"
    except subprocess.TimeoutExpired:
        return -2, "", "adb 超时"


def top_activity():
    _, out, _ = sh(["shell", "dumpsys", "activity", "activities"])
    m = RE_ACTIVITY.search(out)
    return m.group(1) if m else ""


def dump_ui(retries=3, gap=2.0):
    for _ in range(retries):
        code, out, err = sh(["shell", "uiautomator", "dump", "/sdcard/ui.xml"], 30)
        if "dumped" in (out + err).lower() or code == 0:
            _, xml, _ = sh(["shell", "cat", "/sdcard/ui.xml"], 30)
            if "<hierarchy" in xml:
                return xml
        time.sleep(gap)
    return ""


def nodes(xml):
    out = []
    for m in RE_NODE.finditer(xml):
        tag = m.group(0)
        t = re.search(r'text="([^"]*)"', tag)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        c = re.search(r'clickable="(\w+)"', tag)
        if not b:
            continue
        bounds = tuple(map(int, b.groups()))
        out.append({
            "text": t.group(1) if t else "",
            "bounds": bounds,
            "clickable": (c.group(1) == "true") if c else False,
            "visible": bounds != (0, 0, 0, 0),
        })
    return out


def find_text(xml, text, need_clickable=False):
    """按文本精确定位可见节点。"""
    for n in nodes(xml):
        if n["text"] == text and n["visible"] and (n["clickable"] or not need_clickable):
            return n
    return None


def tap_bounds(b):
    x = (b[0] + b[2]) // 2
    y = (b[1] + b[3]) // 2
    sh(["shell", "input", "tap", str(x), str(y)])
    return x, y


def tap_text(text, wait=3.0, need_clickable=False):
    xml = dump_ui()
    if not xml:
        return False
    n = find_text(xml, text, need_clickable)
    if not n:
        return False
    x, y = tap_bounds(n["bounds"])
    print(f"    点「{text}」({x},{y})")
    time.sleep(wait)
    return True


def dismiss_popup():
    """关掉可能挡住的弹窗（找 ✕ / 关闭 / 我知道了）。"""
    xml = dump_ui(retries=1, gap=1)
    if not xml:
        return False
    for label in ("✕", "×", "关闭", "我知道了", "取消"):
        n = find_text(xml, label)
        if n and n["visible"]:
            x, y = tap_bounds(n["bounds"])
            print(f"    关弹窗「{label}」({x},{y})")
            time.sleep(2)
            return True
    return False


def open_welfare():
    deeplink = "ireader://com.chaozh.iReaderFree/openurl?url=" + WELFARE_URL
    sh(["shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", deeplink, PKG])
    for _ in range(12):
        time.sleep(3)
        if top_activity().endswith("ActivityOnline"):
            print("    ✅ 福利页已打开")
            time.sleep(2)
            return True
    print(f"    ❌ 未到福利页，当前: {top_activity() or '?'}")
    return False


def bootstrap(clear=True):
    print("—— 检查设备 ——")
    code, out, _ = sh(["get-state"])
    if code != 0:
        print(f"  ❌ 设备 {SERIAL} 不在线")
        return 1
    print(f"  ✅ {SERIAL}  当前前台: {top_activity() or '?'}")

    if clear:
        print("\n—— 清 App 数据（会重弹隐私窗）——")
        sh(["shell", "pm", "clear", PKG], 60)
        time.sleep(2)

    print("\n—— 启动 App ——")
    sh(["shell", "am", "start", "-n",
        f"{PKG}/com.chaozh.iReader.ui.activity.WelcomeActivity"])
    time.sleep(25)

    print("\n—— 处理启动引导 ——")
    for _ in range(6):
        act = top_activity()
        xml = dump_ui(retries=1, gap=1)
        if not xml:
            time.sleep(5)
            continue

        # 1) 隐私政策
        if find_text(xml, "同意并继续", need_clickable=False):
            tap_text("同意并继续", wait=12)
            continue
        # 2) 阅读偏好
        if find_text(xml, "跳过"):
            tap_text("跳过", wait=12)
            continue
        # 3) 已到书架
        if act.endswith("ActivityBookShelf") or act.endswith("ActivityMain"):
            print(f"    ✅ 已进入主界面 ({act.split('.')[-1]})")
            break
        # 4) 可能是弹窗
        if dismiss_popup():
            continue
        time.sleep(5)

    print("\n—— 打开福利页 ——")
    if not open_welfare():
        return 1

    # 福利页可能有签到弹窗，关一次
    time.sleep(3)
    dismiss_popup()
    return 0


def check():
    print("—— 状态检查 ——")
    code, out, _ = sh(["get-state"])
    print(f"  adb: {'在线' if code == 0 else '离线'}")
    print(f"  前台: {top_activity() or '?'}")
    xml = dump_ui(retries=1, gap=1)
    if xml:
        texts = [n["text"] for n in nodes(xml) if n["text"] and n["visible"]]
        print(f"  可见文本({len(texts)}): {texts[:12]}")
        for kw in ("观看视频，送奖励", "剩余", "同意并继续", "跳过"):
            hit = [t for t in texts if kw in t]
            if hit:
                print(f"    [!] 含「{kw}」: {hit[:2]}")
    return 0


def main():
    global ADB, SERIAL
    ap = argparse.ArgumentParser(description="把掌阅 App 带到福利页")
    ap.add_argument("--adb", default=ADB)
    ap.add_argument("--serial", default=SERIAL)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--no-clear", action="store_true", help="不清 App 数据")
    a = ap.parse_args()
    ADB, SERIAL = a.adb, a.serial
    if a.check:
        return check()
    if a.run:
        return bootstrap(clear=not a.no_clear)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
