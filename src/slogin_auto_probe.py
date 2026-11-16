#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针 4：/user/slogin/auto —— App 冷启动的自动登录（会话恢复）。

背景
----
任务 13901「打开掌阅APP」（+10 经验）的判定不在埋点（四条埋点通道 2026-11-16
已全部实证为黑洞，见 docs 十九节），而在**冷启动专属的业务请求**上。全 dex
分析（2026-11-17，coldstart_*.py 系列脚本）还原出 App 冷启动的会话恢复链路：

    APP 启动 → account/l.u()
      → POST https://uc.ireader.com/user/slogin/auto?<usr+设备参数>
      form: imei / client_id / channel_id / version_id / device /
            is_create_zyeid=1 / timestamp / sign

    * client_id = SPHelper("UserName")（Account.D 写入 $b.a）= usr 名
    * imei      = c0.c(getDeviceId())，即 "__" 前缀 + 设备 ID（p7 同款格式）
    * 签名      = account/d.a(map)：timestamp 入 map 后全参数排序、
                  RSA/SHA1WithRSA + 内置私钥 #4（与 applaunch 同款）
    * 响应      = 与 passwd/v2 相同的 body：name + token{AesKey, Data}
                  → 可解出 session_id（能解密 = 真登录成功）

与福利 H5 会话的本质区别：我们每天跑的是 slogin/passwd（"输密码"，脚本味），
App 冷启动走 slogin/auto（"会话恢复"，打开 App 的标志动作）。若服务端把
「auto 登录成功」当作启动事实，伪造这条请求即可翻转任务。

⚠️ 每天只有一次测试窗口（任务翻成 status=1/2 后当天无法再观察）。
   探针先查 status，非 0 拒绝 --send。错误变体重试不消耗窗口。

用法
----
    python src/slogin_auto_probe.py --status              # 只看任务状态
    python src/slogin_auto_probe.py --probe --variant A   # 发一次看响应
    python src/slogin_auto_probe.py --send  --variant A   # 发 + 等翻转 + 解密会话
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# Windows GBK 控制台兜底：任何无法编码的字符替换输出，避免崩在 print 上
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ireader_welfare import auth, config
from ireader_welfare.crypto import aes_decrypt, rsa_public_decrypt, sign_params
from ireader_welfare.welfare import APP_DEVICE_PARAMS, WelfareClient

TASK_NAME = "打开掌阅APP"
AUTO_URL = "https://uc.ireader.com/user/slogin/auto"
DALVIK_UA = ("Dalvik/2.1.0 (Linux; U; Android 15; 2211133C "
             "Build/AP3A.240905.015)")

# 设备参数（8.8.1，与 APP_DEVICE_PARAMS 同源；capture 时 androidId=5a10c5b990da8855）
OLD_DEVICE_ID = "__5a10c5b990da8855"      # 参数集里的 p7（capture 时）
CUR_DEVICE_ID = "__2c4662b11689a8ea"      # 模拟器当前 android_id（2026-11-17）
EMPTY_IMEI = "__"                          # p31 同款（读不到 IMEI 时）
CHANNEL = "501603"                         # p4
VERSION = "80810"                          # p25
MODEL = "2211133C"                         # p16


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


def build_params(usr: str, variant: str, priv) -> dict:
    """按 account/l.r() + account/d.a() 构造 slogin/auto 请求参数。"""
    imei = {
        "A": OLD_DEVICE_ID,
        "B": CUR_DEVICE_ID,
        "C": EMPTY_IMEI,
        "D": "86" + str(int(time.time() * 1000))[-13:],  # auth.py 同款合成 imei
    }[variant[0]]
    client_id = usr
    if variant[1:] == "sid":       # 用 login_sid 当 client_id 的对照
        client_id = None           # 运行时从登录响应取
    p = {
        "imei": imei,
        "client_id": client_id,
        "channel_id": CHANNEL,
        "version_id": VERSION,
        "device": MODEL,
        "is_create_zyeid": "1",
    }
    return sign_params(priv, p)


def send_auto(usr: str, params: dict, with_query: bool = True) -> dict:
    """发 slogin/auto。返回解析后的 JSON。"""
    q = {"usr": usr, **APP_DEVICE_PARAMS} if with_query else {"usr": usr}
    url = AUTO_URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(params).encode(),
        headers={"User-Agent": DALVIK_UA,
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        txt = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(txt)
    except ValueError:
        return {"_raw": txt[:400]}


def try_decrypt_session(resp: dict) -> str | None:
    """响应 body.token 可解密则返回 session_id（真登录成功的证据）。"""
    body = resp.get("body")
    if not isinstance(body, dict):
        return None
    tok = body.get("token")
    if not isinstance(tok, dict) or "AesKey" not in tok:
        return None
    try:
        aes_raw = base64.b64decode(rsa_public_decrypt(tok["AesKey"]))
        inner = json.loads(aes_decrypt(aes_raw, tok["Data"]))
        return inner.get("session_id")
    except Exception as e:
        return f"解密失败: {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只查状态")
    ap.add_argument("--probe", action="store_true", help="发一次，看响应")
    ap.add_argument("--send", action="store_true", help="发并等翻转")
    ap.add_argument("--variant", default="A",
                    help="A=旧设备ID B=当前androidId C=空imei D=合成imei；"
                         "后缀 sid 用 login_sid 当 client_id（如 Asid）")
    ap.add_argument("--query", default="full", choices=["full", "usr"],
                    help="query 里带不带全套设备参数")
    ap.add_argument("--wait", type=int, default=60)
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

    if before.get("status") != 0:
        print("[!] 任务不是「未完成」，今天观察不到翻转（--probe 仍可测响应）")

    keys = auth.crypto.load_client_keys()
    priv = keys[3]  # native#4

    p = build_params(cred.usr, args.variant, priv)
    if p.get("client_id") is None:
        p["client_id"] = cred.raw.get("login_sid", "")
        p = sign_params(priv, {k: v for k, v in p.items() if k != "sign"})

    print(f"variant={args.variant}  imei={p['imei'][:24]}  "
          f"client_id={p['client_id'][:24]}")
    try:
        resp = send_auto(cred.usr, p, with_query=(args.query == "full"))
    except Exception as e:
        print(f"❌ 请求异常: {type(e).__name__}: {e}")
        return 1

    code = resp.get("code")
    print(f"HTTP 响应: code={code} msg={resp.get('msg')}")
    if code != 0:
        print(f"  完整响应: {json.dumps(resp, ensure_ascii=False)[:400]}")
        print("\n判读：auto 登录被拒（客户端身份参数不对），任务不受影响。")
        return 2

    sid = try_decrypt_session(resp)
    print(f"  ✅ auto 登录成功！可解密 session_id={sid}")

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
            print("\n结论：✅ slogin/auto 伪造触发了任务翻转！"
                  "「打开掌阅APP」可纯 HTTP 完成。")
            return 0
    print("\n结论：❌ auto 登录成功但任务未翻转——启动判定不消费会话恢复事件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
