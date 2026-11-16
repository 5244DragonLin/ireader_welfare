#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针 5：/log/report + init_app 行为事件 —— 「打开掌阅APP」的主攻方向。

为什么这条线最可信
------------------
2026-11-17 全 dex 分析（coldstart_*.py 系列）还原出掌阅自研行为 SDK 的
UserCenter 实时通道：

    每次冷启动（用户名非空）：
      nd/g$a.e(intent)                       ← App 初始化 intent 处理器
        → behavior.g.y("init_app", params)   ← ★ 每次冷启动必发
        → behavior.g.m()                     ← 触发上传
      → repair/q.run()
        → POST <host>/log/report
           form: user_name / compress_type=gzip / data=[{事件JSON}]
                 + timestamp + sign（account/d.a：全参数排序，RSA#4）
           body  = gzip(urlencode(form))
           headers: Content-Type: application/x-www-form-urlencoded; charset=UTF-8
                    content-encoding: gzip
           响应: {"code": 0} 即成功

事件 JSON（repair/p.b）:
    {"topic": "init_app", "time": "YYYY-MM-DD HH:mm:ss",
     "public_params": {...设备上下文 p.d()...},
     "params": {...init_app 业务参数 nd/g$a.a()...}}

与 sensorlog/rlog 的本质区别：那是第三方 SDK 流（神策/实时 clientData），
已实证数仓丢弃；这条是**用户中心自研行为流**，user_name 直绑账号，
且 init_app 语义 = 「打开掌阅APP」本身（任务 value.type=20036 的同族编号
20017=read_time、20033=listen_time 都是行为事件名）。

⚠️ 每天只有一次观察窗口：先查任务 status，非 0 拒绝 --send。

用法
----
    python src/logreport_initapp_probe.py --status
    python src/logreport_initapp_probe.py --probe    # 发一次看响应（不等翻转）
    python src/logreport_initapp_probe.py --send     # 发并等翻转
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ireader_welfare import auth, config
from ireader_welfare.crypto import rsa_sha1_sign, sorted_param_str
from ireader_welfare.welfare import WelfareClient

TASK_NAME = "打开掌阅APP"

# /log/report 挂在用户中心域（URL_REALTIME_BEHAVIOR_UPLOAD_USER_CENTER 是路径，
# host 由 App 网络层配置；USER_CENTER 族 = uc.ireader.com，备选 log.ireader.com）
HOSTS = [
    "https://uc.ireader.com/log/report",
    "https://log.ireader.com/log/report",
]

DALVIK_UA = ("Dalvik/2.1.0 (Linux; U; Android 15; 2211133C "
             "Build/AP3A.240905.015")

# 设备上下文（模拟器实测值；imei 为 MuMu 虚拟电话 IMEI 原值，
# device_id 用神策 distinct_id = App 当前 androidId）
PUBLIC_PARAMS = {
    "user_id": "",               # 运行时填 usr
    "device_id": "5a30c5b770da4455",
    "inner_version": "com.chaozh.iReaderFree",
    "version_code": "80810",
    "channel_id": "501603",
    "app_platform": "android",
    "model": "Xiaomi 2211133C",
    "app_package": "com.chaozh.iReaderFree",
    "imei": "866084042510831",
    "sim_num": "",
    "sim_type": "",
    "ip": "",
    "screen_size": "900*1600",
    "oper_sys_ver": "15",
    "oper_sys_rom": "MuMu",
    "network": "wifi",
}


def find_task(c: WelfareClient) -> dict:
    for t in (c.tasks().get("body") or []):
        if TASK_NAME in (t.get("name") or ""):
            return t
    return {}


def show_status(c: WelfareClient, label: str = "") -> dict:
    t = find_task(c)
    st = t.get("status")
    desc = {0: "未完成", 1: "可领取", 2: "已领取"}.get(st, "?")
    print(f"{label}status={st} ({desc})  award={t.get('award')}")
    return t


def build_event(usr: str) -> dict:
    """按 repair/p.b + p.d + nd/g$a.a 构造 init_app 事件。"""
    now_ms = int(time.time() * 1000)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    public = dict(PUBLIC_PARAMS)
    public["user_id"] = usr
    event = {
        "topic": "init_app",
        "time": now_str,
        "public_params": public,
        "params": {
            "start_type": "cold",
            "hasYunshanfu": 0,
            "is_first_install": 0,
            "pointId": "1580",
            "from_page": "none",
            "from_page_type": "",
            "from_page_key": "",
            "page": "",
            "page_type": "",
            "page_key": "",
            "fixedTime_78501": now_ms,
            "start_source_position": "桌面图标",
            "start_from": "icon",
        },
    }
    return event


def send_logreport(usr: str, host_idx: int = 0) -> dict:
    """发一次 /log/report。返回 {url, resp}。"""
    event = build_event(usr)
    data = json.dumps([event], ensure_ascii=False, separators=(",", ":"))

    form = {
        "user_name": usr,
        "compress_type": "gzip",
        "data": data,
        "timestamp": str(int(time.time() * 1000)),
    }
    # account/d.a：timestamp 入 map 后全参数排序签名（RSA#4）
    keys = auth.crypto.load_client_keys()
    form["sign"] = rsa_sha1_sign(keys[3], sorted_param_str(form))

    body = gzip.compress(urllib.parse.urlencode(form).encode("utf-8"))

    url = HOSTS[host_idx]
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": DALVIK_UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "content-encoding": "gzip",
        },
        method="POST",
    )
    out = {"url": url, "resp": None, "http": None, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "ignore")
            out["http"] = r.status
            try:
                out["resp"] = json.loads(txt)
            except ValueError:
                out["resp"] = txt[:300]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--probe", action="store_true", help="发一次看响应")
    ap.add_argument("--send", action="store_true", help="发并等翻转")
    ap.add_argument("--host", type=int, default=0, choices=[0, 1])
    ap.add_argument("--wait", type=int, default=75)
    args = ap.parse_args()

    if not (args.status or args.probe or args.send):
        ap.print_help()
        return 1

    phone, pwd = config.resolve_credentials(config.load(None))
    cred = auth.login(phone, pwd)
    c = WelfareClient(cred)
    print(f"登录: {cred.nick} ({cred.usr})\n")

    before = show_status(c, "任务  ")
    if args.status:
        return 0

    print(f"\n—— 发送 init_app → {HOSTS[args.host]} ——")
    r = send_logreport(cred.usr, args.host)
    if r["error"]:
        print(f"  ❌ {r['error']}")
        if args.host == 0 and not args.send:
            print("  提示：试 --host 1（log.ireader.com）")
        return 1
    print(f"  HTTP {r['http']}  resp={r['resp']}")

    if not args.send or before.get("status") != 0:
        return 0

    print(f"\n等待任务翻转（最多 {args.wait}s）...")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(6)
        cur = find_task(c)
        st = cur.get("status")
        print(f"  status={st}")
        if st and st >= 1:
            print("\n结论：[OK] /log/report init_app 触发翻转！"
                  "「打开掌阅APP」纯 HTTP 打通。")
            return 0
    print("\n结论：[X] 网关收下但任务未翻转。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
