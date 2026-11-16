#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rewarded-video/report 协议破解记录与复现工具。

═══════════════════════════════════════════════════════════════
2026-11-16 重大突破：这个接口第一次返回 code=0 并真的发奖
═══════════════════════════════════════════════════════════════

## 结论（已实测）

端点：`GET https://saad.ms.zhangyue.net/rewarded-video/report?app_id=10000`

签名：`usr` + `timestamp` + `position` + `sign`（position 必须进签名）
      → 不进签名会返回 `code:4 验证签名失败`
未签名追加：`bookId` `chapterId` `appId` `id` `ext`

**`id` 是唯一的业务键，`position` 只是标签。**

## 已确认能成功的 id（code=0, isSendGift=true）

| id | giftType | noAdTimeAmount | 说明 |
|----|----------|----------------|------|
| 100002 | adless | 10 | 免广告 10 秒 |
| 101465~101469 | adless | 1 | 免广告 1 秒 |
| 101533 / 101535~101540 | adless | 900 | **免广告 900 秒（15分钟）** |
| 101537 / 101539 | adless | 900 | 同上（来自 /ad/cfg 的 filterId） |

实测响应样例：
```json
{"code":0,"msg":"您的免广告时长已生效",
 "body":{"isSendGift":true,"giftAmount":0,"giftType":"adless",
         "chapterAmount":0,"noAdTimeAmount":900,"remainViewCount":9,"bookId":0}}
```

**`remainViewCount: 9` 与 App UI 的「剩余9次机会」完全吻合**——证明这个接口
真正连通了业务逻辑，不是假成功。

## 错误码语义（差分实验确认）

| 现象 | 语义 |
|------|------|
| `code:0` + `isSendGift:true` | ✅ 发奖成功 |
| `code:2 reward not exist` | id 不在奖励配置表（如 10156 / 147659 / 999999） |
| `code:10013 上报的位置和配置的位置不一致` | id 在表里，但该条记录的 position 绑定与我们上报的不符（如 101461~101464） |
| `code:1 bad biz_code` | position 为空 |
| `code:4 验证签名失败` | position 没进签名 |

**10013 与 position 的字符串值无关**：实测传 `VIDEO`、`MARKET`、`IREADER__ENV`
这种明显不是广告位的值，101463 依然返回 10013。所以 10013 是 id 自身的属性
（配置里绑了别的 position / 已过期 / 未绑定活动），不是我们传错。

## 重要修正：这个接口只发「免广告时长」

扫了 100000 段（100000-100050）、101400 段、101460 段、101530-101560 段，
**所有 code=0 的响应 giftType 都是 `adless`**，没有一个发代金券（voucher）。

所以：
- ❌ 之前设想「用 rewarded-video/report 直接领 10 代金券」——**不成立**
- ✅ 它是「看完广告免广告」功能的领奖接口
- ❓ 视频代金券（VIDEO_PAYTASK 的 10 券）走的是**另一条路**，尚未找到

## 视频代金券可能的路径（待验证）

`/zycl/welfare/video/*` 全部 404（draw/receive/reward/get/finish/complete 都试过）。
说明它不是独立端点，而是：
1. `/zycl/welfare/video/status` 的某种「带动作的调用」（改 method 或加 action 参数）
2. 或走第三方广告 SDK 的回调（sigmob / kwad / csj 的 server-to-server 回调）
3. 或走 `/welfare/market/task/*` 任务体系（但 16.9 已证明视频不在此系统）

───────────────────────────────────────────────────────────────
用法：
    python src/reward_video_api.py --probe          # 探测已知有效的 id
    python src/reward_video_api.py --claim 101533   # 领取 900 秒免广告
    python src/reward_video_api.py --scan 101530 101560  # 扫区间
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "src")
from ireader_welfare import config as zconfig, auth  # noqa: E402
from ireader_welfare.crypto import rsa_sha1_sign, sorted_param_str  # noqa: E402

UA = ("Mozilla/5.0 (Linux; Android 15; 2211133C Build/AP4A.250105.002; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 "
      "Mobile Safari/537.36")
SAAD = "https://saad.ms.zhangyue.net"
APP_ID = "10000"

# 已验证 code=0 的 id
KNOWN_GOOD = {
    "101533": 900, "101535": 900, "101536": 900, "101537": 900,
    "101538": 900, "101539": 900, "101540": 900,
    "101465": 1, "101466": 1, "101467": 1, "101468": 1, "101469": 1,
    "100002": 10,
}


def build_url(cred, bean_id, position="VIDEO_PAYTASK", extra=None, app_id=APP_ID):
    """按 j;->t 的真实规则构造 URL。

    签名覆盖 usr / timestamp / position（+ 任何 extra 里要签的）；
    bookId / chapterId / appId / id / ext 作为未签名 query 追加。
    """
    signed = {"usr": cred.usr,
              "timestamp": str(int(time.time() * 1000)),
              "position": position}
    if extra:
        signed.update(extra)
    signed["sign"] = rsa_sha1_sign(cred.native_priv, sorted_param_str(signed))
    payload = {"bookId": "", "chapterId": "", "appId": APP_ID,
               "id": str(bean_id), "ext": ""}
    signed.update(payload)
    return f"{SAAD}/rewarded-video/report?app_id={app_id}&" + urllib.parse.urlencode(signed)


def call(cred, bean_id, position="VIDEO_PAYTASK", extra=None, app_id=APP_ID, timeout=15):
    url = build_url(cred, bean_id, position, extra, app_id)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
        ms = round((time.time() - t0) * 1000)
        return json.loads(body), ms
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", "ignore")[:100]}, 0
    except Exception as e:
        return {"code": "ERR", "msg": f"{type(e).__name__}: {e}"}, 0


def fmt(j):
    code = j.get("code")
    msg = j.get("msg") or ""
    b = j.get("body") or {}
    if code == 0:
        return (f"✅ code=0  {msg!r}  giftType={b.get('giftType')!r} "
                f"amount={b.get('giftAmount')} noAdTime={b.get('noAdTimeAmount')} "
                f"remainView={b.get('remainViewCount')}")
    return f"❌ code={code}  {msg!r}"


def main():
    ap = argparse.ArgumentParser(description="rewarded-video/report 复现工具")
    ap.add_argument("--probe", action="store_true", help="探测已知有效 id")
    ap.add_argument("--claim", metavar="ID", help="领取指定 id 的奖励")
    ap.add_argument("--scan", nargs=2, metavar=("LO", "HI"), help="扫描 id 区间")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--position", default="VIDEO_PAYTASK")
    ap.add_argument("--delay", type=float, default=0.9)
    a = ap.parse_args()

    cfg = zconfig.load("config.yaml")
    phone, pwd = zconfig.resolve_credentials(cfg, None, None)
    cred = auth.login(phone, pwd)
    print(f"usr={cred.usr}\n")

    if a.claim:
        j, ms = call(cred, a.claim, a.position)
        print(f"id={a.claim} pos={a.position} {ms}ms")
        print(" ", fmt(j))
        if j.get("body"):
            print("  body:", json.dumps(j["body"], ensure_ascii=False))
        return

    if a.scan:
        lo, hi = int(a.scan[0]), int(a.scan[1])
        print(f"扫描 {lo}-{hi} step={a.step} pos={a.position}\n")
        stats = {}
        goods = []
        for fid in range(lo, hi + 1, a.step):
            j, _ = call(cred, str(fid), a.position)
            c = j.get("code")
            b = j.get("body") or {}
            gt = b.get("giftType") or ""
            key = f"{c}|{gt}"
            stats[key] = stats.get(key, 0) + 1
            if c == 0:
                noad = b.get("noAdTimeAmount")
                amt = b.get("giftAmount")
                goods.append((fid, gt, amt, noad))
                print(f"  ✅ {fid} gift={gt!r} amt={amt} noAd={noad}")
            time.sleep(a.delay)
        print("\n分类统计:")
        for k, v in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {k:22s} × {v}")
        if goods:
            types = sorted(set(g[1] for g in goods))
            print(f"\n出现的 giftType: {types}")
            if "adless" in types and len(types) == 1:
                print("  → 全是 adless（免广告时长），本区间没有代金券奖励")
        return

    # 默认 probe
    print("探测已验证的有效 id（每个会真的发奖，谨慎使用）:")
    print("  提示：--claim 指定单个更省事\n")
    for fid, noad in sorted(KNOWN_GOOD.items(), key=lambda x: -x[1])[:5]:
        j, ms = call(cred, fid, a.position)
        print(f"  id={fid:8s} 预期noAd={noad:4d}  {ms:>4}ms  {fmt(j)}")
        time.sleep(a.delay)


if __name__ == "__main__":
    main()