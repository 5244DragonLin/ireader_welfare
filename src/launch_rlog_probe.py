#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针 3：用 rlog 实时埋点通道伪造 event_id=20036（打开APP）。

为什么这是最后一个没试过的变量
------------------------------
任务 13901 的判定字段是 `value.type = 20036`。把四个日常任务横向对比：

| 任务 | target.type_id | value.type | mode | 判定靠什么 |
|------|----------------|------------|------|-----------|
| 阅读品质好书 满5分钟 | 20 | 20017 | 1（累计） | 埋点 read_time 事件累加 |
| 收听品质好书满10分钟 | 20 | 20033 | 1（累计） | 埋点 listen_time 事件累加 |
| 分享今日日签 | **19** | 0 | 0 | 业务回执 share_report |
| 打开掌阅APP | 20 | **20036** | 0 | ？（本节要测） |

`target.type_id=20` 的三个任务，`value.type` 都是「埋点事件 id」。
13.4 曾用 rlog 伪造 `event_id=20017 / 20033`，但**从未试过 20036**。
这是本轮唯一还有新变量的方向。

协议（13.3 已验证 code:0）：
    POST https://log.ireader.com/log-agent/rlog
    form: data={"event_id":"<id>","params":{...}}
          &public_params={...device/user...}
          &topic=<值>&sign_type=MD5&sign=MD5(排序串, topic 值 reverse)

用法
----
    python src/launch_rlog_probe.py --probe   # 只测网关是否收（不等翻转）
    python src/launch_rlog_probe.py --send    # 发并等翻转（需 status=0）
"""
import argparse
import hashlib
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
RLOG = "https://log.ireader.com/log-agent/rlog"
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 15; 2211133C) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Version/4.0 Chrome/100.0 Mobile Safari/537.36")


def find_task(c):
    for t in (c.tasks().get("body") or []):
        if TASK_NAME in (t.get("name") or ""):
            return t
    return {}


def post_rlog(c, event_id="20036", params=None, topic="zyreader") -> dict:
    """按 13.3 的协议发一条 rlog。

    topic 实测**任意非空值都收**（云端配置的真实值本地拿不到，
    见 13.2 注）。传空字符串会回 `code:1 parameter is required: topic`。
    """
    now_ms = int(time.time() * 1000)
    data = {
        "event_id": str(event_id),
        "params": params or {
            "action_type": "launch",
            "time": now_ms,
            "usr": c.usr,
        },
    }
    public = {
        "type": "clientData",
        "package_name": "com.chaozh.iReaderFree",
        "app_version": "8.8.1",
        "device_id": APP_DEVICE_PARAMS.get("p31", "__"),
        "user_id": c.usr,
        "rgt": "7",
        "channel_id": APP_DEVICE_PARAMS.get("p4", "501603"),
        "os_version": APP_DEVICE_PARAMS.get("p22", "15"),
        "version_code": APP_DEVICE_PARAMS.get("p25", "80810"),
    }

    form = {
        "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        "public_params": json.dumps(public, ensure_ascii=False,
                                    separators=(",", ":")),
        "topic": topic,
        "sign_type": "MD5",     # ★ 在签名之前 put，参与签名
    }
    # sign（repair/j.D）：按 key 字典序拼 k=v，跳过空 key / null 值，
    # topic 的值先 reverse()，最后 MD5 小写 hex。
    # 反编译 eh/b.m 指令顺序：data→public_params→topic→sign_type→算 sign→put sign
    parts = []
    for k in sorted(form):
        if not k:
            continue
        v = form[k]
        if v is None:
            continue
        v = str(v)
        if k == "topic":
            v = v[::-1]
        parts.append(f"{k}={v}")
    form["sign"] = hashlib.md5("&".join(parts).encode()).hexdigest()

    req = urllib.request.Request(
        RLOG,
        data=urllib.parse.urlencode(form).encode(),
        headers={"User-Agent": MOBILE_UA,
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
    ap.add_argument("--probe", action="store_true", help="只测网关")
    ap.add_argument("--send", action="store_true", help="发并等翻转（需 status=0）")
    ap.add_argument("--wait", type=int, default=60)
    args = ap.parse_args()

    if not (args.probe or args.send):
        ap.print_help()
        return 1

    phone, pwd = config.resolve_credentials(config.load(None))
    cred = auth.login(phone, pwd)
    c = WelfareClient(cred)
    print(f"登录: {cred.nick} ({cred.usr})\n")

    t = find_task(c)
    st = t.get("status")
    names = {0: "未完成", 1: "可领取", 2: "已领取"}
    print(f"任务: {TASK_NAME}  status={st} ({names.get(st, '?')})\n")

    if args.probe:
        print("—— 埋点网关对照（不同 event_id 是否都 code:0）——")
        for eid in ("20036", "20017", "20033", "99999", ""):
            r = post_rlog(c, event_id=eid)
            code = r.get("code")
            print(f"  event_id={eid or '(空)':<8} "
                  f"{'✅ code=0' if code == 0 else f'❌ code={code}'}  "
                  f"{r.get('msg') or r}")
        print("\n判读：")
        print("  · 全部 code=0（含 99999 / 空）→ 网关不校验 event_id，")
        print("    与 13.4 的 sensorlog 同族，翻转与否取决于数仓层。")
        print("  · 只有合法 id 通过 → 服务端有白名单，20036 值得 --send 实测。")
        return 0

    if st != 0:
        print(f"[!] status={st}，今天观察不到翻转。等次日重置后再 --send。")
        return 2

    print("—— 发送 rlog event_id=20036 并等翻转 ——")
    r = post_rlog(c)
    print(f"  响应: {r}")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(6)
        cur = find_task(c)
        print(f"  status={cur.get('status')}")
        if (cur.get("status") or 0) >= 1:
            print("  ✅ 翻转了 —— 20036 埋点可驱动任务，纯 HTTP 可行")
            return 0
    print("  ❌ 未翻转 —— 与 sensorlog 同族，被数仓丢弃")
    return 0


if __name__ == "__main__":
    sys.exit(main())
