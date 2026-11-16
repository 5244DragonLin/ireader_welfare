#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""摸清 rewarded-video/report 的 id 空间结构。

已知（2026-11-16）：
  · 代金券（voucher）集中在 10006x-10008x
  · 免广告（adless）在 1010xx / 1015xx
  · 大量 id 返回 10013（状态不符）或 2（不存在）或 5（param fail）

本脚本按区间粗扫，统计每段里 voucher / adless / 10013 / 2 的占比，
找出所有代金券聚集的区段，再对候选段细扫。

⚠️ 注意：探测本身会**真的发奖并消耗次数池**。
   代金券 id 一旦命中就会被领掉，所以「探测」= 「领取」。
   如果只想摸底不想消耗，用 --only-dead 只打明显无效的段。

用法:
    python src/id_space_survey.py --segment 100000 100200    # 粗扫一段
    python src/id_space_survey.py --voucher-hunt             # 找代金券聚集段
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "src")
from ireader_welfare import config as zconfig, auth  # noqa: E402
from reward_video_api import call  # noqa: E402

# 响应归类
CATS = {
    0: "ok",
    2: "notexist",
    5: "paramfail",
    10013: "mismatch",
    33709: "exhausted",
    33711: "cooldown",
}


def cat_of(code):
    return CATS.get(code, f"other({code})")


def survey(cred, lo, hi, step=1, delay=0.45, verbose_codes=False):
    """扫一段，返回统计与命中列表。"""
    stats = {}
    vouchers = []
    adless = []
    for fid in range(lo, hi + 1, step):
        j, _ = call(cred, str(fid))
        code = j.get("code")
        c = cat_of(code)
        stats[c] = stats.get(c, 0) + 1
        if code == 0:
            b = j.get("body") or {}
            gt = b.get("giftType")
            row = (fid, b.get("giftAmount"), b.get("noAdTimeAmount"),
                   b.get("remainViewCount"))
            if gt == "voucher":
                vouchers.append(row)
                print(f"  ★VOUCHER {fid}: {b.get('giftAmount')}券 "
                      f"remain={b.get('remainViewCount')}")
            elif gt == "adless":
                adless.append(row)
                if verbose_codes:
                    print(f"   adless  {fid}: {b.get('noAdTimeAmount')}s "
                          f"remain={b.get('remainViewCount')}")
        elif verbose_codes:
            print(f"   {c:9s} {fid}: {j.get('msg')}")
        time.sleep(delay)
    return stats, vouchers, adless


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--delay", type=float, default=0.45)
    ap.add_argument("--show-all", action="store_true", help="打印每一行")
    ap.add_argument("--voucher-hunt", action="store_true",
                    help="粗扫多段找代金券聚集区")
    a = ap.parse_args()

    cfg = zconfig.load("config.yaml")
    phone, pwd = zconfig.resolve_credentials(cfg, None, None)
    cred = auth.login(phone, pwd)
    print(f"usr={cred.usr}")
    print("⚠️  探测会真实领取，谨慎操作\n")

    if a.voucher_hunt:
        # 粗扫：每 5 个取一个，看哪段有 voucher
        segments = [(100040, 100120), (100120, 100200), (101600, 101700),
                    (101700, 101800), (102000, 102100)]
        for lo, hi in segments:
            print(f"\n=== 粗扫 {lo}-{hi} (step=5) ===")
            stats, vs, ads = survey(cred, lo, hi, step=5, delay=0.4)
            print(f"  统计: {stats}")
            print(f"  voucher: {len(vs)}  adless: {len(ads)}")
            if vs:
                print(f"  ⭐ 代金券段找到: {[v[0] for v in vs]}")
            time.sleep(1)
        return

    if not a.segment:
        ap.print_help()
        return

    lo, hi = int(a.segment[0]), int(a.segment[1])
    print(f"=== 扫 {lo}-{hi} step={a.step} ===")
    stats, vs, ads = survey(cred, lo, hi, a.step, a.delay,
                            verbose_codes=a.show_all)
    print(f"\n统计: {json.dumps(stats, ensure_ascii=False)}")
    print(f"voucher 命中 {len(vs)}: {vs}")
    print(f"adless  命中 {len(ads)}: {len(ads)} 个")


if __name__ == "__main__":
    main()