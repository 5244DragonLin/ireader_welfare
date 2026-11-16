#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""看视频奖励的纯 HTTP 领取实现（2026-11-16 打通）。

协议（详见 docs/掌阅逆向笔记.md 16.12）：

    GET https://saad.ms.zhangyue.net/rewarded-video/report
        ?app_id=10000
        &usr=<usr>&timestamp=<ms>&position=<非空串>&sign=<签名>
        &bookId=&chapterId=&appId=10000&id=<奖励id>&ext=

要点：
  · 签名只覆盖 usr / timestamp / position；id 等不进签名
  · position 必须非空且必须进签名，否则「验证签名失败」
  · **id 决定发什么奖**：
        10006x-10008x 段 → voucher（代金券，看 giftAmount）
        1015xx 段        → adless（免广告时长，看 noAdTimeAmount）
  · 同 id 有约 20 秒冷却（code 33711），次数池耗尽报 33709
"""
import json
import time
import urllib.parse
import urllib.request

from .crypto import rsa_sha1_sign, sorted_param_str

SAAD = "https://saad.ms.zhangyue.net"
ZYAD_APP_ID = "10000"
UA = ("Mozilla/5.0 (Linux; Android 15; 2211133C Build/AP4A.250105.002; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 "
      "Mobile Safari/537.36")

# 奖励 id
VOUCHER_IDS = ["100063", "100065", "100078", "100079", "100064", "100099"]
ADLESS_IDS = ["101533", "101540", "100002"]

# 已知状态不符（10013），不必再试
SKIP_IDS = {"101461", "101462", "101463", "101464", "101470"}

CODE_OK = 0
CODE_NOT_EXIST = 2         # id 不在奖励表
CODE_COOLDOWN = 33711      # 两次观看间隔过短
CODE_EXHAUSTED = 33709     # 该位置次数超限
CODE_POS_MISMATCH = 10013  # id 在表里但状态不符


def build_url(cred, reward_id, position="VIDEO_PAYTASK", app_id=ZYAD_APP_ID):
    signed = {"usr": cred.usr,
              "timestamp": str(int(time.time() * 1000)),
              "position": position}
    signed["sign"] = rsa_sha1_sign(cred.native_priv, sorted_param_str(signed))
    signed.update({"bookId": "", "chapterId": "", "appId": ZYAD_APP_ID,
                   "id": str(reward_id), "ext": ""})
    return f"{SAAD}/rewarded-video/report?app_id={app_id}&" + urllib.parse.urlencode(signed)


def report(cred, reward_id, position="VIDEO_PAYTASK", app_id=ZYAD_APP_ID,
           timeout=15):
    """调用发奖端点。返回 (json_dict, 耗时ms)。"""
    url = build_url(cred, reward_id, position, app_id)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
        return json.loads(body), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return {"code": e.code,
                "msg": e.read().decode("utf-8", "ignore")[:120]}, 0
    except Exception as e:
        return {"code": "ERR", "msg": f"{type(e).__name__}: {e}"}, 0


def describe(j):
    """把响应压成一行人话。"""
    code = j.get("code")
    if code == CODE_OK:
        b = j.get("body") or {}
        if b.get("giftType") == "voucher":
            return f"✅ +{b.get('giftAmount')} 代金券"
        if b.get("giftType") == "adless":
            return f"✅ 免广告 {b.get('noAdTimeAmount')}s"
        return f"✅ {j.get('msg')}"
    mapping = {
        CODE_NOT_EXIST: "id 不存在",
        CODE_COOLDOWN: "冷却中",
        CODE_EXHAUSTED: "次数超限",
        CODE_POS_MISMATCH: "状态不符",
    }
    return f"❌ {mapping.get(code, f'code={code}')} {j.get('msg') or ''}".strip()


def claim_vouchers(cred, ids=None, rounds=None, delay=20.0, verbose=True,
                   max_per_id=200, max_seconds=None, stop_on_all_exhausted=True):
    """把每个 id 各自领到「次数超限」为止（不是固定轮数）。

    ## 为什么这样设计（2026-11-16 实测）

    当天逐笔流水显示：38 笔 / 356 券，时间跨度 12:20-12:46。
    相邻间隔最小 **2 秒**，中位 18 秒 —— 说明冷却**不是全局的**，
    而是**每个 id 各 20 秒**。所以轮换 id 可以做到几乎无缝：

        100063(领) → 100065(领) → 100078(领) → 100064(领) → 回到 100063（已过 20s）

    因此正确的策略是「每个 id 独立跑到底」，而不是「固定 N 轮」。
    固定轮数会导致：轮数不够 → 有额度没领完；轮数太多 → 空转。

    ## 算法

    维护一个待办队列，每个 id 带「下次可领时间」：

        1. 取当前可领的 id（冷却已过），逐个请求
        2. 成功 → 记入账，该 id 冷却 delay 秒后重新入队
        3. 33709（次数超限）→ 该 id 永久出队
        4. 33711（间隔过短）→ 冷却 delay 秒后重新入队
        5. 其它错误 → 重试 retries 次后出队
        6. 队列空 / 全部耗尽 → 结束

    参数：
        rounds       兼容旧调用；None 表示「跑到耗尽」（推荐）
        delay        同 id 冷却秒数，实测约 20s，留 1s 余量
        max_per_id   单 id 上限，防止死循环
        max_seconds  总时长上限（秒），到点收工
        stop_on_all_exhausted  全部耗尽即停（否则会一直等到 max_seconds）

    返回 (总入账代金券, 成功次数, 明细列表)。
    """
    ids = [x for x in (ids or VOUCHER_IDS) if x not in SKIP_IDS]
    gained = 0.0
    adless_sec = 0
    ok = 0
    log = []
    per_id = {}          # fid -> {"ok", "amt", "fail"}
    next_at = {fid: 0.0 for fid in ids}
    exhausted = set()
    fail_count = {}
    t_start = time.time()

    # 兼容旧语义：rounds 给定时换算成单 id 上限
    if rounds:
        max_per_id = min(max_per_id, rounds * len(ids))

    def alive():
        return [f for f in ids if f not in exhausted]

    while True:
        if max_seconds and time.time() - t_start > max_seconds:
            if verbose:
                print(f"    [到时长上限 {max_seconds}s，收工]")
            break

        pool = alive()
        if not pool:
            if verbose:
                print("    [全部 id 已耗尽]")
            break

        now = time.time()
        ready = [f for f in pool if next_at[f] <= now]
        if not ready:
            # 全部在冷却：等到最早可领的那个
            wait = min(next_at[f] for f in pool) - now
            if wait > 0:
                time.sleep(min(wait, delay))
            continue

        for fid in ready:
            if per_id.get(fid, {}).get("ok", 0) >= max_per_id:
                exhausted.add(fid)
                continue

            j, ms = report(cred, fid)
            code = j.get("code")
            body = j.get("body") or {}
            d = per_id.setdefault(fid, {"ok": 0, "amt": 0.0, "fail": 0})

            if code == CODE_OK:
                amount = float(body.get("giftAmount") or 0)
                gained += amount
                ok += 1
                d["ok"] += 1
                d["amt"] += amount
                log.append((fid, "ok", amount, body.get("remainViewCount")))
                if verbose:
                    remain = body.get("remainViewCount")
                    remain_s = "-" if remain is None else str(remain)
                    kind = body.get("giftType") or ""
                    if kind == "adless":
                        sec = int(body.get("noAdTimeAmount") or 0)
                        adless_sec += sec
                        detail = f"免广告{sec}s"
                        cum = f"免广累计{adless_sec}s"
                    else:
                        detail = f"{amount:g}券"
                        cum = f"券累计{gained:g}"
                    print(f"    [{time.strftime('%H:%M:%S')}] #{ok:<2d} "
                          f"{fid}  {detail:<10s} 剩余={remain_s:<3s} "
                          f"{cum:<14s} ({ms}ms)")
                next_at[fid] = time.time() + delay

            elif code == CODE_EXHAUSTED:
                exhausted.add(fid)
                log.append((fid, "used", 0, 0))
                if verbose:
                    print(f"    [{time.strftime('%H:%M:%S')}]    "
                          f"{fid}  次数超限（共领 {d['ok']} 次 {d['amt']:g} 券），出队")

            elif code == CODE_COOLDOWN:
                # 服务端判定间隔过短，推迟更久一点
                next_at[fid] = time.time() + max(delay, 5)
                if verbose:
                    print(f"    [{time.strftime('%H:%M:%S')}]    "
                          f"{fid}  间隔过短，{delay:.0f}s 后重试")

            else:
                d["fail"] += 1
                fail_count[fid] = fail_count.get(fid, 0) + 1
                log.append((fid, "fail", 0, 0))
                if verbose:
                    print(f"    [{time.strftime('%H:%M:%S')}]    "
                          f"{fid}  {describe(j)}")
                if fail_count[fid] >= 2:
                    exhausted.add(fid)
                    if verbose:
                        print(f"    [{time.strftime('%H:%M:%S')}]    "
                              f"{fid}  连续失败，出队")
                else:
                    next_at[fid] = time.time() + max(delay, 5)

            time.sleep(0.8)

        if stop_on_all_exhausted and not alive():
            break

    # 逐 id 汇总（终端日志，不落盘）
    if verbose and per_id:
        print(f"\n    —— 本次逐 id 汇总 ——")
        for fid in sorted(per_id, key=lambda k: -per_id[k]["amt"]):
            d = per_id[fid]
            if d["ok"] and d["amt"] > 0:
                avg = d["amt"] / d["ok"]
                print(f"    {fid}  领 {d['ok']:>2d} 次  {d['amt']:>5g} 券  "
                      f"(均 {avg:.1f})")
            elif d["ok"]:
                print(f"    {fid}  领 {d['ok']:>2d} 次  （免广告，不发券）")
            elif d["fail"]:
                print(f"    {fid}  失败 {d['fail']} 次")
            else:
                print(f"    {fid}  无可用额度")
        spent = time.time() - t_start
        print(f"    合计 {gained:g} 券 / 免广告 {adless_sec}s / "
              f"成功 {ok} 次 / 耗时 {spent:.0f}s")

    return round(gained, 1), ok, log
