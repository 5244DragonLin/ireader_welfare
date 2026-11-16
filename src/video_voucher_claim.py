#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""★★ 纯 HTTP 领取「看视频送代金券」奖励（2026-11-16 打通）

══════════════════════════════════════════════════════════════
结论：完全不需要真的看广告，一个 GET 请求就能领到代金券
══════════════════════════════════════════════════════════════

端点：GET https://saad.ms.zhangyue.net/rewarded-video/report?app_id=10000

签名：usr + timestamp + position + sign   （position 必须进签名，否则「验证签名失败」）
未签名追加：bookId chapterId appId id ext

## 核心机制（实测确认）

`id` 是唯一的业务键，position 只要非空就行（连中文乱写都通过）。
**id 决定发什么奖**：
  · voucher  → 代金券，giftAmount 是面额
  · adless   → 免广告时长，noAdTimeAmount 是秒数

## 有效 id 段（2026-11-16 实测，位于 100060-100100）

| id | giftType | 首次面额 | 备注 |
|----|----------|---------|------|
| 100063 | voucher | 10 | 有冷却，可重复领 |
| 100064 | voucher | 5~15 | 阶梯递增 |
| 100065 | voucher | 10 | |
| 100078 | voucher | 10 | |
| 100079 | voucher | 10 | 次数少 |
| 100099 | voucher | 5 | |
| 101533/101535-101540 | adless | 900 秒免广告 | 不限次 |
| 100002 | adless | 10 秒 | |

## 三个限制（实测）

| 错误码 | 含义 | 应对 |
|--------|------|------|
| `33709 该位置观看次数超过限制` | 该 id 的当日/总量次数用完 | 换下一个 id |
| `33711 两次观看视频间隔时间过短` | 同 id 冷却中（实测约 20 秒） | 等待或轮换 id |
| `10013 上报的位置和配置的位置不一致` | id 在表里但状态不符（如已过期） | 跳过 |
| `2 reward not exist` | id 不在奖励表 | 跳过 |

## 实测战果（2026-11-16）

```text
起始 402 券
  100063 → +10   100064 → +6   100065 → +10   100078 → +10
  100099 → 33711（CD 中）
余额 438 券  ← 实际入账 36
```

验真方式：`WelfareClient.vouchers()` 里「看视频送礼」的金额会实打实增加。

──────────────────────────────────────────────────────────────
用法：
    python src/video_voucher_claim.py --list          # 探测可用 id 与当前面额
    python src/video_voucher_claim.py --claim         # 自动轮换领取全部可用
    python src/video_voucher_claim.py --claim --rounds 3
    python src/video_voucher_claim.py --balance       # 只看余额
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "src")
from ireader_welfare import config as zconfig, auth, welfare  # noqa: E402
from reward_video_api import KNOWN_GOOD, call, fmt  # noqa: E402

# 已确认发代金券的 id（按首次面额降序，优先领大的）
VOUCHER_IDS = ["100063", "100065", "100078", "100079", "100064", "100099"]

# 免广告时长 id（不限次，但价值低，默认不领）
ADLESS_IDS = ["101533", "101540", "100002"]

# 明确跳过（已知状态不符）
SKIP = {"101461", "101462", "101463", "101464", "101470"}

CD_SECONDS = 22          # 实测同 id 冷却约 20 秒，留点余量
CODE_COOLDOWN = 33711    # 间隔过短
CODE_EXHAUSTED = 33709   # 次数超限


def balance(c):
    """返回 (未用合计, 看视频送礼合计, 明细)。"""
    vs = c.vouchers()
    active = [v for v in vs if not v.get("used")]
    total = round(sum(v["amount"] for v in active), 1)
    video = round(sum(v["amount"] for v in active
                      if "看视频" in (v.get("source") or "")), 1)
    return total, video, active


def probe(c, ids):
    """探测每个 id 当前能否领、面额多少（会真的发放，谨慎）。"""
    rows = []
    for fid in ids:
        if fid in SKIP:
            rows.append((fid, "skip", "已知状态不符"))
            continue
        j, ms = call(c.cred, fid)
        code = j.get("code")
        b = j.get("body") or {}
        if code == 0:
            rows.append((fid, "ok", f"{b.get('giftType')} {b.get('giftAmount')}券"
                                     f" / 免广{b.get('noAdTimeAmount')}s"))
        elif code == CODE_COOLDOWN:
            rows.append((fid, "cd", "冷却中"))
        elif code == CODE_EXHAUSTED:
            rows.append((fid, "used", "次数超限"))
        else:
            rows.append((fid, "fail", f"code={code} {j.get('msg')}"))
        time.sleep(1.2)
    return rows


def claim(c, ids, rounds=1, delay=CD_SECONDS, verbose=True):
    """轮换领取。返回 (总获得, 成功次数, 明细)。"""
    gained_total = 0.0
    ok_count = 0
    log = []
    cd = {}          # id -> 下次可领时间

    for rnd in range(1, rounds + 1):
        if verbose:
            print(f"\n—— 第 {rnd}/{rounds} 轮 ——")
        for fid in ids:
            if fid in SKIP:
                continue
            now = time.time()
            if cd.get(fid, 0) > now:
                wait = cd[fid] - now
                if verbose:
                    print(f"  {fid}: 冷却 {wait:.0f}s，跳过")
                continue

            before, _, _ = balance(c)
            j, ms = call(c.cred, fid)
            code = j.get("code")
            b = j.get("body") or {}
            time.sleep(3)
            after, _, _ = balance(c)
            delta = round(after - before, 1)

            if code == 0:
                ok_count += 1
                gained_total += delta
                amt = b.get("giftAmount")
                msg = (f"✅ +{delta}券（响应面额 {amt}）"
                       if b.get("giftType") == "voucher"
                       else f"✅ 免广告 {b.get('noAdTimeAmount')}s")
                log.append((fid, "ok", delta, msg))
                if verbose:
                    print(f"  {fid}: {msg}  余额 {before}→{after}")
                cd[fid] = time.time() + delay
            elif code == CODE_COOLDOWN:
                log.append((fid, "cd", 0, "冷却"))
                if verbose:
                    print(f"  {fid}: 冷却中")
                cd[fid] = time.time() + delay
            elif code == CODE_EXHAUSTED:
                log.append((fid, "used", 0, "次数超限"))
                if verbose:
                    print(f"  {fid}: 次数已超限，本 id 不再尝试")
                ids = [x for x in ids if x != fid]   # 移除
            else:
                log.append((fid, "fail", 0, f"code={code} {j.get('msg')}"))
                if verbose:
                    print(f"  {fid}: ❌ code={code} {j.get('msg')}")
            time.sleep(1.5)

        if rnd < rounds:
            if verbose:
                print(f"  …本轮结束，等 {delay}s 进入下一轮")
            time.sleep(delay)

    return gained_total, ok_count, log


def main():
    ap = argparse.ArgumentParser(description="纯 HTTP 领取看视频代金券")
    ap.add_argument("--claim", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--delay", type=float, default=CD_SECONDS)
    ap.add_argument("--ids", default=",".join(VOUCHER_IDS))
    ap.add_argument("--with-adless", action="store_true", help="顺便领免广告时长")
    a = ap.parse_args()

    cfg = zconfig.load("config.yaml")
    phone, pwd = zconfig.resolve_credentials(cfg, None, None)
    cred = auth.login(phone, pwd)
    c = welfare.WelfareClient(cred)
    print(f"usr={cred.usr}")

    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    if a.with_adless:
        ids += ADLESS_IDS

    if a.balance or (not a.claim and not a.list):
        total, video, _ = balance(c)
        print(f"  未用代金券合计: {total}")
        print(f"  其中「看视频送礼」: {video}")

    if a.list:
        print("\n—— 探测 id 可用性（会真的发放，注意）——")
        for fid, state, note in probe(c, ids):
            print(f"  {fid:8s} [{state:4s}] {note}")
        total, video, _ = balance(c)
        print(f"\n  当前未用合计 {total}（看视频送礼 {video}）")

    if a.claim:
        t0, v0, _ = balance(c)
        print(f"\n起始：未用合计 {t0}（看视频送礼 {v0}）")
        gained, ok, log = claim(c, ids, rounds=a.rounds, delay=a.delay)
        time.sleep(3)
        t1, v1, _ = balance(c)
        print("\n" + "=" * 60)
        print(f"成功 {ok} 次，实测入账 {round(t1-t0,1)} 券")
        print(f"余额：{t0} → {t1}（看视频送礼 {v0} → {v1}）")


if __name__ == "__main__":
    main()