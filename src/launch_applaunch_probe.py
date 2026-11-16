#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针 2：/zyboot/datapoint/applaunch —— 有签名校验的启动上报。

⚠️ 2026-11-16 实测结论：签名可伪造，但**通了也没用**，详见 docs 十九节。

为什么单独测这条
----------------
8.8.1 里有三个启动上报端点，实测行为完全不同：

  /zyboot/datapoint/launchreport              → 一切输入都回 code:0（黑洞，无校验）
  /zyapi/datapoint/launchreport_app_created   → 一律 -99999 系统错误（已废弃）
  /zyboot/datapoint/applaunch                 → **53001 签名错误**（活着，且有校验）

反编译定位（`pf/d0.c()`）——注意指令顺序，这是签名的关键：

    put("usr", Account.getUserName())      ← 先放 usr
    account/d.a(map)                       ← 这一步加 timestamp 并算 sign
    put("datapoint", {"action_type":"launch"})   ← ★ 在签名之后才加入

即 **签名串只含 usr + timestamp，不含 datapoint**（与福利接口全参数入签不同）。
签名算法 `Security.hash` = RSA/SHA1WithRSA + **客户端内置私钥 #4**。

⚠️ 但 `applaunch` 的真实触发点是**书架页广告投放**（`ActivityBookShelf$g.onRequested`
→ `requestTouFangInfo` → `DeviceInfor.w()` → `pf/d0.c()`），
不是「打开掌阅APP」任务的判定点。且服务端只验签不验语义（乱填 action_type 也 code:0）。

用法
----
    python src/launch_applaunch_probe.py --probe   # 验证签名 + 语义对照（推荐）
    python src/launch_applaunch_probe.py --send    # 发并等任务翻转（需 status=0）
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
from ireader_welfare.crypto import rsa_sha1_sign, sorted_param_str
from ireader_welfare.welfare import APP_DEVICE_PARAMS, WelfareClient

TASK_NAME = "打开掌阅APP"
URL = "https://ah2.zhangyue.com/zyboot/datapoint/applaunch"
DALVIK_UA = ("Dalvik/2.1.0 (Linux; U; Android 15; 2211133C "
             "Build/AP3A.240905.015)")


def find_task(c):
    for t in (c.tasks().get("body") or []):
        if TASK_NAME in (t.get("name") or ""):
            return t
    return {}


def post(c, dp=None, sign_in="usr_ts", key=None):
    """按 App 的结构发一次 applaunch。

    sign_in:
      usr_ts   App 实际行为：签名串只含 usr + timestamp（datapoint 后加）
      with_dp  对照：把 datapoint 也拼进签名串
      with_q   对照：把 query 设备参数也拼进签名串
    """
    dp = dp if dp is not None else {"action_type": "launch"}
    dp_json = json.dumps(dp, separators=(",", ":"))
    ts = str(int(time.time() * 1000))

    base = {"usr": c.usr, "timestamp": ts}
    if sign_in == "with_dp":
        base["datapoint"] = dp_json
    elif sign_in == "with_q":
        base.update(APP_DEVICE_PARAMS)
        base["zysid"] = c.zysid

    form = {
        "usr": c.usr,
        "timestamp": ts,
        "sign": rsa_sha1_sign(key or c.cred.native_priv, sorted_param_str(base)),
        "datapoint": dp_json,
    }

    q = {"usr": c.usr, "zysid": c.zysid, **APP_DEVICE_PARAMS}
    url = URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode(),
        headers={"User-Agent": DALVIK_UA,
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "ignore")
            try:
                return json.loads(txt)
            except ValueError:
                return {"_raw": txt[:300]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只测签名能否通过")
    ap.add_argument("--send", action="store_true", help="通过后发并等翻转")
    ap.add_argument("--wait", type=int, default=45)
    args = ap.parse_args()

    phone, pwd = config.resolve_credentials(config.load(None))
    cred = auth.login(phone, pwd)
    c = WelfareClient(cred)
    print(f"登录: {cred.nick} ({cred.usr})\n")

    t = find_task(c)
    st = t.get("status")
    names = {0: "未完成", 1: "可领取", 2: "已领取"}
    print(f"任务: {TASK_NAME}  status={st} ({names.get(st, '?')})")

    if args.probe:
        print("\n—— [1] 签名变体对照 ——")
        from ireader_welfare.crypto import load_client_keys
        keys = load_client_keys()
        for label, kw in [
            ("签名串=usr+ts（App 行为）", dict(sign_in="usr_ts")),
            ("签名串含 datapoint", dict(sign_in="with_dp")),
            ("签名串含 query 设备参数", dict(sign_in="with_q")),
        ]:
            r = post(c, **kw)
            code = r.get("code")
            print(f"  {label:<26} {'✅ code=0' if code == 0 else f'❌ code={code}'}"
                  f"  {r.get('msg')}")

        print("\n—— [2] 私钥对照（哪把内置私钥能过验签）——")
        for i, k in enumerate(keys, 1):
            r = post(c, key=k)
            code = r.get("code")
            print(f"  native#{i:<3} {'✅ code=0' if code == 0 else f'❌ code={code}'}")

        print("\n—— [3] ★ 语义对照：服务端是否校验 datapoint ——")
        for label, dp in [
            ("action_type=launch", {"action_type": "launch"}),
            ("action_type=zzz_not_exist", {"action_type": "zzz_not_exist"}),
            ("空 JSON {}", {}),
        ]:
            r = post(c, dp=dp)
            code = r.get("code")
            print(f"  {label:<28} {'✅ code=0' if code == 0 else f'❌ code={code}'}")

        print("\n判读：")
        print("  · 只有 native#4 + 签名串=usr+ts 能过 → 签名规则已完全还原。")
        print("  · 语义对照全 code=0 → 服务端只验签不验语义，")
        print("    datapoint 只是随包负载，不驱动任何任务判定。")
        print("  · 因此这条端点**不是**「打开掌阅APP」的判定点，")
        print("    真实触发点是书架页广告投放（见 docs 十九节）。")
        return 0

    if not args.send:
        ap.print_help()
        return 1

    if st != 0:
        print(f"\n[!] status={st}，今天观察不到翻转。等次日重置。")
        return 2

    print("\n—— 发送 applaunch 并等翻转 ——")
    r = post(c)
    print(f"  响应: {r}")
    if r.get("code") != 0:
        print("  签名未通过，先跑 --probe 排查")
        return 1

    print(f"等待翻转（最多 {args.wait}s）...")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(5)
        cur = find_task(c)
        print(f"  status={cur.get('status')}")
        if (cur.get("status") or 0) >= 1:
            print("  ✅ 翻转了 —— 这条任务可以纯 HTTP 完成")
            return 0
    print("  ❌ 未翻转")
    return 0


if __name__ == "__main__":
    sys.exit(main())
