#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「打开掌阅APP」冷启动埋点能否纯 HTTP 伪造 —— 实测探针。

背景
----
任务 13901「打开掌阅APP」（+10 经验值）的完成判定在客户端冷启动埋点：
  jump_url: "javascript:void(0);"   value.type: 20036

反编译 8.8.1 后还原出真实的请求结构（`com/zhangyue/iReader/app/APP.m()` →
`pf/d0.e(String)` → `pf/d0.b(String)`）：

    POST https://ah2.zhangyue.com/zyboot/datapoint/launchreport?<App 网络栈自动附加的设备参数>
    Content-Type: application/x-www-form-urlencoded
    datapoint={"action_type":"launch"}

关键点：
  * **没有业务签名**（不像 /zyuc /zycl 那套 RSA-SHA1），只有 query 上的设备参数；
  * body 只有一个 JSON：{"action_type":"launch"}；
  * 名字里带 datapoint，和 /log-agent/sensorlog、/log-agent/rlog 同族——
    都是「埋点网关」，不是「业务接口」。

所以本探针要回答的问题是：这条**埋点通道**发出去，任务会不会翻转？
（13.4 已证 sensorlog/rlog 伪造会被数仓丢弃，但这两条是「行为数据流」；
 本接口语义是「一次启动事实」，性质更接近回执，值得单独测。）

用法
----
    python src/launch_report_probe.py --status     # 只看任务当前状态（零请求）
    python src/launch_report_probe.py --probe      # 只测网关是否收（不等翻转）
    python src/launch_report_probe.py --send       # 发埋点并观察翻转（需 status=0）

⚠️ 每天只能测一次：任务一旦被真实冷启动翻成 status=2，当天就再也测不出来了。
   `--send` 会先检查 status，非 0 时直接拒绝发请求；`--probe` 只测响应格式。
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ireader_welfare import auth, config
from ireader_welfare.welfare import APP_DEVICE_PARAMS, WelfareClient

TASK_NAME = "打开掌阅APP"
LAUNCH_URL = "https://ah2.zhangyue.com/zyboot/datapoint/launchreport"
APP_CREATED_URL = "https://ah2.zhangyue.com/zyapi/datapoint/launchreport_app_created"

# App 原生栈的 Dalvik UA（与 share_report 实测翻转时一致）
DALVIK_UA = ("Dalvik/2.1.0 (Linux; U; Android 15; 2211133C "
             "Build/AP3A.240905.015)")


def find_task(c: WelfareClient) -> dict:
    for t in (c.tasks().get("body") or []):
        if TASK_NAME in (t.get("name") or ""):
            return t
    return {}


def show_status(c: WelfareClient, label: str = "") -> dict:
    t = find_task(c)
    if not t:
        print("未找到该任务（可能今日不下发）")
        return {}
    st = t.get("status")
    desc = {0: "未完成", 1: "可领取", 2: "已领取"}.get(st, "?")
    print(f"{label}status={st} ({desc})  award={t.get('award')}  "
          f"value.type={(t.get('target_list') or [{}])[0].get('value', {}).get('type')}")
    return t


def send_launch(c: WelfareClient, variant: str = "full",
                endpoint: str = "launch", payload: dict = None) -> dict:
    """发一次启动埋点。返回 {url, body, resp}。"""
    if variant == "full":
        # App 网络栈自动附加：usr + zysid + 全套设备参数
        q = {"usr": c.usr, "zysid": c.zysid, **APP_DEVICE_PARAMS}
    elif variant == "fake_usr":
        q = {"usr": "i9999999999", "zysid": c.zysid, **APP_DEVICE_PARAMS}
    elif variant == "no_usr":
        q = dict(APP_DEVICE_PARAMS)
    else:
        q = {"usr": c.usr}

    url = (LAUNCH_URL if endpoint == "launch" else APP_CREATED_URL) \
        + "?" + urllib.parse.urlencode(q)
    if payload is None:
        payload = {"datapoint": json.dumps({"action_type": "launch"},
                                           separators=(",", ":"))}

    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode(),
        headers={
            "User-Agent": DALVIK_UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    out = {"url": url, "body": payload, "resp": None, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "ignore")
            out["http"] = r.status
            try:
                out["resp"] = json.loads(txt)
            except ValueError:
                out["resp"] = txt[:400]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def wait_flip(c: WelfareClient, seconds: int = 45, step: int = 5) -> dict:
    """轮询等任务翻转，返回最后一次读到的任务。"""
    print(f"等待翻转（最多 {seconds} 秒，每 {step} 秒查一次）...")
    deadline = time.time() + seconds
    last = {}
    while time.time() < deadline:
        time.sleep(step)
        last = find_task(c)
        st = last.get("status")
        print(f"  T+{int(time.time() - (deadline - seconds))}s  status={st}")
        if st and st >= 1:
            print("  ✅ 翻转了")
            return last
    print("  ❌ 未翻转")
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只查状态，不发请求")
    ap.add_argument("--probe", action="store_true",
                    help="只测网关是否接受请求（不要求 status=0，也不等翻转）")
    ap.add_argument("--send", action="store_true", help="发启动埋点")
    ap.add_argument("--variant", default="full", choices=["full", "bare"],
                    help="full=带全套设备参数，bare=只带 usr")
    ap.add_argument("--endpoint", default="launch",
                    choices=["launch", "app_created"],
                    help="launch=/zyboot/datapoint/launchreport；"
                         "app_created=/zyapi/datapoint/launchreport_app_created")
    ap.add_argument("--wait", type=int, default=45, help="发完后等多少秒")
    args = ap.parse_args()

    if not (args.status or args.send or args.probe):
        ap.print_help()
        return 1

    phone, pwd = config.resolve_credentials(config.load(None))
    cred = auth.login(phone, pwd)
    c = WelfareClient(cred)
    print(f"登录: {cred.nick} ({cred.usr})\n")

    before = show_status(c, "基线  ")
    if not before:
        return 1

    if args.status:
        return 0

    if args.probe:
        print("\n—— 只测网关（不等翻转）——")
        print("\n[1] 正常结构（launchreport）")
        for variant in ("bare", "full"):
            r = send_launch(c, variant, "launch")
            tag = f"launch/{variant}"
            if r["error"]:
                print(f"  {tag:<24} ❌ {r['error']}")
            else:
                print(f"  {tag:<24} HTTP {r.get('http')}  {r['resp']}")

        print("\n[2] ★ 对照实验：故意发垃圾，看是否同样成功（判空壳）")
        cases = [
            ("空 body", {}, "full"),
            ("datapoint 为空", {"datapoint": ""}, "full"),
            ("action_type 乱填",
             {"datapoint": json.dumps({"action_type": "zzz_not_exist"})}, "full"),
            ("datapoint 非法 JSON", {"datapoint": "{{{"}, "full"),
            ("不存在的 usr", None, "fake_usr"),
            ("完全不带 usr", None, "no_usr"),
        ]
        for label, payload, variant in cases:
            r = send_launch(c, variant, "launch", payload)
            if r["error"]:
                print(f"  {label:<24} ❌ {r['error']}")
            else:
                print(f"  {label:<24} HTTP {r.get('http')}  {r['resp']}")

        print("\n判读：")
        print("  · 垃圾数据也回 code:0 → 网关只做「收下」不做校验，")
        print("    这条通道和 sensorlog/rlog 同族，翻转与否只能看次日实测。")
        print("  · 垃圾数据被拒（非 0）→ 服务端有参数校验，说明它认这个结构，")
        print("    翻转概率更高，值得次日 status=0 时用 --send 测。")
        return 0

    if before.get("status") != 0:
        print(f"\n[!] 任务当前不是「未完成」，今天测不出来了。"
              f"需要等次日重置后再测。")
        return 2

    print(f"\n—— 发送启动埋点（variant={args.variant}, "
          f"endpoint={args.endpoint}）——")
    r = send_launch(c, args.variant, args.endpoint)
    print(f"  URL : {r['url'][:160]}...")
    print(f"  body: {r['body']}")
    if r["error"]:
        print(f"  ❌ {r['error']}")
        return 1
    print(f"  HTTP {r.get('http')}  resp={r['resp']}")

    after = wait_flip(c, args.wait)
    print()
    if after.get("status") and after["status"] >= 1:
        print("结论：✅ 埋点伪造**成功**触发了任务翻转。")
        print("      → 这条任务可以纯 HTTP 完成，可以接进 run。")
        return 0
    print("结论：❌ 网关收下（或未收下）但任务不翻转。")
    print("      → 与 sensorlog/rlog 同族，被数仓/风控层过滤，纯 HTTP 不可行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
