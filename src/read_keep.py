#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真机挂机：不用 root、不 hook、不伪造，让客户端自己把阅读时长累计上去。

为什么走这条路
--------------
阅读分钟数由客户端原生阅读器计时后自行上报。没 root 就用不了运行时 Hook，
也就不可能改客户端内存或报文；但只要设备保持在前台并被翻页，
客户端会按正常链路自己上报，服务端没有理由不认。

两个模式
--------
  read    打开一本书，每 N 秒上滑一页（模拟真实阅读）
  listen  不做任何操作，用于验证「听书是否计入阅读档位」——若计入，
          可以息屏整晚播放，是最省事的刷法

脚本只做三件事：保持常亮、定时翻页、定期查一次阅读分钟数并打印。
书在哪里打开由人工完成一次，脚本不猜界面坐标。

用法
----
  python src/read_keep.py --check                       # 只查环境和当前分钟数
  python src/read_keep.py --run --minutes 12 --mode listen   # 验证听书是否计入
  python src/read_keep.py --run --minutes 180           # 挂满 3 小时拿全 505 券

无线 adb（手机扔一边充电，电脑只发指令，不占内存）：
  python src/read_keep.py --tcpip                       # 先 USB 连一次，切到无线后拔线
  python src/read_keep.py --connect 192.168.1.5:5555 --run --minutes 180
  # 手机重启后无线 adb 会失效，重新 --connect 即可；云手机也用 --connect 接入

前提：USB 连接、开启 USB 调试、手机上已登录掌阅、**插着电**（常亮靠 svc power stayon usb）。
不配账号也能挂机，只是看不到分钟数变化。

合规提示
--------
自动化操作可能违反掌阅用户协议，仅在自己账号上低频使用。
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import share_daily as sd
from read_probe import read_current
from ireader_welfare import auth, config
from ireader_welfare.welfare import WelfareClient

PKG = sd.PKG
ADB_DEFAULT = sd.ADB


# --------------------------------------------------------------------------
# 设备操作
# --------------------------------------------------------------------------
def screen_size() -> tuple[int, int]:
    """取屏幕分辨率，翻页坐标按它换算。"""
    _, out, _ = sd.sh(["shell", "wm", "size"])
    m = re.search(r"(\d+)x(\d+)", out or "")
    if not m:
        return 1080, 2400
    return int(m.group(1)), int(m.group(2))


def keep_awake():
    """插电常亮。不需要 root，但要求手机在充电（USB 或电源适配器都算）。"""
    sd.sh(["shell", "svc", "power", "stayon", "true"])


def launch_app():
    sd.sh(["shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1"])


def turn_page(w: int, h: int):
    x = w // 2
    sd.sh(["shell", "input", "swipe", str(x), str(int(h * 0.78)),
           str(x), str(int(h * 0.25)), "300"])


# --------------------------------------------------------------------------
# 无线 adb：手机扔一边充电，电脑只发指令，不占内存
# --------------------------------------------------------------------------
def device_ip() -> str:
    """取设备的局域网地址。"""
    _, out, _ = sd.sh(["shell", "ip", "route"])
    m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", out or "")
    if m:
        return m.group(1)
    _, out, _ = sd.sh(["shell", "ip", "addr", "show", "wlan0"])
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else ""


def switch_to_wifi(port: int = 5555) -> str:
    """USB 连着时切到无线 adb，之后可以拔线。返回连接地址，失败返回空串。"""
    print("—— 切换到无线 adb ——")
    _, out, _ = sd.sh(["devices"])
    if "device" not in (out or ""):
        print("  需要先用 USB 连上手机，切一次无线之后就能拔线")
        return ""
    ip = device_ip()
    if not ip:
        print("  取不到设备 IP，确认手机已连上 WiFi")
        return ""
    sd.sh(["tcpip", str(port)])
    time.sleep(2)
    addr = f"{ip}:{port}"
    _, out, _ = sd.sh(["connect", addr])
    print(f"  adb connect {addr}")
    print(f"  {out.strip()[:120]}")
    print("  现在可以拔掉 USB，手机放一边充电即可")
    return addr


def connect_wifi(addr: str) -> bool:
    """连接已经在无线 adb 模式下的设备（手机重启后需要重连一次）。"""
    print(f"—— 连接 {addr} ——")
    _, out, _ = sd.sh(["connect", addr])
    print(f"  {out.strip()[:120]}")
    return "connected" in (out or "").lower()


def foreground() -> str:
    """当前前台包名/界面，用于确认掌阅还在前台。"""
    _, out, _ = sd.sh(["shell", "dumpsys", "window", "windows"])
    for line in (out or "").splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            return line.strip()
    return ""


def check_env(adb_path: str) -> bool:
    print("—— 环境检查 ——")
    if not os.path.exists(adb_path):
        print(f"  找不到 adb：{adb_path}（用 --adb 或环境变量 ADB_PATH 指定）")
        return False
    print(f"  adb: {adb_path}")

    _, out, _ = sd.sh(["devices", "-l"])
    lines = [l.strip() for l in (out or "").strip().splitlines() if l.strip()]
    devs = [l for l in lines[1:] if "device" in l and "unauthorized" not in l]
    if not devs:
        print("  没有可用设备。USB 连手机 → 开启 USB 调试 → 手机上点「允许调试」，")
        print("  或先用 --tcpip 切无线、之后用 --connect 192.168.1.5:5555 接入")
        print(f"  当前 adb 输出: {lines[1:] if len(lines) > 1 else '(空)'}")
        return False
    print(f"  设备: {devs[0]}")

    _, out, _ = sd.sh(["shell", "pm", "list", "packages", PKG])
    if PKG not in (out or ""):
        print(f"  未安装 {PKG}")
        return False
    print(f"  已安装 {PKG}")

    w, h = screen_size()
    print(f"  分辨率: {w}x{h}")
    _, out, _ = sd.sh(["shell", "dumpsys", "battery"])
    m = re.search(r"AC powered: (\w+)", out or "")
    if m:
        print(f"  插电: {'是' if m.group(1) == 'true' else '否（常亮可能失效，请插电）'}")
    return True


# --------------------------------------------------------------------------
# 分钟数采样
# --------------------------------------------------------------------------
def open_client(cfg_path, phone, password):
    """登录并返回客户端；失败时返回 None（仍可挂机，只是看不到分钟数）。"""
    cfg = config.load(cfg_path)
    phone, password = config.resolve_credentials(cfg, phone, password)
    if not phone or not password:
        print("未配置账号，看不到阅读分钟数（挂机照常进行）")
        return None
    try:
        cred = auth.login(phone, password)
        print(f"登录成功: {cred.nick}  ({cred.usr})")
        return WelfareClient(cred)
    except Exception as e:
        print(f"登录失败，跳过分钟数统计: {e}")
        return None


def sample(client):
    if client is None:
        return None
    old, _ = read_current(client)
    return old


# --------------------------------------------------------------------------
# 挂机
# --------------------------------------------------------------------------
def run(client, minutes: int, mode: str, interval: int, sample_every: int):
    print("\n—— 挂机 ——")
    base = sample(client)
    if base is not None:
        print(f"起始阅读分钟数: {base}")

    keep_awake()
    launch_app()
    time.sleep(3)

    w, h = screen_size()
    if mode == "read":
        print(f"现在请在手机上打开一本书（脚本只负责翻页，不猜界面坐标）")
        print(f"每 {interval} 秒上滑一页，共 {minutes} 分钟")
    else:
        print("听书模式：请在手机上点开听书并播放，脚本不做任何操作")
        print(f"共观察 {minutes} 分钟，用来验证听书是否计入阅读档位")

    if client is None:
        print("（无账号，不打印分钟数）")

    total = minutes * 60
    start = time.time()
    next_page = start + interval
    next_sample = start + sample_every
    last = base

    while True:
        elapsed = int(time.time() - start)
        if elapsed >= total:
            break

        now = time.time()
        if mode == "read" and now >= next_page:
            turn_page(w, h)
            next_page = now + interval

        if now >= next_sample:
            cur = sample(client)
            if cur is not None:
                delta = "" if (base is None or cur is None) else f"  (+{cur - base})"
                print(f"  {elapsed // 60:>3} 分钟  已读 {cur} 分钟{delta}")
                last = cur
            else:
                print(f"  {elapsed // 60:>3} 分钟  （未统计）")
            next_sample = now + sample_every

        time.sleep(5)

    print("\n—— 结果 ——")
    end = sample(client)
    if base is not None and end is not None:
        got = end - base
        print(f"  起始 {base} 分钟 → 结束 {end} 分钟，本次增加 {got} 分钟")
        print(f"  实际挂机 {minutes} 分钟，转化率 {got / minutes:.0%}")
        if got == 0:
            print("  一分钟都没涨：检查掌阅是否仍在前台、书是否真的打开着")
        elif mode == "listen" and got > 0:
            print("  听书计入阅读时长，后续可用息屏播放，不必翻页")
    else:
        print("  未配置账号，无法给出分钟数对比")

    if end is not None:
        print("\n  达到档位后领奖：python src/main.py read")


def main():
    ap = argparse.ArgumentParser(description="真机挂机刷阅读时长（不用 root）")
    ap.add_argument("--phone", help="手机号，默认读环境变量 / config.yaml")
    ap.add_argument("--password", help="密码")
    ap.add_argument("--config", help="配置文件路径")
    ap.add_argument("--adb", default=ADB_DEFAULT, help="adb.exe 路径")
    ap.add_argument("--tcpip", action="store_true",
                    help="USB 连着时切到无线 adb，之后可拔线")
    ap.add_argument("--connect", metavar="IP:端口",
                    help="连接已在无线 adb 模式下的设备，如 192.168.1.5:5555")
    ap.add_argument("--port", type=int, default=5555, help="无线 adb 端口，默认 5555")
    ap.add_argument("--check", action="store_true", help="只检查环境与当前分钟数")
    ap.add_argument("--run", action="store_true", help="开始挂机")
    ap.add_argument("--minutes", type=int, default=180, help="挂机分钟数，默认 180")
    ap.add_argument("--mode", choices=("read", "listen"), default="read",
                    help="read=定时翻页 / listen=不操作，验证听书")
    ap.add_argument("--interval", type=int, default=60, help="翻页间隔秒数，默认 60")
    ap.add_argument("--sample", type=int, default=300, help="采样间隔秒数，默认 300")
    args = ap.parse_args()

    sd.ADB = args.adb

    if args.tcpip:
        switch_to_wifi(args.port)
    if args.connect:
        connect_wifi(args.connect)

    ok = check_env(args.adb)
    if not ok:
        return 2

    client = open_client(args.config, args.phone, args.password)

    if args.check:
        cur = sample(client)
        if cur is not None:
            print(f"\n当前阅读分钟数: {cur}")
        return 0

    if args.run:
        run(client, args.minutes, args.mode, args.interval, args.sample)
        return 0

    print("\n加 --run 开始挂机，或 --check 只看状态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
