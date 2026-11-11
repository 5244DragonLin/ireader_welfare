#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真机 UI 自动化：在 App 内点一次真实的「分享今日日签」。

为什么需要它
------------
「分享今日日签」（+20 代金券）的完成动作在 App 原生层：
H5 只调一句 `downloadImage()` 让原生弹系统分享面板，
分享成功后回调仅 `updateTaskList`（重拉列表），**全程没有 HTTP 上报**。

我实测过所有像上报的接口（`/user/scomm/share_report`、`/welfare/operation/callback`、
`/zycl/welfare/task/finish` …），全部是幂等空壳，任务 `status` 一律不变。
所以纯协议层做不到，必须让 App **真的分享一次**。

本脚本用 adb 驱动真实设备完成这件事——**不 root、不 hook、不伪造**：
    ① 用 deeplink 打开福利中心
    ② uiautomator dump 拿界面元素树，找到「分享今日日签」按钮并点击
    ③ 等系统分享面板弹出，选中一个目标（默认第一个可用项）发出
    ④ 回到福利中心，确认任务状态是否已变为「可领取」
    ⑤ 提示你用主项目的 `tasks` 命令把 20 代金券领掉

⚠️ 运行前提（缺一不可）
  1. 一台 Android 手机，已开「开发者选项 → USB 调试」
  2. USB 连接本机（或先 adb connect 走无线），`adb devices` 能看到 device
  3. 手机上已装掌阅 App 且**已登录**
  4. 屏幕解锁、亮屏
  5. **手机侧需登录一个真实的分享目标**（微信 / QQ / 微博任一），
     否则分享面板里找不到可发出的应用

用法
----
  python src/share_daily.py --check          # 只检查环境与当前任务状态
  python src/share_daily.py --run            # 打开福利中心 → 点分享 → 走面板
  python src/share_daily.py --run --target 微信    # 指定分享到哪个 App
  python src/share_daily.py --run --dry      # 只点到弹面板为止，不真发

合规提示
--------
自动化操作可能违反掌阅用户协议，仅建议用于自己账号的便利性操作。
每日一次即可，不要高频调用。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

ADB = os.environ.get("ADB_PATH", r"D:\platform-tools\adb.exe")
PKG = "com.chaozh.iReaderFree"
WELFARE_ACTIVITY = "com.chaozh.iReader.ui.activity.WelcomeActivity"

# 福利中心 H5 页（App 内 webview 打开它即是福利中心）
WELFARE_URL = "https://ah2.zhangyue.com/activity/welfareIreader.html?ca=Welfare.Index"

# 点分享按钮时，命中这些文案之一即认为是日签分享入口
SHARE_BTN_KEYS = ("分享今日日签", "分享日签", "立即分享", "分享")

# 分享面板里，这些是要跳过的（避免发给「保存到相册 / 复制链接」之类）
SHARE_SKIP_KEYS = ("保存", "下载", "复制", "收藏", "更多", "取消", "关闭", "蓝牙", "邮件")


# --------------------------------------------------------------------------
# adb 基础
# --------------------------------------------------------------------------
def sh(args: list[str], timeout: int = 60, check: bool = False):
    """跑一条 adb 命令，返回 (code, stdout, stderr)。"""
    try:
        p = subprocess.run([ADB] + args, capture_output=True, timeout=timeout,
                           encoding="utf-8", errors="ignore")
        if check and p.returncode != 0:
            print(f"  adb 失败: {' '.join(args)}")
            print(f"    {(p.stderr or '').strip()[:200]}")
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return -1, "", f"找不到 adb：{ADB}（用 --adb 或环境变量 ADB_PATH 指定）"
    except subprocess.TimeoutExpired:
        return -2, "", "adb 命令超时"


def check_env() -> bool:
    """检查 adb / 设备 / App / 屏幕是否就绪。"""
    print("—— 环境检查 ——")
    if not os.path.exists(ADB):
        print(f"  ❌ 找不到 adb：{ADB}")
        print("     请确认 platform-tools 路径，或用 --adb 指定")
        return False
    print(f"  ✅ adb: {ADB}")

    code, out, _ = sh(["devices", "-l"])
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    devs = [l for l in lines[1:] if "device" in l and "unauthorized" not in l]
    if not devs:
        print("  ❌ 没有可用的设备")
        print("     请 USB 连手机 → 开启「USB 调试」→ 手机上点「允许调试」")
        print(f"     当前 adb 输出: {lines[1:] if len(lines) > 1 else '(空)'}")
        return False
    print(f"  ✅ 设备: {devs[0]}")

    code, out, _ = sh(["shell", "pm", "list", "packages", PKG])
    if PKG not in out:
        print(f"  ❌ 未安装 {PKG}")
        return False
    print(f"  ✅ 已安装 {PKG}")

    code, out, _ = sh(["shell", "settings", "get", "system", "screen_off_timeout"])
    print(f"  屏幕超时: {out.strip()} ms（建议临时调大，避免操作中息屏）")

    print("  ✅ 环境就绪")
    return True


def wake_unlock():
    """点亮屏幕并尝试上滑解锁（有密码锁需手动先解开）。"""
    sh(["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    time.sleep(0.5)
    sh(["shell", "input", "swipe", "540", "1800", "540", "600", "200"])
    time.sleep(1.0)


# --------------------------------------------------------------------------
# 界面解析
# --------------------------------------------------------------------------
def dump_ui() -> str:
    """抓当前界面的 uiautomator XML。"""
    sh(["shell", "uiautomator", "dump", "/sdcard/ui.xml"], timeout=30)
    code, out, _ = sh(["shell", "cat", "/sdcard/ui.xml"], timeout=30)
    return out


def find_nodes(xml: str) -> list[dict]:
    """把 XML 解析成 [{text, bounds, clickable}]。"""
    out = []
    for m in re.finditer(r"<node[^>]*>", xml):
        tag = m.group(0)
        t = re.search(r'text="([^"]*)"', tag)
        d = re.search(r'content-desc="([^"]*)"', tag)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        c = re.search(r'clickable="(\w+)"', tag)
        if not b:
            continue
        label = (t.group(1) if t else "") or (d.group(1) if d else "")
        out.append({
            "text": label,
            "bounds": (int(b.group(1)), int(b.group(2)), int(b.group(3)), int(b.group(4))),
            "clickable": (c.group(1) == "true") if c else False,
        })
    return out


def tap_bounds(b) -> tuple[int, int]:
    x = (b[0] + b[2]) // 2
    y = (b[1] + b[3]) // 2
    sh(["shell", "input", "tap", str(x), str(y)])
    return x, y


def visible_texts(nodes: list[dict], limit: int = 14) -> list[str]:
    return [n["text"] for n in nodes if n["text"]][:limit]


# --------------------------------------------------------------------------
# 打开福利中心
# --------------------------------------------------------------------------
def open_welfare() -> bool:
    """用 deeplink 打开福利中心页。"""
    print("\n—— 打开福利中心 ——")
    attempts = [
        # 优先：直接交给 App 处理 https 链接（App 注册了该 host 的 view intent）
        ["shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-d", WELFARE_URL, PKG],
        # 备选：ireader scheme
        ["shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-d", "ireader://com.chaozh.iReaderFree/openurl?url=" + WELFARE_URL, PKG],
        # 兜底：只拉起 App 主界面，让你手动进福利中心
        ["shell", "am", "start", "-n", f"{PKG}/{WELFARE_ACTIVITY}"],
    ]
    for a in attempts:
        code, out, err = sh(a)
        blob = (out + err)
        if "Error" not in blob and "Exception" not in blob:
            print(f"  ✅ 已启动（{a[2] if len(a) > 2 else ''}）")
            time.sleep(6)
            return True
        print(f"  ⚠️ 失败: {blob.strip()[:150]}")
    return False


# --------------------------------------------------------------------------
# 核心：点分享 → 走系统面板
# --------------------------------------------------------------------------
def find_share_button(nodes: list[dict]) -> dict | None:
    """在界面里找「分享今日日签」入口。"""
    cands = []
    for n in nodes:
        t = n["text"]
        if not t:
            continue
        for k in SHARE_BTN_KEYS:
            if k in t:
                # 越精确的文案优先级越高；可点击的优先
                cands.append((0 if t == "分享今日日签" else 1, not n["clickable"], n))
                break
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]))
    return cands[0][2]


def locate_share_button(retries: int = 4, scrolls: int = 3,
                        gap: float = 3.0) -> tuple[dict | None, list[str]]:
    """反复找分享入口：页面可能要加载，按钮也可能在下方需要滚动。

    返回 (按钮节点或 None, 最后一次可见文案)。
    """
    last_texts: list[str] = []
    for attempt in range(1, retries + 1):
        xml = dump_ui()
        if xml and "<hierarchy" in xml:
            nodes = find_nodes(xml)
            last_texts = visible_texts(nodes)
            btn = find_share_button(nodes)
            if btn:
                return btn, last_texts
        if attempt < retries:
            print(f"  [{attempt}/{retries}] 暂无入口，{gap:.0f}s 后重试 ...")
            time.sleep(gap)

    # 还没找到：尝试向下滚动再找（日签入口可能在页面下方）
    for s in range(1, scrolls + 1):
        print(f"  向下滚动找入口（{s}/{scrolls}）...")
        sh(["shell", "input", "swipe", "540", "1500", "540", "700", "300"])
        time.sleep(1.5)
        xml = dump_ui()
        if xml and "<hierarchy" in xml:
            nodes = find_nodes(xml)
            last_texts = visible_texts(nodes)
            btn = find_share_button(nodes)
            if btn:
                return btn, last_texts
    return None, last_texts


def find_share_target(nodes: list[dict], prefer: str | None) -> dict | None:
    """在系统分享面板里挑一个发送目标。"""
    cands = []
    for n in nodes:
        t = n["text"]
        if not t:
            continue
        if any(s in t for s in SHARE_SKIP_KEYS):
            continue
        if not n["clickable"]:
            continue
        # 面板项通常是「微信 / 朋友圈 / QQ / 微博 / 复制链接…」
        cands.append((0 if (prefer and prefer in t) else 1, n))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


def do_share(target_prefer: str | None, dry: bool, wait_panel: int = 6) -> str:
    """打开福利中心 → 点分享 → 走面板。返回结果标识。"""
    if not open_welfare():
        return "no-welfare-page"

    print("\n—— 定位「分享今日日签」——")
    btn, texts = locate_share_button()
    if not btn:
        print("  ❌ 界面上始终没找到分享入口")
        print(f"     最后可见文案: {texts}")
        print("     → 可能原因：福利页没加载完 / 未登录 / 今天已完成且入口收起 /")
        print("       入口文案与预期不同（请把上面这行文案发我，好加进关键词）")
        return "no-button"

    print(f"  命中「{btn['text'][:20]}」 {btn['bounds']}")
    tap_bounds(btn["bounds"])

    print(f"  等待系统分享面板（最多 {wait_panel}s）...")
    time.sleep(wait_panel)

    if dry:
        print("  [dry] 已点到弹面板为止，不实际发送")
        return "dry-stop-at-panel"

    # 面板可能带延迟，重试几次抓取
    tgt = None
    texts2: list[str] = []
    for attempt in range(1, 4):
        xml2 = dump_ui()
        if xml2 and "<hierarchy" in xml2:
            nodes2 = find_nodes(xml2)
            texts2 = visible_texts(nodes2, 20)
            tgt = find_share_target(nodes2, target_prefer)
            if tgt:
                break
        if attempt < 3:
            print(f"  [{attempt}/3] 面板还没就绪，2s 后重试 ...")
            time.sleep(2)

    if not tgt:
        print("  ❌ 分享面板里没找到可发送的目标")
        print(f"     面板可见文案: {texts2}")
        print("     → 请确认：手机侧已登录微信 / QQ / 微博任意一个；")
        print("       且 App 弹出的确实是系统分享面板（文案见上）")
        return "no-target"

    print(f"  选中「{tgt['text'][:20]}」 {tgt['bounds']}")
    tap_bounds(tgt["bounds"])
    print("  已发出，等待目标 App 处理 ...")
    time.sleep(6)

    # 退回掌阅（分享跳出去后需要切回来）
    sh(["shell", "am", "start", "-n", f"{PKG}/{WELFARE_ACTIVITY}"])
    time.sleep(4)
    return "sent"


def read_task_status(via_api: bool = True) -> int | None:
    """读「分享今日日签」任务的 status（0 未完成 / 1 可领取 / 2 已领取）。

    默认走主项目的接口（最准）；失败则返回 None，让调用方自行看 App。
    """
    if not via_api:
        return None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from ireader_welfare import auth
        from ireader_welfare.config import load, resolve_credentials
        from ireader_welfare.welfare import WelfareClient

        phone, pwd = resolve_credentials(load(), None, None)
        if not phone or not pwd:
            return None
        cred = auth.login(phone, pwd)
        t = WelfareClient(cred).share_task()
        return t.get("status") if t else None
    except Exception:
        return None


# --------------------------------------------------------------------------
def main():
    global ADB
    ap = argparse.ArgumentParser(
        prog="share-daily",
        description="真机 UI 自动化：在 App 内点一次真实的「分享今日日签」",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python src/share_daily.py --check\n"
            "  python src/share_daily.py --run\n"
            "  python src/share_daily.py --run --target 微信\n"
        ),
    )
    ap.add_argument("--adb", default=ADB, help="adb.exe 路径")
    ap.add_argument("--check", action="store_true", help="只检查环境与当前任务状态")
    ap.add_argument("--run", action="store_true", help="执行：开福利中心 → 点分享 → 走面板")
    ap.add_argument("--target", default=None, help="优先分享到哪个 App（如 微信 / QQ）")
    ap.add_argument("--dry", action="store_true", help="只点到弹面板为止，不实际发送")
    ap.add_argument("--wait", type=int, default=6, help="等待分享面板秒数（默认 6）")
    a = ap.parse_args()

    ADB = a.adb

    # 先看接口侧状态，避免白跑一趟
    st = read_task_status()
    if st is not None:
        label = {0: "未完成", 1: "可领取（已完成）", 2: "已领取"}.get(st, str(st))
        print(f"—— 接口侧任务状态（分享今日日签）: {label}（status={st}）——")
        if st in (1, 2) and not a.check:
            print("\n今天已经完成过，无需再跑。直接领奖即可：")
            print("  python src/main.py tasks")
            return 0

    if not check_env():
        print("\n❌ 环境不满足，无法继续")
        return 1
    if a.check:
        return 0

    if not a.run:
        print("\n提示：加 --run 才会真正操作手机。")
        return 0

    wake_unlock()
    result = do_share(a.target, a.dry, a.wait)
    print(f"\n—— 结果: {result} ——")

    if result == "sent":
        st2 = read_task_status()
        if st2 in (1, 2):
            print("✅ 任务已变为可领取/已领取")
            print("   下一步：python src/main.py tasks   # 把 20 代金券领掉")
        elif st2 == 0:
            print("⚠️ 面板走通了，但任务状态仍是「未完成」。")
            print("   可能原因：分享未真正送达 / 服务端延迟。")
            print("   建议：回手机确认微信/QQ 那边是否真的收到了日签，或稍等 1 分钟再 --check。")
        else:
            print("   已发出分享，请用 --check 或 App 内确认任务状态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())