#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命令行入口：签到、抽奖、阅读领奖、宝箱与任务状态。"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time

from . import auth, config, welfare
from .welfare import (
    CODE_ALREADY,
    CODE_NO_WHEEL,
    CODE_OK,
    CODE_VIDEO_NO_CHANCE,
    WelfareClient,
)


# ---------------------------- 展示 ----------------------------
def _usable_vouchers(c: WelfareClient):
    """可用代金券（未使用、未过期），按到期日升序；接口失败返回 None。"""
    try:
        items = c.vouchers()
    except Exception:
        return None
    today = dt.date.today()
    usable = []
    for v in items:
        if v["used"] or not v["expire"]:
            continue
        try:
            exp = dt.date.fromisoformat(v["expire"])
        except ValueError:
            continue
        if exp >= today:
            usable.append({**v, "days_left": (exp - today).days})
    usable.sort(key=lambda v: (v["expire"], -v["amount"]))
    return usable


def _fmt_amount(n) -> str:
    return str(int(n)) if float(n) == int(n) else str(n)


def show_vouchers(c: WelfareClient):
    """代金券账面：可用总额 + 最早过期的几张。

    阅饼有效期通常只有几天，不专门提醒很容易看着余额过期作废。
    展示规则：
      - 只统计「未使用且未过期」的券，按有效期升序，最早到期的排最前
      - 三天内到期的标 [!];  当天到期的标 [!!]
      - 接口失败不阻塞主流程，只显示一行提示
    """
    usable = _usable_vouchers(c)
    if usable is None:
        print("  代金券余额获取失败")
        return

    total = sum(v["amount"] for v in usable)
    if not usable:
        print("  代金券   : 暂无可用余额")
        return
    print(f"  代金券   : {_fmt_amount(total)} 可用")

    # 到期提醒：列出三天内到期的；若一张都没有，再带出最早到期的两张作参考
    expiring = [v for v in usable if v["days_left"] <= 3]
    shown = expiring[:5] if expiring else usable[:2]
    head = "三天内到期" if expiring else "最早到期"
    for v in shown:
        flag = f"[{'!!' if v['days_left'] == 0 else '!'}] " if v["days_left"] <= 3 else ""
        print(f"    {flag}{v['amount']:g}  {v['expire']} 到期"
              f"  ({v['days_left']} 天后)  {v['source'] or ''}".rstrip())
    if expiring:
        n = sum(v["amount"] for v in expiring)
        print(f"    —— {head} 共 {len(expiring)} 张 / {_fmt_amount(n)}，别忘花掉 ——")


def show_summary(c: WelfareClient):
    """run 系列命令结尾的紧凑账面（2~3 行）。

    只保留「钱还剩多少、什么时候过期、还剩几次机会」这三件每天关心的事；
    完整状态（签到进度、宝箱差值等）用 status 命令或 --verbose 查看。
    """
    print("\n—— 账面 ——")

    usable = _usable_vouchers(c)
    if usable:
        total = sum(v["amount"] for v in usable)
        expiring = [v for v in usable if v["days_left"] <= 3]
        first = (expiring or usable)[0]
        tag = "[!]" if first["days_left"] <= 3 else "最早"
        print(f"  代金券 {_fmt_amount(total)} 可用｜{tag} {_fmt_amount(first['amount'])} "
              f"于 {first['expire']} 到期（{first['days_left']} 天后）")
        if len(expiring) > 1:
            rest = sum(v["amount"] for v in expiring) - first["amount"]
            print(f"  另有 {len(expiring) - 1} 张三天内到期，共 {_fmt_amount(rest)} —— 别忘花掉")
    elif usable is not None:
        print("  代金券 0 可用")
    else:
        print("  代金券余额获取失败")

    wheel = "?"
    try:
        wheel = c.wheel_chances()
    except Exception:
        pass
    lottery = "?"
    try:
        lt = c.read_lottery_state()
        lottery = (lt.get("drawTimes") or 0) if lt.get("isLogin") else "?"
    except Exception:
        pass
    coupon_n = 0
    try:
        coupon_n = len([x for x in c.coupons() if not x["expired"]])
    except Exception:
        pass
    print(f"  转盘 {wheel} 次｜阅读抽奖 {lottery} 次｜满减券 {coupon_n} 张")


def show_status(c: WelfareClient):
    page = c.sign_page().get("body") or {}
    user = page.get("user") or {}
    state = c.sign_state()
    week = state.get("week_info") or []
    daily = state.get("daily_reward_detail") or {}
    weekly = state.get("week_reward_detail") or {}

    print("\n—— 账号 ——")
    print(f"  昵称     : {user.get('nick')}  (Lv{user.get('level')})")
    print(f"  账号     : {user.get('name')}")
    print(f"  今日签到 : {'已签到' if state.get('is_sign') else '未签到'}"
          f"  (本周第 {state.get('week_day')} 天 / 活动 {state.get('act_id')})")
    if daily.get("name") or weekly.get("name"):
        print(f"  签到奖励 : 每日 {daily.get('name') or '-'}　连续7天 {weekly.get('name') or '-'}")

    if week:
        bar = "".join(
            "■" if w.get("status") == 1 else ("□" if w.get("is_today") else "·") for w in week
        )
        print(f"  本周进度 : {bar}   (■已签 □今天 ·未到)")

    # 等级经验：成长任务每天给经验，攒够才升级（升级发代金券/VIP）。
    # 这套数据和福利中心分开，接口偶发失败不影响主面板，静默跳过。
    try:
        lv = c.level_state()
        info = lv.get("userInfo") or {}
        if info:
            cur, need = int(info.get("currentExp") or 0), int(info.get("exp") or 0)
            gap = max(0, need - cur)
            print(f"  等级经验 : {cur}/{need}  (Lv{info.get('level')}，还差 {gap} 升级)")
            todo = [t for t in (lv.get("taskInfo") or []) if t.get("status") == 0]
            if todo:
                print(f"    经验任务 : {'、'.join(t.get('name') or '' for t in todo)}")
    except Exception:
        pass

    show_vouchers(c)
    box = c.box_state()
    print(f"  本周活跃值: {box['score']}")
    for ch in box["chests"]:
        if ch["got"]:
            tag = "已开"
        elif ch["able"]:
            tag = "可开"
        else:
            tag = f"差 {max(0, ch['need'] - box['score'])}"
        print(f"    宝箱#{ch['index']} 需 {ch['need']:3} → {ch['gift']:8} {tag}")

    print(f"  转盘机会 : {c.wheel_chances()} 次")

    # 阅读时长抽奖是独立活动（每天最多 4 次），不显示出来很容易漏抽
    try:
        lt = c.read_lottery_state()
        if lt.get("isLogin"):
            print(f"  阅读抽奖 : {lt.get('drawTimes') or 0} 次")
    except Exception:
        pass

    # 转盘常抽到「满减优惠券」，它记在 /zyuc/coupon 而不是代金券列表里，
    # 不单独列出来容易以为奖品没到账。
    try:
        cs = [x for x in c.coupons() if not x["expired"]]
    except Exception:
        cs = []
    # 淘书市的券（名字带「淘书市」，如 180淘书市-24）单独一组，方便对账
    tst = [x for x in cs if "淘书市" in x["name"]]
    cs = [x for x in cs if "淘书市" not in x["name"]]
    if cs:
        print(f"  满减券   : {len(cs)} 张可用")
        for x in cs:
            print(f"    {x['name']}  {x['valid']}")
    if tst:
        print(f"  淘书市券 : {len(tst)} 张可用")
        for x in tst:
            print(f"    {x['name']}  {x['valid']}")


def show_cross_app_tasks(c: WelfareClient):
    """显示跨 App 换量任务（如「打开携程做任务领 +5 满减券」）。

    这条任务纯 HTTP 做不完，原因不是参数没猜对，而是完成信号由**对方 App**
    回跳时产生——服务端在那一刻往会话里写 `cooperation_source`，而没有任何
    接口允许客户端自己设置它（实测见 docs 逆向笔记十八节）。

    但它会**间歇性下发**，且默认渠道号看不到。不显式列出来，用户会以为
    「任务不存在」，或者在 App 里手动做完却不知道为什么工具没报。

    所以这里的职责只是：看见了就诚实说「看见了，但做不了」，并说清怎么手动做。
    """
    try:
        items = c.cross_app_tasks()
    except Exception:
        return          # 换量任务查询失败不阻塞主面板
    if not items:
        return

    for t in items:
        status = t.get("status")
        if status == 2:
            print(f"\n  跨App任务: {t['name']} — 已领取")
            continue
        if status == 1:
            print(f"\n  跨App任务: {t['name']} — 已完成，待领取（跑 tasks 领奖）")
            continue
        # 未完成：这条只能手动做
        print(f"\n  跨App任务: {t['name']}  {t.get('award') or ''}")
        print("    需在 App 里手动完成：点「去完成」→ 跳转到对方 App → 按对方要求操作")
        print("    本工具不做这条（完成信号由对方 App 回跳产生，纯 HTTP 无法构造）")


def show_tasks(c: WelfareClient):
    # 看视频
    # 注意：/zycl/welfare/video/status 对本工具的会话可能恒返 53002（已用完），
    # 与 H5 实际状态不一致，所以这里同时显示服务端下发的广告策略作为佐证。
    vs = c.video_status("VIDEO_PAYTASK")
    vb = vs.get("body") or {}
    if vs.get("code") == CODE_OK and vb.get("show"):
        print(f"  看视频   : 可看，奖励 {vb.get('amountTotal')} 代金券")
    elif vs.get("code") == CODE_VIDEO_NO_CHANCE:
        print(f"  看视频   : 未开放（{vs.get('msg')}）")
    else:
        print(f"  看视频   : {vs.get('msg')}")

    # 广告策略（/ad/cfg）：能拿到说明服务端已识别本客户端，可看到真实投放源
    try:
        from .adcfg import fetch_rules, slot_strategies
        rules = fetch_rules(c.cred)
        if rules:
            st = slot_strategies(rules, "VIDEO_PAYTASK")
            srcs = [x.get("source") for x in st if x.get("source")]
            print(f"  视频投放 : {len(st)} 个源  ({', '.join(srcs)})" if st
                  else "  视频投放 : 策略里无视频位")
        else:
            print("  视频投放 : 策略未下发")
    except Exception:
        pass

    # 分享日签
    # 注意：/welfare/sign/today/share 的 status 是黄历「宜/忌」标记，
    # 不能用来判断任务是否完成；完成状态要从日常任务列表里取。
    st_task = c.share_task()
    if st_task:
        if st_task.get("status") == 2:
            print("  分享日签 : 已领取")
        elif st_task.get("status") == 1:
            print("  分享日签 : 已完成，待领取（跑 tasks 领奖）")
        else:
            print("  分享日签 : 未完成　→ 跑 tasks 自动补一次分享上报")
    else:
        print("  分享日签 : 服务端未下发该任务")

    # 日常任务
    tl = c.tasks().get("body")
    if isinstance(tl, list) and tl:
        print(f"\n—— 日常任务（{len(tl)} 项）——")
        for t in tl:
            print(f"  [{t.get('status')}] {t.get('name')}  {t.get('voucher_amount') or ''}")
    else:
        print("\n—— 日常任务 ——")
        print("  服务端未下发任务列表")

    show_cross_app_tasks(c)

    # 阅读任务
    rl = (c.read_tasks().get("body") or {}).get("list") or []
    if rl:
        # status: 0 未达标 / 1 可领 / 2 已领取。
        # 只按 current>=quota 判断会把「已领取」误报成「可领」，必须看 status。
        claimable = sum(1 for t in rl if t.get("status") == 1)
        total = sum(int((t.get("gift") or {}).get("amount") or 0) for t in rl)
        print(f"\n—— 阅读赚代金券（{len(rl)} 档，全领 {total}）——")
        for t in rl:
            cur, quota = t.get("current") or 0, t.get("quota") or 0
            if t.get("status") == 2:
                mark = "已领取"
            elif t.get("status") == 1 or cur >= quota > 0:
                mark = "可领"
            else:
                mark = f"差 {max(0, quota - cur)} 分钟"
            print(f"  {t.get('name'):12} {cur:3}/{quota:<3} 分钟  {mark}")
        if claimable:
            print(f"  其中 {claimable} 档待领取（跑 read 领奖）")


# ---------------------------- 动作 ----------------------------
def do_sign(c: WelfareClient, verbose: bool = False) -> bool:
    r = c.reset_sign_flow()
    if r["already"]:
        if verbose:
            print("\n签到: 今日已签到")
        return True
    if not r["signed"]:
        print("\n签到: 失败（状态未变化）")
        return False

    reward = r.get("reward") or {}
    if reward.get("code") == CODE_OK:
        print(f"\n签到: 成功，奖励已领（{reward.get('msg') or ''}）".rstrip())
    elif reward.get("code") == CODE_ALREADY:
        print("\n签到: 成功（奖励今日已领过）")
    else:
        print("\n签到: 成功")
        if reward and verbose:
            print(f"签到奖励: {reward.get('msg')}")
    return True


def do_wheel(c: WelfareClient, max_times: int = 10, verbose: bool = False) -> bool:
    """抽奖，把当前持有的次数一次性抽完。

    翻卡机会（draw_times）会由日常任务奖励发放，例如
    「阅读品质好书 满5分钟」给 +1 次；领完任务再来抽，次数才会被用掉。
    因此本函数循环抽到次数为 0 为止，不是只抽一次。
    """
    act_id = c.wheel_act_id()
    times = c.wheel_chances()
    if not verbose:
        print()  # 与上一块输出空一行
    if verbose:
        print(f"\n转盘: act_id={act_id} 剩余 {times} 次")
    if not act_id or times <= 0:
        if verbose:
            print("转盘: 无可用次数")
        return False

    drawn, gained = 0, []
    for _ in range(min(times, max_times)):
        r = c.wheel_draw(act_id)
        if r.get("code") == CODE_OK:
            gift = r.get("gift") or {}
            name = gift.get("desc") or gift.get("gift_type") or ""
            left = r.get("draw_times")
            if left is None:
                left = c.wheel_chances()
            drawn += 1
            gained.append(name)
            if verbose:
                print(f"  第 {drawn} 次: 抽中{f'「{name}」' if name else '（奖品已入账）'}"
                      f"　剩余 {left} 次")
            if left is not None and left <= 0:
                break
        elif r.get("code") == CODE_NO_WHEEL:
            break
        else:
            print(f"\n转盘: 抽奖失败: {r.get('msg') or json.dumps(r, ensure_ascii=False)[:150]}")
            break

    if drawn:
        left = c.wheel_chances()
        prizes = "、".join(f"「{n}」" if n else "（奖品已入账）" for n in gained)
        if verbose:
            print(f"转盘: 共抽 {drawn} 次，剩余 {left} 次")
            if left > 0:
                print("      还有次数没抽完：翻卡机会可能刚由任务发放，再跑一次 wheel 即可")
        else:
            tail = f"；还有 {left} 次未抽完，可再跑一次 wheel" if left > 0 else ""
            print(f"转盘: {drawn} 次抽完 → {prizes}{tail}")
    return drawn > 0


def do_read_lottery(c: WelfareClient, verbose: bool = False) -> bool:
    """阅读时长抽奖：每天最多 4 次，由阅读时长里程碑（30/60/120/180 分钟）解锁。

    这是福利页「去抽奖」跳转的那个页面（活动号 16416），
    **不是**大转盘（12516）——两者奖品池和次数完全独立，
    很容易只抽了转盘就以为抽完了。
    """
    st = c.read_lottery_state()
    if not st:
        print("\n阅读时长抽奖: 取不到页面状态")
        return False
    if not st.get("isLogin"):
        print("\n阅读时长抽奖: 页面判定未登录，检查 usr/zysid")
        return False

    # 里程碑机会不会自动到账，先领一次
    ca = c.read_lottery_claim_awards()
    if ca.get("code") == CODE_OK:
        body = ca.get("body") or {}
        st = c.read_lottery_state()

    times = int(st.get("drawTimes") or 0)
    if verbose:
        if ca.get("code") == CODE_OK:
            print(f"\n阅读时长抽奖: 里程碑机会已领取，当前 {times} 次")
        else:
            print(f"\n阅读时长抽奖: 里程碑无需再领（{ca.get('msg') or '无返回'}）")
        print(f"阅读时长抽奖: 活动 {st.get('actSign')} 剩余 {times} 次")
    if times <= 0:
        if verbose:
            print("  今日次数已用完（阅读满 30/60/120/180 分钟各给 1 次）")
        return False

    res = c.read_lottery_draw_all(claim_first=False)
    gained = []
    for i, x in enumerate(res, 1):
        if x["ok"]:
            gained.append(x["name"])
            if verbose:
                print(f"  第 {i} 次: 抽中「{x['name']}」　剩余 {x['left']} 次")
        else:
            print(f"\n阅读时长抽奖: 第 {i} 次失败 code={x['code']} {x['msg'] or ''}")
            break
    if gained:
        if verbose:
            print(f"阅读时长抽奖: 共抽 {len(gained)} 次（{('、'.join(gained))}）")
            left = int(c.read_lottery_state().get("drawTimes") or 0)
            print(f"      剩余 {left} 次")
        else:
            amounts = []
            for n in gained:
                m = re.search(r"(\d+)", n)
                amounts.append(m.group(1) if m else "?")
            print(f"阅读抽奖: {len(gained)} 次抽完 → {'+'.join(amounts)} 代金券")
    return bool(gained)


def do_read(c: WelfareClient, verbose: bool = False) -> bool:
    done = c.claim_read_all()
    if not done:
        if verbose:
            print("\n阅读任务: 无可领取档位（需在 App 内累计阅读时长）")
        return False

    gained = 0
    ok_n = 0
    fails = []
    for d in done:
        code = d["resp"].get("code")
        if code == CODE_OK:
            gained += d["amount"]
            ok_n += 1
        else:
            fails.append(d)
    if verbose:
        print("\n阅读任务:")
        for d in done:
            if d["resp"].get("code") == CODE_OK:
                print(f"  领取成功 {d['name']} (+{d['amount']})")
            else:
                print(f"  领取失败 {d['name']}: {d['resp'].get('msg')}")
        print(f"  本轮共领 {gained} 代金券")
    elif fails:
        print("\n阅读任务:")
        for d in fails:
            print(f"  领取失败 {d['name']}: {d['resp'].get('msg')}")
        print(f"  成功 {ok_n} 档共 {gained} 代金券")
    else:
        print(f"\n阅读: {ok_n}/{len(done)} 档全领，共 {gained} 代金券")
    return gained > 0


def do_level_tasks(c: WelfareClient, verbose: bool = False) -> bool:
    """等级成长任务：把「浏览书城超过20秒」这类回执型任务走完并领经验。

    与福利中心的日常任务无关——这是「我的 → Lv 勋章 → 我的等级」那套，
    奖励是经验值（升级才发代金券/VIP），接口在 /zyuc/api/user/ 下、
    签名规则也不同（详见 docs/掌阅逆向笔记.md 十七节）。
    """
    try:
        out = c.upgrade_tasks_flow()
    except Exception as e:
        if verbose:
            print(f"\n等级任务: 调用失败（{e}）")
        return False

    if not out["tasks"]:
        if verbose:
            print("\n等级任务: 今日经验任务已全部完成（服务端不再下发）")
        return False

    reported_ok = [r for r in out["reported"] if r["ok"]]
    failed = [r for r in out["reported"] if not r["ok"]] + \
             [r for r in out["claimed"] if not r["ok"]]
    # 上报名义上成功、但服务端迟迟不翻「待领取」，经验其实还没到账。
    # 这类必须显式喊出来，否则会像 2026-11-16 那样静默漏掉一整天的经验。
    pending = out.get("pending") or []

    if verbose:
        print("\n等级任务:")
        for r in out["reported"]:
            flag = "上报成功" if r["ok"] else "上报失败"
            print(f"  {flag} {r['name']}" + ("" if r["ok"] else f": {r['msg']}"))
        for r in out["claimed"]:
            if r["ok"]:
                print(f"  领取成功 {r['name']}  +{r['gained']} 经验")
            else:
                print(f"  领取失败 {r['name']}: {r['msg']}")
        for r in pending:
            print(f"  待领取 {r['name']}（服务端未翻状态，稍后跑 `level` 可补领）")
        for t in out["tasks"]:
            if t["status"] == 0 and "浏览书城" not in (t["name"] or ""):
                print(f"  未自动化 {t['name']}（{t['status']}）")
        return bool(out["claimed"])

    if out["claimed"] or reported_ok:
        bits = []
        if reported_ok:
            bits.append("、".join(r["name"] for r in reported_ok))
        if out["claimed"]:
            bits.append(f"领 {out['gained']} 经验")
        line = "等级任务: " + "；".join(bits)
        if failed:
            line += f"（{len(failed)} 项失败）"
        print("\n" + line)
        if pending:
            print("  [!] 已上报但未领到：" +
                  "、".join(r["name"] for r in pending) +
                  "，稍后跑 `level` 补领")
    elif failed:
        # 上报全失败时也要出声，否则精简模式看不出今天经验没拿到
        print("\n等级任务: " + "；".join(
            f"{r['name']} 失败({r.get('msg')})" for r in out["reported"] if not r["ok"]))
    return bool(out["claimed"])


VIDEO_REWARD_IDS = ["100063", "100065", "100078", "100079", "100064", "100099"]
VIDEO_CD_SECONDS = 20   # 实测同 id 冷却约 20s（不同 id 互不影响）


def do_video_reward(c: WelfareClient, verbose: bool = False,
                    max_seconds: int = 0) -> float:
    """领取「看视频送代金券」奖励（纯 HTTP，不需要真的看广告）。

    2026-11-16 打通，详见 docs/掌阅逆向笔记.md 16.12：
      端点  GET {saad}/rewarded-video/report?app_id=10000
      签名  usr + timestamp + position（position 必须进签名）
      id    决定发什么奖：10006x-10008x 段是代金券，1015xx 段是免广告时长
      限制  每个 id 各有独立次数池 + 各自 20 秒冷却（不是全局冷却）

    策略：**每个 id 各自领到 33709 为止**，不是固定轮数。
    实测 38 笔 / 356 券 / 26 分钟，相邻间隔最小 2 秒（轮换 id 实现的）。
    """
    from .reward import claim_vouchers

    def balance():
        vs = c.vouchers()
        return round(sum(v["amount"] for v in vs if not v.get("used")), 1)

    t0 = balance()
    if verbose:
        print(f"\n看视频领代金券（纯 HTTP，每个 id 领到耗尽为止）")
        print(f"  起始未用代金券: {t0}")

    # 精简模式只打汇总（一行），详细模式打逐笔
    gained, ok, log = claim_vouchers(
        c.cred, VIDEO_REWARD_IDS,
        delay=VIDEO_CD_SECONDS, verbose=verbose,
        max_seconds=max_seconds)

    time.sleep(3)
    t1 = balance()
    delta = round(t1 - t0, 1)
    if verbose:
        print(f"  余额: {t0} → {t1}（实测入账 {delta} 券）")
    else:
        # 一行说清：领了几个 id、共多少券
        if delta > 0:
            print(f"看视频: {ok} 次入账 {delta} 代金券（余额 {t0} → {t1}）")
        elif ok:
            print(f"看视频: {ok} 次成功但未增券（余额 {t0}）")
        else:
            print(f"看视频: 暂无可领（额度已用完，明天再来）")
    return delta


def do_taoshushi(c: WelfareClient, verbose: bool = False) -> bool:
    """领淘书市活动的满减券（纯 HTTP，不需要打开 App）。

    活动入口是书城「出版」频道顶部的滚动 banner（约一月换一次期），
    里面挂的运营活动页（h5.zhangyue.com/opr/...）每期发 3~4 张满减券。
    2026-11-17 打通，详见 docs/掌阅逆向笔记.md 二十一节。

    没换期时滚动栏里没有活动页链接，此时安静跳过，不算失败。
    """
    from .taoshushi import claim_all

    try:
        out = claim_all(c.cred, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"\n淘书市: 调用失败（{e}）")
        return False

    if not out["found"]:
        return False

    # 精简模式一行汇总（verbose 已逐张打印过）
    if not verbose and out["items"]:
        n = out["claimed"]
        amt = out["total_amount"]
        if n and amt:
            print(f"淘书市: 领 {n} 张满减券，共 {amt:g}（{out['already']} 张已领过）")
        elif n:
            print(f"淘书市: 领 {n} 张满减券（{out['already']} 张已领过）")
        elif out["already"]:
            print(f"淘书市: {out['already']} 张均已领过")
        elif out["failed"]:
            print(f"淘书市: {out['failed']} 张领取失败")
    elif not verbose:
        print("淘书市: 本期无券可领")
    return bool(out["claimed"] or out["already"])


def do_box(c: WelfareClient, verbose: bool = False) -> bool:
    st = c.box_state()
    ready = [ch for ch in st["chests"] if ch["able"] and not ch["got"]]
    if verbose:
        print(f"\n宝箱: 本周活跃值 {st['score']}")
    if not ready:
        if st["next"]:
            if verbose:
                print(f"  暂无可开宝箱，距下一档还需 {st['next_gap']} 活跃值")
        elif verbose:
            print("  本周宝箱已全部开启")
        return False
    ok = 0
    for ch in ready:
        r = c.open_box(ch["index"])
        if r.get("status") == 0:
            ok += 1
            print(f"宝箱: 开启成功 #{ch['index']}: {ch['gift']}")
        else:
            print(f"宝箱: 开启失败 #{ch['index']}: {r.get('message')}")
    return ok > 0


# ---------------------------- 入口 ----------------------------
def do_report_read_time(c: WelfareClient, minutes: int = 180,
                        verbose: bool = False) -> bool:
    """先铺阅读时长（PC 网页版接口），让后面的领奖有东西可领。

    这是「阅读赚代金券」10 档的计数来源。必须在 do_read 之前跑：
    分钟数不到，do_read 就没有可领的档位。用 c.usr / c.zysid 直接报，
    不需要再登录一次。
    """
    from . import reading

    if verbose:
        print(f"\n阅读时长: 上报 {minutes} 分钟 ...")
    try:
        out = reading.report_minutes(c.usr, c.zysid, reading.DEFAULT_BOOK_ID,
                                     minutes, step=10, verbose=verbose)
    except Exception as e:
        print(f"\n阅读时长: 上报失败（{e}）—— 跳过，继续领奖")
        return False
    if not out:
        # 没有可铺的新时段：要么今天已铺满，要么当前时刻距零点不足。
        # 后者是常见的「没铺满」原因（例如把任务排在每天 00:30，全天只过了 30 分钟），
        # 静默会让人误以为上报出了问题，所以这里明确提示。
        elapsed = time.localtime().tm_hour * 60 + time.localtime().tm_min
        if elapsed < minutes:
            print(f"\n阅读时长: 今天只过了 {elapsed} 分钟，"
                  f"距目标 {minutes} 分钟还差 {minutes - elapsed} 分钟——稍晚再跑")
        else:
            print(f"\n阅读时长: 今天可铺时段已全部上报过（目标 {minutes} 分钟），无需再铺")
        return False
    ok = sum(1 for x in out if x["ok"])
    if verbose:
        print(f"阅读时长: 完成 {ok}/{len(out)} 段")
        return ok > 0
    mins = sum((x["to"] - x["from"]) // 60000 for x in out if x["ok"])
    tail = "" if ok == len(out) else f"（{len(out) - ok} 段失败）"
    print(f"\n阅读时长: 上报 {mins} 分钟，{ok}/{len(out)} 段成功{tail}")
    if mins < minutes:
        # 报不满有两个原因，必须区分开，否则用户会以为「等一会儿还能补」：
        #   ① 跑得太早：今天从零点到现在不足目标分钟数
        #   ② 额度已用尽：白天反复跑，可铺的空闲时段被用光了
        elapsed = time.localtime().tm_hour * 60 + time.localtime().tm_min
        if elapsed < minutes:
            print(f"  [!] 未达目标：今天只过了 {elapsed} 分钟，"
                  f"稍晚再跑可补齐剩余 {minutes - mins} 分钟")
        else:
            print(f"  [!] 未达目标：今天可铺的空闲时段已用尽"
                  f"（零点至今 {elapsed} 分钟，已报时段占了大部分）。"
                  f"这是反复执行导致的，明天重置后可正常铺满")
    return ok > 0


def do_tasks(c: WelfareClient, verbose: bool = False) -> bool:
    """领取日常任务奖励；分享日签与品质好书任务未完成时自动补上报。

    分享日签：调 share_daily_flow() 纯 HTTP 上报，服务端即标记完成
    （2026-11-14 实测，status 0 → 2 且奖励直接入账），全程不需要打开 App。
    品质好书（阅读满5分钟 / 收听满10分钟）：调 quality_task_flow()，
    曝光书单页入口 + read/time 铺时长（2026-11-16 实测可翻转）。
    「打开掌阅APP」的完成信号在 App 本地，不自动化。
    领奖统一走 claim_daily_tasks。
    """
    # 分享日签：未完成就补一次真实上报
    share = c.share_daily_flow()
    if share["reported"] and verbose:
        print(f"\n分享日签: 已上报分享，状态 {share['status_before']} → {share['status_after']}")
    elif share["msg"] and verbose:
        print(f"\n分享日签: {share['msg']}")

    # 品质好书：阅读满5分钟（res=R）+ 收听满10分钟（res=L）
    for kw, mins, res in (("阅读品质好书", 5, "R"), ("收听品质好书", 10, "L")):
        try:
            q = c.quality_task_flow(kw, minutes=mins, res=res)
        except Exception as e:
            if verbose:
                print(f"\n品质好书({kw}): 调用失败（{e}）")
            continue
        if not verbose:
            continue
        t = q.get("task")
        if not t:
            continue
        if q.get("ok"):
            print(f"\n品质好书: {t.get('name')} → 已达标"
                  f"（{t.get('current')}/{t.get('quota')}），可领奖")
        else:
            print(f"\n品质好书: {t.get('name')} → {q.get('msg')}")

    tasks = c.tasks().get("body") or []
    claimable = [t for t in tasks if t.get("status") == 1]
    pending = [t for t in tasks if t.get("status") == 0]

    # 换量任务不在默认渠道列表里，单独查一次：看得见才谈得上领奖
    try:
        cross = c.cross_app_tasks()
    except Exception:
        cross = []
    cross_claimable = [t for t in cross if t.get("status") == 1]
    cross_pending = [t for t in cross if t.get("status") == 0]

    if verbose:
        print("\n日常任务:")
        if claimable or cross_claimable:
            for r in c.claim_daily_tasks():
                if r["ok"]:
                    print(f"  领取成功 {r['name']}  {r['award']}")
                else:
                    print(f"  领取失败 {r['name']}: {r['msg']}")
        else:
            print("  暂无可领取的任务")
        for t in pending:
            target = (t.get("target_list") or [{}])[0]
            print(f"  未完成 {t['name']}  {t.get('award')}")
            if target.get("description"):
                print(f"         → {target['description']}")
        for t in cross_claimable:
            print(f"  换量任务待领 {t['name']}  {t.get('award')}（已补入上方领奖）")
        for t in cross_pending:
            print(f"  换量任务未完成 {t['name']}  {t.get('award')}"
                  f"（需手动去对方 App 完成）")
        return bool(claimable or cross_claimable)

    # 紧凑模式：领奖合并一行；未完成项里只标注真正需要 App 行为的那些。
    results = c.claim_daily_tasks() if (claimable or cross_claimable) else []
    ok_names = [f"{r['name']} {r['award']}".strip() for r in results if r["ok"]]
    fail_names = [f"{r['name']}: {r['msg']}" for r in results if not r["ok"]]

    dev_pending = [t for t in pending if t["name"] != "分享今日日签"]
    share_pending = [t for t in pending if t["name"] == "分享今日日签"]

    parts = []
    if ok_names:
        parts.append("领取 " + "；".join(ok_names))
    if share_pending:
        parts.append("分享日签仍未翻转（可能服务端延迟，稍后再跑一次 tasks）")
    if dev_pending:
        names = "、".join(t["name"].replace("品质好书 ", "").replace("今日", "")
                         for t in dev_pending)
        parts.append(f"{len(dev_pending)} 项待真机（{names}）")
    if cross_pending:
        names = "、".join(t["name"] for t in cross_pending)
        parts.append(f"{len(cross_pending)} 项换量任务需手动做（{names}）")
    if fail_names:
        parts.append("失败 " + "；".join(fail_names))
    if parts:
        print("\n任务: " + "；".join(parts))
    return bool(claimable)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="ireader-welfare",
        description="掌阅福利中心自动签到工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  ireader-welfare run                     铺时长 → 签到 → 领阅读奖 → 领任务 → 等级 → 转盘 → 阅读抽奖 → 开箱 → 看视频领券\n"
            "  ireader-welfare run --no-video          同上，但不领看视频代金券\n"
            "  ireader-welfare video                   只领看视频代金券（逐笔日志）\n"
            "  ireader-welfare status                  查看状态\n"
            "  ireader-welfare lottery                只抽阅读时长抽奖（每天 4 次）\n"
            "  ireader-welfare sign --no-wheel         只签到，不抽奖\n"
            "  ireader-welfare run --no-read-pc        跳过「铺阅读时长」这一步\n"
        ),
    )
    ap.add_argument("command", nargs="?", default="run",
                    choices=["status", "run", "sign", "wheel", "lottery",
                             "read", "box", "tasks", "level", "video",
                             "taoshushi"],
                    help="要执行的动作，默认 run")
    ap.add_argument("--phone", help="手机号（也可用环境变量 IREADER_PHONE 或配置文件）")
    ap.add_argument("--password", help="密码（也可用环境变量 IREADER_PWD 或配置文件）")
    ap.add_argument("--config", help="配置文件路径，默认向上查找 config.yaml")
    ap.add_argument("--no-wheel", action="store_true", help="跳过转盘抽奖")
    ap.add_argument("--no-lottery", action="store_true", help="跳过阅读时长抽奖")
    ap.add_argument("--no-read", action="store_true", help="跳过阅读任务领奖")
    ap.add_argument("--no-box", action="store_true", help="跳过宝箱开启")
    ap.add_argument("--no-tasks", action="store_true", help="跳过日常任务领奖")
    ap.add_argument("--no-level", action="store_true",
                    help="跳过等级成长任务（浏览书城得经验）")
    ap.add_argument("--no-video", action="store_true",
                    help="跳过看视频领券（默认 run 会领）")
    ap.add_argument("--no-taoshushi", action="store_true",
                    help="跳过淘书市满减券（默认 run 会领）")
    ap.add_argument("--video-max-seconds", type=int, default=0,
                    help="看视频领券的时长上限（秒），0=不限制（领到全部耗尽为止）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出状态（便于脚本调用）")
    ap.add_argument("--verbose", action="store_true",
                    help="完整逐行输出（含操作后全量状态）；默认精简为关键行")
    ap.add_argument("--no-read-pc", action="store_true",
                    help="跳过「先上报阅读时长」这一步（默认 run 会先铺时长再领奖）")
    ap.add_argument("--read-pc-minutes", type=int, default=180,
                    help="上报多少分钟阅读时长，默认 180（拿满 10 档）")
    ap.add_argument("--save", action="store_true", help="把凭证摘要保存到输出目录")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config.load(args.config)
    phone, password = config.resolve_credentials(cfg, args.phone, args.password)

    if not phone or not password:
        print("缺少账号信息。请用 --phone/--password，或设置环境变量 "
              "IREADER_PHONE / IREADER_PWD，或写入 config.yaml", file=sys.stderr)
        return 2

    if not args.verbose:
        print("登录中 ...", end="")
    try:
        cred = auth.login(phone, password)
    except Exception as e:
        print(f"\n登录失败: {e}", file=sys.stderr)
        return 1
    if args.verbose:
        print(f"登录成功: {cred.nick} ({cred.usr})")
    else:
        print(f" {cred.nick} ({cred.usr})")

    c = WelfareClient(cred)
    vb = args.verbose

    if args.command == "status":
        show_status(c)
        show_tasks(c)
        return 0

    if args.command == "video":
        do_video_reward(c, vb, max_seconds=args.video_max_seconds)
        return 0

    if args.command == "taoshushi":
        do_taoshushi(c, vb)
        return 0

    if args.command in ("run", "sign"):
        do_sign(c, vb)
    # 顺序有讲究：
    #   ① 先上报阅读时长（do_report_read_time）—— 没铺够，do_read 就无奖可领
    #   ② 再领阅读奖、领日常任务奖 —— 翻卡机会由这两处发放
    #   ③ 最后抽奖 —— 否则刚发的次数要等到下一次执行才用得上
    if args.command == "run" and not args.no_read_pc and not args.no_read:
        do_report_read_time(c, args.read_pc_minutes, vb)
    if args.command in ("run", "read") and not args.no_read:
        do_read(c, vb)
    if args.command in ("run", "tasks") and not args.no_tasks:
        do_tasks(c, vb)
    if args.command in ("run", "level") and not args.no_level:
        do_level_tasks(c, vb)
    if args.command in ("run", "wheel") and not args.no_wheel:
        # 必须用关键字传参：do_wheel 的第二个形参是 max_times 而不是 verbose，
        # 按位置传 vb 会让精简模式（vb=False）变成 max_times=0，静默一次都不抽。
        do_wheel(c, verbose=vb)
    if args.command in ("run", "lottery") and not args.no_lottery:
        do_read_lottery(c, vb)
    if args.command in ("run", "box") and not args.no_box:
        do_box(c, vb)

    # 看视频领券：纯 HTTP，不依赖前面任何步骤。
    # 放在最后是因为它耗时最长（每 id 20s 冷却），且收益要进下面的「账面」。
    # run 默认包含它——「run 跑全部」才符合直觉，漏掉一项就是坑。
    if args.command == "run" and not args.no_video:
        do_video_reward(c, vb, max_seconds=args.video_max_seconds)

    # 淘书市满减券：活动一月约换两次，滚动栏里挂着才能领；
    # 没换期时自动跳过（不出声），有则逐张领。
    if args.command == "run" and not args.no_taoshushi:
        do_taoshushi(c, vb)

    if vb:
        print("\n" + "=" * 46)
        print("—— 操作后状态 ——")
        show_status(c)
    else:
        show_summary(c)

    out_dir = cfg.get("output", {}).get("dir") or "output"
    if args.save or cfg.get("output", {}).get("save_credentials"):
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "credentials.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cred.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n凭证摘要已保存: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())