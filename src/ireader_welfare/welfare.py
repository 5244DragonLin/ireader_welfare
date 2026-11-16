#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""福利中心接口客户端。

所有请求的签名规则一致：
    参数按 key 字典序拼成 k=v&k=v → SHA1WithRSA 签名（客户端私钥）→ 作为 sign 字段

另外有一个容易踩的坑：福利中心的多数接口必须带 H5 页面参数
（p2/p3/p4/p29/p33），否则服务端返回 code:0 但业务不落账。
"""
import time
import urllib.parse

from .crypto import sorted_param_str
from .http import UA, get_json

API = "https://ah2.zhangyue.com"

# H5 福利页带的设备参数，缺失会导致接口“假成功”
PAGE_PARAMS = {
    "p2": "104583",
    "p3": "18070403",
    "p4": "501603",
    "p29": "zye5b814",
    "p33": "com.chaozh.iReaderFree",
}

# 常用返回码
CODE_OK = 0
CODE_ALREADY = 10240111        # 已领取过奖励
CODE_NO_WHEEL = 32533          # 转盘无次数
CODE_VIDEO_NO_CHANCE = 53002   # 视频次数用完 / 未开放
CODE_VIDEO_UNSUPPORTED = 50003 # 不支持视频


class WelfareClient:
    """福利中心客户端，持有一个已登录的凭证。"""

    def __init__(self, credential, sign_key=None):
        self.cred = credential
        self.usr = credential.usr
        self.zysid = credential.zysid
        # 默认用登录时下发的会话私钥；签到类接口用客户端内置私钥亦可
        self.key = sign_key or credential.native_priv

    # ---------------- 底层 ----------------
    def _call(self, path: str, params: dict = None, with_page: bool = True) -> dict:
        p = dict(PAGE_PARAMS) if with_page else {}
        p.update(params or {})
        p["usr"] = self.usr
        p["timestamp"] = str(int(time.time() * 1000))
        from .crypto import rsa_sha1_sign
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))
        qs = urllib.parse.urlencode(p) + f"&zysid={self.zysid}"
        return get_json(
            f"{API}{path}?{qs}",
            headers={"Referer": f"{API}/activity/welfareIreader.html"},
        )

    # ---------------- 查询 ----------------
    def sign_page(self) -> dict:
        """福利页聚合数据：用户信息 + 转盘 + 宝箱。"""
        return self._call("/welfare/sign/page")

    def sign_info(self) -> dict:
        """签到活动详情（act_id / week_info / 奖励明细）。"""
        return self._call("/welfare/sign/info")

    def sign_get(self) -> dict:
        """签到状态（is_sign 以此为准，sign_info 可能缓存）。"""
        return self._call("/welfare/market/task/sign_get", {"appId": "com.chaozh.iReaderFree"})

    def sign_state(self) -> dict:
        """今日签到状态的 body，最可靠的判断来源。"""
        return self.sign_get().get("body") or {}

    def has_signed_today(self) -> bool:
        return bool(self.sign_state().get("is_sign"))

    def tasks(self, p2: str = None) -> dict:
        """日常任务列表。

        `p2` 是渠道号，决定服务端下发哪批任务。默认用 PAGE_PARAMS 里的值；
        跨 App 换量任务只在特定渠道下发，见 cross_app_tasks()。
        """
        return self._call("/welfare/sign/v2/task", {"p2": p2} if p2 else None)

    def cross_app_tasks(self) -> list:
        """跨 App 换量任务（type_id=32，如「打开携程做任务领取现金」）。

        ⚠️ 只在换量渠道下发：用默认 p2 查任务列表**永远看不到它**。
        实测 p2=116298 / 116297 会额外下发携程任务，其余 4 条与默认渠道完全一致，
        所以这里只是在默认列表之外**补查一次**，不影响别的任务。

        为什么单独一个方法：这条任务纯 HTTP 做不完（详见 docs 逆向笔记十八节），
        但「看不见」和「做不了」是两回事——看不见会让人以为任务不存在。

        返回 [{id, name, award, status, sub_type, jump_url, uri}]。
        """
        out = []
        for t in self.tasks(CROSS_APP_CHANNEL).get("body") or []:
            tl = (t.get("target_list") or [{}])[0]
            value = tl.get("value") or {}
            sub_type = value.get("type")
            if not str(sub_type).startswith("32"):
                continue
            out.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "award": t.get("award"),
                "status": t.get("status"),
                "sub_type": sub_type,
                "jump_url": t.get("jump_url"),
                "uri": value.get("uri") or "",
            })
        return out

    def read_tasks(self) -> dict:
        """阅读赚代金券任务（10 档）。"""
        return self._call("/welfare/sign/v2/read")

    def read_tasks_v2(self) -> dict:
        """阅读赚代金券任务（新版活动接口）。

        H5 里 `getNewReadInfo` 按 `isNewActivity` 二选一：
        新版走 `/welfare/market/task/read_get`，旧版走 `/welfare/sign/v2/read`。
        老账号多半仍在旧活动，此时本接口 body 为空。
        """
        return self._call("/welfare/market/task/read_get")

    def read_reward_v2(self, params: dict = None) -> dict:
        """领取新版阅读任务奖励。"""
        return self._call("/welfare/market/task/read_reward", params or {})

    def video_status(self, position: str = "VIDEO_PAYTASK") -> dict:
        """看视频任务状态。position 常用值见 VIDEO_POSITIONS。"""
        return self._call("/zycl/welfare/video/status", {"position": position})

    def video_info(self, position: str = "VIDEO_PAYTASK") -> dict:
        """看视频任务文案（50003 表示不支持视频）。"""
        return self._call("/zycl/welfaremarket/video/info", {"position": position})

    def share_status(self) -> dict:
        """分享日签卡片数据（用于渲染日签图）。

        ⚠️ 返回体里的 `status` 是**黄历「宜/忌」标记**（1=宜，2=忌），
        卡片页就是靠它渲染日签上的【宜】/【忌】，**与任务是否完成无关**。

        body 示例：
            {"date": "2026-10-28", "desc": "重要的不是过去，而是未来。",
             "doing": "面对未来", "img_url": "https://...", "status": 1}

        要判断「分享日签」有没有做完，请用 share_task()。
        """
        return self._call("/welfare/sign/today/share")

    def share_task(self) -> dict:
        """「分享今日日签」任务的真实状态。

        从日常任务列表（/welfare/sign/v2/task）里挑出
        target_list[0].value.share_type == "daily_sign_in" 的那条。

        status: 0=未完成 / 1=可领取 / 2=已领取。
        """
        for t in self.tasks().get("body") or []:
            target = (t.get("target_list") or [{}])[0]
            if (target.get("value") or {}).get("share_type") == "daily_sign_in":
                return t
        return {}

    def report_share(self, way: str = "weixin", typ: str = "daily_sign_in",
                     pos: str = "welfare") -> dict:
        """上报一次「分享成功」，让服务端把分享类任务标记为完成。

        2026-11-14 实测有效：分享日签任务 status 由 0 直接翻转为 2（奖励自动发放）。

        协议还原（自 8.8.1 `Platform/Share/b.d()`）：
          POST uc.ireader.com/user/scomm/share_report
          form（参与签名，按 key 字典序 SHA1WithRSA 客户端内置私钥）：
            attr / open_uid / pos / timestamp / type / user_name / way + sign
          query（不参与签名，App 网络栈自动附加的会话与设备参数）：
            usr / zysid / p1..p34 全套设备参数

        三个参数都不可省（缺设备参数或把 zysid 放进 form 都只回 OK 不落账）：
          - type  分享内容类型，日签即 daily_sign_in（H5 downloadImage 的 shareType）
          - pos   来源位置，福利中心固定传 welfare（H5 downloadImage 的 pos）
          - way   渠道，App 实际会传 weixin / weixin_friend / qq / qq_zone / weibo
        """
        p = {
            "attr": "{}",
            "open_uid": SHARE_APP_IDS.get(way, ""),
            "pos": pos,
            "type": typ,
            "user_name": self.usr,
            "way": way,
        }
        p["timestamp"] = str(int(time.time() * 1000))
        from .crypto import rsa_sha1_sign, sorted_param_str
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))

        import urllib.request
        from .http import DEFAULT_TIMEOUT
        qs = urllib.parse.urlencode({"usr": self.usr, "zysid": self.zysid,
                                     **APP_DEVICE_PARAMS})
        url = f"https://uc.ireader.com/user/scomm/share_report?{qs}"
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(p).encode(),
            headers={
                # 与实测翻转成功的请求保持一致：App 原生栈的 Dalvik UA
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 15; 2211133C Build/AP3A.240905.015)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            import json as _json
            return _json.loads(r.read().decode("utf-8", "ignore"))

    def share_daily_flow(self) -> dict:
        """分享日签翻转：未完成 → 上报一次分享 → 复查状态。

        领奖不在这里做——翻成可领取后交给 claim_daily_tasks 统一领。

        返回 {status_before, reported, status_after, msg}。
        """
        before = self.share_task().get("status")
        out = {"status_before": before, "reported": False,
               "status_after": before, "msg": None}
        if before != 0:
            return out
        try:
            self.report_share()
        except Exception as e:
            out["msg"] = f"上报失败: {e}"
            return out
        out["reported"] = True
        out["status_after"] = self.share_task().get("status")
        return out

    def box_state(self) -> dict:
        """宝箱状态：本周活跃值 + 三档宝箱进度。"""
        box = (self.sign_page().get("body") or {}).get("box") or {}
        score = int(box.get("user_score") or 0)
        chests = []
        for i, item in enumerate(box.get("list") or []):
            gift = (item.get("gift") or [{}])[0]
            chests.append({
                "index": i + 1,
                "need": int(item.get("score") or 0),
                "gift": gift.get("gift_name"),
                "able": bool(item.get("able")),
                "got": bool(item.get("got")),
            })
        nxt = next((c for c in chests if not c["got"] and not c["able"]), None)
        return {
            "score": score,
            "chests": chests,
            "next": nxt,
            "next_gap": (nxt["need"] - score) if nxt else 0,
        }

    # ---------------- 写操作 ----------------
    def do_sign(self) -> dict:
        """执行签到。

        ⚠️ 必须带页面参数，否则接口返回 code:0 但 is_sign 不变化（假成功）。
        调用后请用 has_signed_today() 复核。
        """
        return self._call("/zycl/welfaremarket/sign")

    def claim_sign_reward(self, act_id=None, act_type=None) -> dict:
        """领取签到奖励（签到弹窗里的 autoSign）。

        返回 CODE_OK 表示领取成功，CODE_ALREADY 表示今天已领过。
        """
        st = self.sign_state()
        params = {
            "actId": str(act_id or st.get("act_id") or ""),
            "actType": str(act_type or st.get("act_type") or ""),
        }
        return self._call("/zycl/welfaremarket/draw", params)

    def reset_sign_flow(self) -> dict:
        """签到 + 领奖一步到位，以 has_signed_today() 判定成败。"""
        result = {"signed": False, "reward": None, "already": False}
        if self.has_signed_today():
            result["already"] = True
            result["signed"] = True
        else:
            self.do_sign()
            if not self.has_signed_today():
                return result
            result["signed"] = True
        result["reward"] = self.claim_sign_reward()
        return result

    def wheel_draw(self, act_id) -> dict:
        """大转盘抽奖，act_id 取自 sign_page 的 wheel.act_id。"""
        p = dict(PAGE_PARAMS)
        p.update({"ca": "Activity_NewDzp.Draw", "activeId": str(act_id)})
        p["usr"] = self.usr
        p["timestamp"] = str(int(time.time() * 1000))
        from .crypto import rsa_sha1_sign
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))
        qs = urllib.parse.urlencode(p) + f"&zysid={self.zysid}"
        return get_json(f"{API}/zyam/app/app.php?{qs}")

    def wheel_chances(self) -> int:
        wheel = (self.sign_page().get("body") or {}).get("wheel") or {}
        return int(wheel.get("draw_times") or 0)

    def wheel_act_id(self):
        wheel = (self.sign_page().get("body") or {}).get("wheel") or {}
        return wheel.get("act_id")

    def coupons(self) -> list:
        """满减优惠券列表（转盘常抽到这类奖品）。

        ⚠️ 与「代金券」是两套东西，接口也不同：
          代金券 → /zyuc/gift/ajaxrequest   （选书助手 picker skill 查余额用它）
          优惠券 → /zyuc/coupon/ajaxrequest （满1000减200 之类记在这里）

        返回体是 {"html": "<div class='card_use'>..."}，卡片本身是 HTML，
        所以按卡片切段再用正则取券名与有效期；带 expired class 的是已过期。
        """
        import re
        import urllib.request
        from .http import DEFAULT_TIMEOUT, UA

        url = (f"{API}/zyuc/coupon/ajaxrequest?index=1"
               f"&usr={self.usr}&zysid={self.zysid}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        try:
            import json as _json
            html = (_json.loads(raw) or {}).get("html") or ""
        except ValueError:
            html = raw

        out = []
        # 每张券以 <div class="card_use ..."> 开头
        chunks = re.split(r'<div class="card_use', html)[1:]
        for seg in chunks:
            expired = seg.lstrip().startswith("expired")
            name = re.search(r'class="name">([^<]*)<', seg)
            valid = re.search(r'class="txt[^"]*">([^<]*)<', seg)
            amount = re.search(r'class="num">([^<]*)<', seg)
            pid = re.search(r'data-productid="(\d+)"', seg)
            out.append({
                "name": (name.group(1).strip() if name else ""),
                "amount": (amount.group(1).strip() if amount else ""),
                "valid": (valid.group(1).strip() if valid else ""),
                "expired": expired,
                "product_id": (pid.group(1) if pid else ""),
            })
        return out

    def vouchers(self) -> list:
        """代金券（阅饼）明细，含面额 / 来源 / 有效期。

        ⚠️ 与「满减优惠券」是两套东西（见 coupons 的注释）：
          代金券 → /zyuc/gift/ajaxrequest   index=2 是「已获得」（含有效期）

        返回体同样是 HTML 卡片片段，解析规则与 ireader-picker 技能一致：
          <div class="card_useless used" data-id="...">
            <div class="num">30</div><div class="tag">代金券</div>
            <div class="name">获得30阅饼 | 签到送礼</div>
            <div class="txt">有效期至2026-11-30</div>
        已使用的卡片带 used class；是否过期按有效期日期判断。
        """
        import re

        url = f"{API}/zyuc/gift/ajaxrequest?index=2&usr={self.usr}&zysid={self.zysid}"
        raw = get_json(url)
        html = raw.get("html") or raw.get("_raw") or ""

        out = []
        for chunk in re.split(r'(?=<div class="card_useless)', html)[1:]:
            amt = re.search(r'<div class="num">([0-9.]+)</div>', chunk)
            if not amt:
                continue
            name = re.search(r'<div class="name">(.*?)</div>', chunk, re.S)
            txt = re.search(r'<div class="txt">([^<]*)</div>', chunk)
            exp = None
            if txt:
                m = re.search(r'(\d{4}-\d{2}-\d{2})', txt.group(1))
                exp = m.group(1) if m else None
            out.append({
                "amount": float(amt.group(1)),
                "source": (re.sub(r"<[^>]+>", "", name.group(1)).split("|")[-1].strip()
                           if name else ""),
                "expire": exp,
                "used": "used" in chunk[:60],
            })
        return out

    def open_box(self, index: int) -> dict:
        """开启宝箱（index 从 1 开始）。活跃值不足时不会误扣。"""
        return self._call("/zycl/welfare/box/open", {"index": index})

    def claim_daily_tasks(self) -> list:
        """领取所有已完成但未领奖的日常任务（如分享日签）。

        注意：任务完成与否由服务端判定，客户端无法伪造。
        这里只负责「已完成 → 领奖」这一步。

        换量任务（跨 App）要**额外**查一次换量渠道：它不在默认渠道的任务列表里，
        只查默认列表会漏掉「用户在 App 里手动做完、这里领不到奖」的情况。

        返回 [{name, award, code, msg, ok}]
        """
        out = []
        tasks = self.tasks().get("body") or []
        # 换量渠道的任务按 id 去重后并入（默认渠道查不到那几条）
        seen = {t.get("id") for t in tasks if t.get("id")}
        try:
            for t in self.tasks(CROSS_APP_CHANNEL).get("body") or []:
                if t.get("id") and t["id"] not in seen:
                    tasks.append(t)
                    seen.add(t["id"])
        except Exception:
            pass          # 换量渠道查询失败不影响普通任务领奖

        for t in tasks:
            if not t.get("id") or not t.get("id_sign"):
                continue
            # status: 1=可领取 2=已领取 0=未完成
            if t.get("status") != 1:
                continue
            r = self._call("/zycl/newsign/task/draw", {
                "id": str(t.get("id")),
                "idSign": t.get("id_sign"),
            })
            out.append({
                "name": t.get("name"),
                "award": t.get("award"),
                "code": r.get("code"),
                "msg": r.get("msg"),
                "ok": r.get("code") == CODE_OK,
            })
        return out

    def claim_read(self, task: dict) -> dict:
        """领取单档阅读奖励（需 id + id_sign）。"""
        return self._call("/zycl/newsign/task/draw", {
            "id": str(task.get("id")),
            "idSign": task.get("id_sign"),
        })

    def claim_read_all(self, max_amount: int = 0) -> list:
        """领取所有已达标的阅读档位。max_amount>0 时跳过超过该额度的档。

        已领取（status==2）的档会跳过，避免重复请求换来一串 22030。
        """
        tasks = (self.read_tasks().get("body") or {}).get("list") or []
        done = []
        for t in tasks:
            if t.get("status") == 2:      # 已领取
                continue
            cur, quota = t.get("current") or 0, t.get("quota") or 0
            reached = t.get("status") == 1 or (quota and cur >= quota)
            if not reached:
                continue
            amount = int((t.get("gift") or {}).get("amount") or 0)
            if max_amount and amount > max_amount:
                continue
            done.append({"name": t.get("name"), "amount": amount, "resp": self.claim_read(t)})
        return done

    # ---------------- 阅读时长抽奖（与大转盘是两个活动） ----------------
    def read_lottery_state(self) -> dict:
        """阅读时长抽奖的状态：次数、actSign、是否在活动期。

        这是福利页「去抽奖」跳过去的那个页面（每天最多 4 次，
        由阅读时长里程碑解锁），**与大转盘 act_id=12516 不是同一个活动**，
        活动号是 READ_LOTTERY_ACT_ID。

        页面是服务端渲染的 HTML，状态塞在 `id="dataMap"` 的 data-option
        属性里（未登录时 drawTimes 恒为 0），所以带上 usr/zysid 直接抓页面再解析。
        """
        import re
        import urllib.request
        from .http import DEFAULT_TIMEOUT, UA

        url = (f"{API}/zyam/app/app.php?ca=Activity_NewDzp.Index"
               f"&activeId={READ_LOTTERY_ACT_ID}&a0=ydcc-09"
               f"&usr={self.usr}&zysid={self.zysid}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", "ignore")

        i = html.find('id="dataMap"')
        if i < 0:
            return {}
        seg = html[i:i + 8000]
        m = re.search(r"data-option='(\{.*?\})'", seg, re.S)
        if not m:
            return {}
        import json as _json
        st = _json.loads(m.group(1))

        # 顺手把「阅读 X 分钟得 1 次机会」的里程碑列表抠出来，
        # 页面里是 data-list='[{"t":30,"d":1},...]'，领奖时要原样回传
        ml = re.search(r"data-list='(\[[^\]]*\])'", html)
        if ml:
            try:
                st["milestones"] = _json.loads(ml.group(1))
            except ValueError:
                st["milestones"] = []
        return st

    def read_lottery_claim_awards(self) -> dict:
        """领取阅读时长里程碑的抽奖机会（30/60/120/180 分钟各 1 次）。

        这一步不能省：不领里程碑，drawTimes 就是 0。已领过时服务端回
        `code:1 领取失败`，无副作用，可以放心重复调用。
        """
        st = self.read_lottery_state()
        ms = st.get("milestones") or []
        if not ms:
            return {}
        import json as _json
        return self._call("/zyam/app/app.php", {
            "ca": "Activity_NewDzp.DrawReadAward",
            "activeId": READ_LOTTERY_ACT_ID,
            "list": _json.dumps(ms, separators=(",", ":")),
        })

    def read_lottery_draw(self, act_sign: str) -> dict:
        """阅读时长抽奖抽一次。需要页面下发 actSign。"""
        return self._call("/zyam/app/app.php", {
            "ca": "Activity_NewDzp.Draw",
            "activeId": READ_LOTTERY_ACT_ID,
            "actSign": str(act_sign),
        }, with_page=True)

    def read_lottery_draw_all(self, max_times: int = 10, claim_first: bool = True) -> list:
        """把阅读时长抽奖的次数抽完，返回每轮结果。

        `claim_first=True` 时先领里程碑机会——阅读够 30/60/120/180 分钟后
        那 4 个「得1次机会」不会自动到账，必须调一次 DrawReadAward。
        """
        if claim_first:
            self.read_lottery_claim_awards()
        st = self.read_lottery_state()
        if not st.get("isLogin"):
            return []
        left = int(st.get("drawTimes") or 0)
        sign = st.get("actSign")
        out = []
        while left > 0 and len(out) < max_times:
            r = self.read_lottery_draw(sign)
            gift = r.get("gift") or {}
            ok = r.get("code") == CODE_OK
            out.append({
                "ok": ok,
                "name": gift.get("desc") or gift.get("gift_type") or "",
                "code": r.get("code"),
                "msg": r.get("msg"),
                "left": r.get("draw_times"),
            })
            if not ok:
                break
            # 服务端会回剩余次数；没有就重抓页面
            left = r.get("draw_times")
            if left is None:
                left = int((self.read_lottery_state().get("drawTimes") or 0))
            left = int(left)
        return out

    # ---------------- 等级成长任务（经验值 / 升级礼包） ----------------
    #
    # 入口：App「我的 → 昵称 → Lv14 等级勋章 → 我的等级」。
    # 这套接口与福利中心完全独立（在 ah2.zhangyue.com 的 /zyuc/ 下），
    # 签名规则也不同：只签 usr + timestamp，taskId 走 query 不参与签名，
    # 且上报是 POST（签名串放 body）。踩错任何一点都只回 code:0 不落账。

    def _level_call(self, path: str, signed_extra: dict = None,
                    query_extra: dict = None, post: bool = False) -> dict:
        """等级接口的统一调用。

        与福利接口的两处差异（实测得出，见 docs/掌阅逆向笔记.md 十七节）：
          1. 参与签名的只有 usr / timestamp（+ 少数业务参数），
             设备参数与 taskId 只放 query，不进签名；
          2. TaskReport 是 POST，签名串当 form body 提交。
        """
        import urllib.request
        from .http import DEFAULT_TIMEOUT
        from .crypto import rsa_sha1_sign, sorted_param_str

        p = {"usr": self.usr, "timestamp": str(int(time.time() * 1000))}
        p.update(signed_extra or {})
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))

        q = dict(APP_DEVICE_PARAMS)
        q.update(query_extra or {})
        qs = urllib.parse.urlencode(q) + f"&zysid={self.zysid}"
        url = f"{API}{path}?{qs}"

        body = None
        headers = {"User-Agent": LEVEL_UA}
        if post:
            body = urllib.parse.urlencode(p).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            # GET 时签名参数要进 query，且必须放在设备参数之后（顺序不影响验签）
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(p)}"

        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if post else "GET")
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            txt = r.read().decode("utf-8", "ignore")
        try:
            import json as _json
            return _json.loads(txt)
        except ValueError:
            return {"_raw": txt}

    def level_info(self) -> dict:
        """等级总览：当前等级/经验 + 升级礼包列表 + 成长任务列表。

        body 结构（实测）：
            userInfo  {level, currentExp, exp}
            levelTag  {levelInfo: [{level, exp, packId, status, giftInfo: [...]}]}
            taskInfo  [{id, name, taskType, status, giftList: [{name, typeId}]}]
        status：0 未完成 / 1 已完成待领取 / 2 已领取。
        """
        return self._level_call("/zyuc/api/user/level")

    def level_state(self) -> dict:
        """level_info 的 body，取不到时返回空字典。"""
        return (self.level_info() or {}).get("body") or {}

    def level_exp_tasks(self) -> list:
        """只要「做任务得经验」这一类的条目（taskType=0）。

        taskType=1 是充值送经验，纯描述、无 taskId 可上报，过滤掉。
        """
        return [t for t in (self.level_state().get("taskInfo") or [])
                if t.get("taskType") == 0 and t.get("id")]

    def report_level_task(self, task_id, amount: str = None) -> dict:
        """上报一个成长任务已完成（如「浏览书城超过20秒」）。

        协议（反编译 pluginwebdiff_bookstore3 `mylevel/b.b()` +
        `network/f$b.sign()` + `f.urlPost()`）：
            POST /zyuc/api/user/TaskReport?taskId=<id>&<全套设备参数>&zysid=<zysid>
            body = usr & timestamp & sign（sign = SHA1WithRSA(排序串)）
        注意 taskId **不进签名**、放 body 会 53001；反过来放 query 才落账。
        """
        q = {"taskId": str(task_id)}
        if amount not in (None, ""):
            q["amount"] = str(amount)
        return self._level_call("/zyuc/api/user/TaskReport",
                                query_extra=q, post=True)

    def claim_level_task_reward(self, task_id) -> dict:
        """领取成长任务奖励（taskId 参与签名，走 query）。

        成功时 body.info 里带发奖明细，例如
        [{"type_id": 530, "name": "10点经验", "value": {"amount": "10"}}]。
        """
        return self._level_call("/zyuc/task/drawreward",
                                signed_extra={"taskId": str(task_id)})

    def upgrade_tasks_flow(self, browse_task_name: str = "浏览书城",
                           flip_wait: int = 5) -> dict:
        """把「浏览书城超过20秒」这类纯浏览任务走完并领奖。

        为什么能纯 HTTP 完成：这类任务的完成判定就是「收到一条
        TaskReport 回执」，服务端不校验阅读/浏览行为本身——与福利中心的
        「阅读满5分钟」不同（那个要真实阅读会话）。所以上报即完成。

        坑：上报成功后服务端 status 由 0 翻成 1 有秒级延迟（实测约 1 秒），
        立刻复查会拿到 status=0 而跳过领奖，白漏经验。2026-11-16 的 04:00
        定时任务就是这么漏掉 10 点经验的。所以上报后要轮询到翻转为止，
        最多等 flip_wait 秒。

        返回 {reported, claimed, gained, pending, tasks, msg}。
        pending 是「上报成功但没领到」的条目，供调用方提示补跑。
        """
        out = {"reported": [], "claimed": [], "gained": 0, "pending": [],
               "tasks": [], "msg": None}
        tasks = self.level_exp_tasks()
        out["tasks"] = [{"id": t.get("id"), "name": t.get("name"),
                         "status": t.get("status")} for t in tasks]
        if not tasks:
            out["msg"] = "服务端未下发成长任务"
            return out

        for t in tasks:
            tid, name, status = t.get("id"), t.get("name") or "", t.get("status")
            if status == 2:
                continue
            # 只做「浏览书城」这类回执型任务；充值任务无可上报的入口
            if browse_task_name not in name:
                continue
            if status == 0:
                r = self.report_level_task(tid)
                ok = r.get("code") == CODE_OK
                out["reported"].append({"id": tid, "name": name, "ok": ok,
                                        "msg": r.get("msg")})
                if not ok:
                    continue

        # 上报后服务端 status 0→1 有秒级延迟，先轮询等它翻过来再领，
        # 否则复查拿到 status=0 会直接跳过、这一天的经验就漏了。
        reported_ids = {r["id"] for r in out["reported"] if r["ok"]}
        if reported_ids:
            for _ in range(max(1, flip_wait)):
                flipped = {t.get("id") for t in self.level_exp_tasks()
                           if t.get("status") == 1}
                if reported_ids <= flipped:
                    break
                time.sleep(1)

        # 复查一次，把已完成的领掉
        for t in self.level_exp_tasks():
            tid, name, status = t.get("id"), t.get("name") or "", t.get("status")
            if status != 1:
                continue
            r = self.claim_level_task_reward(tid)
            if r.get("code") != CODE_OK:
                out["claimed"].append({"id": tid, "name": name, "ok": False,
                                       "msg": r.get("msg")})
                out["pending"].append({"id": tid, "name": name})
                continue
            gained = 0
            for info in (r.get("body") or {}).get("info") or []:
                try:
                    gained += int(((info.get("value") or {}).get("amount")) or 0)
                except (TypeError, ValueError):
                    pass
            out["gained"] += gained
            out["claimed"].append({"id": tid, "name": name, "ok": True,
                                   "gained": gained})

        # 上报成功、但服务端还没翻成「待领取」的，记进 pending 提醒补跑
        claimed_ids = {c["id"] for c in out["claimed"]}
        left = {t.get("id"): t.get("status") for t in self.level_exp_tasks()}
        for r in out["reported"]:
            if not r["ok"] or r["id"] in claimed_ids:
                continue
            if left.get(r["id"]) == 0:
                out["pending"].append({"id": r["id"], "name": r["name"]})
        # 同一条任务可能既「领奖失败」又「仍未翻转」，去重后只提示一次
        seen, uniq = set(), []
        for r in out["pending"]:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            uniq.append(r)
        out["pending"] = uniq
        return out

    def level_unclaimed_packages(self) -> list:
        """已达标但还没领的等级礼包（经验够、status=0、isNew=1）。"""
        cur = int((self.level_state().get("userInfo") or {}).get("currentExp") or 0)
        out = []
        for pk in (self.level_state().get("levelTag") or {}).get("levelInfo") or []:
            if not pk.get("isNew"):
                continue
            if int(pk.get("exp") or 0) > cur:
                continue
            if pk.get("status") == 0:
                out.append(pk)
        return out

    # ---------------- 品质好书任务（阅读满5分钟 / 收听满10分钟） ----------------
    #
    # 这两条任务曾被认为是「纯 HTTP 不可行」的代表，2026-11-16 实测推翻。
    # 判定需要两个条件同时满足：
    #   ① 书单页的「入口上下文」——一条 sensorlog 曝光事件，page_key = act_id
    #   ② 落在当日书单内的书 + read/time 时长
    #
    # 关键坑（都踩过）：
    #   - 书单页 act_id **每天轮换**（11-15 是 1hj4v9irk00，11-16 是 1k6aq1vqu00），
    #     必须从任务 jump_url 里动态取，写死就会对着过期书单做实验。
    #   - 单发曝光事件不够（current 不动），必须再配 read/time。
    #   - 判据只看 target_list[0].current 增量，**不看 code**（code:0 是假成功）。
    #   - 任务有 quota 天花板，封顶后任何写入都看不出来。

    def quality_read_task(self, keyword: str = "阅读品质好书") -> dict:
        """取「品质好书」类任务条目（实例 id 每天变化，按名字匹配）。"""
        for t in self.tasks().get("body") or []:
            if keyword not in (t.get("name") or ""):
                continue
            tl = (t.get("target_list") or [{}])[0]
            return {
                "id": t.get("id"),
                "name": t.get("name"),
                "status": t.get("status"),
                "current": tl.get("current"),
                "quota": tl.get("quota"),
                "jump_url": t.get("jump_url"),
            }
        return {}

    @staticmethod
    def act_from_jump_url(jump_url: str) -> str:
        """从 jump_url 取当日书单页 act_id。"""
        import re as _re
        m = _re.search(r"act_id=([A-Za-z0-9]+)", jump_url or "")
        if m:
            return m.group(1)
        m = _re.search(r"/opr/([A-Za-z0-9]+)\.html", jump_url or "")
        return m.group(1) if m else ""

    def task_book_list(self, act_id: str) -> list:
        """抓书单页里的书 ID 列表（匿名可访问）。"""
        import urllib.request
        import re as _re
        from .http import DEFAULT_TIMEOUT
        url = (f"https://h5.zhangyue.com/opr/{act_id}.html"
               f"?act_id={act_id}&launch=inside")
        req = urllib.request.Request(url, headers={"User-Agent": _TASK_MOBILE_UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            html = r.read().decode("utf-8", "ignore")
        return list(dict.fromkeys(_re.findall(r"BookCollection:(\d+)", html)))

    def report_task_pageview(self, act_id: str) -> dict:
        """上报一次书单页「访问」（sensorlog 曝光事件，page_key = act_id）。

        协议与 App 内埋点同一路径（书单页 JS 里 loggerType=sensorWeb，
        实际仍走 /log-agent/sensorlog + logAdapter=sensor + MD5 签名）。
        签名：按 key 字典序拼 k=v，topic 的值先 reverse，再 MD5。
        """
        import hashlib
        import json as _json
        import urllib.request
        from .http import DEFAULT_TIMEOUT

        now_ms = int(time.time() * 1000)
        ev = {
            "type": "track",
            "event": "$pageview",
            "time": now_ms,
            "_track_id": now_ms % 100000,
            "distinct_id": self.usr,
            "login_id": self.usr,
            "anonymous_id": "",
            "properties": {
                "page_key": act_id,
                "page_type": "opr",
                "page_name": "出版图书指定阅读任务",
                "from_page": "", "from_page_key": "", "from_page_type": "",
                "project": "opr",
                "$lib": "js", "$lib_method": "code", "$lib_version": "1.0.0",
                "$screen_width": 1080, "$screen_height": 2340,
                "topic": "",
            },
        }
        params = {
            "logAdapter": "sensor", "sign_type": "MD5",
            "compress_type": "NONE",
            "data": _json.dumps([ev], ensure_ascii=False, separators=(",", ":")),
            "topic": "",
        }
        parts = []
        for k in sorted(params):
            v = str(params[k])
            if k == "topic":
                v = v[::-1]
            parts.append(f"{k}={v}")
        params["sign"] = hashlib.md5("&".join(parts).encode()).hexdigest()

        req = urllib.request.Request(
            "https://logv2.ireader.com/log-agent/sensorlog",
            data=urllib.parse.urlencode(params).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": _TASK_MOBILE_UA},
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            txt = r.read().decode("utf-8", "ignore")
        try:
            import json as _j
            return _j.loads(txt)
        except ValueError:
            return {"_raw": txt}

    def quality_task_flow(self, keyword: str = "阅读品质好书",
                          minutes: float = 5, res: str = "R") -> dict:
        """把「品质好书」类任务用纯 HTTP 做完（曝光入口 + read/time 铺时长）。

        2026-11-16 实测：
          - 阅读满5分钟：res="R"，current 0→10、status 0→1，
            领奖 +10经验值 +10活跃值 +1次翻卡；
          - 收听满10分钟：必须把 res 换成 "L"（"R" 铺满也不动），同样翻转到 10/10，
            领奖 +30经验值 +10活跃值。

        返回 {task, act_id, books, book_id, reported, ok, msg}。
        """
        out = {"task": None, "act_id": "", "books": [], "book_id": None,
               "reported": False, "ok": False, "msg": None}
        task = self.quality_read_task(keyword)
        out["task"] = task
        if not task:
            out["msg"] = "服务端未下发该任务"
            return out
        if task.get("status") == 2:
            out["msg"] = "已领取"
            out["ok"] = True
            return out
        # 已封顶或已可领取，无需再铺时长
        cur, quota = task.get("current") or 0, task.get("quota") or 0
        if task.get("status") == 1 or (quota and cur >= quota):
            out["msg"] = "已达标，待领取"
            out["ok"] = True
            return out

        act_id = self.act_from_jump_url(task.get("jump_url"))
        out["act_id"] = act_id
        if not act_id:
            out["msg"] = "jump_url 里取不到 act_id"
            return out

        try:
            books = self.task_book_list(act_id)
        except Exception as e:
            out["msg"] = f"抓书单失败: {e}"
            return out
        out["books"] = books
        if not books:
            out["msg"] = "书单为空"
            return out
        out["book_id"] = books[0]

        # ① 入口曝光（书单页访问）
        try:
            self.report_task_pageview(act_id)
            out["reported"] = True
        except Exception as e:
            out["msg"] = f"曝光上报失败: {e}"
            return out

        # ② 用 read/time 铺时长（复用阅读时长通道）
        #    res="R" 阅读 / "L" 收听 —— 收听用 "R" 报多久都不会动。
        #
        #    窗口必须挑「今天还没报过」的时段：服务端按分钟在 res="R" 内跨任务
        #    去重，而 run 里主流程刚把 180 分钟铺满、末尾正压在当前时刻，
        #    若这里仍用 now-minutes → now 会被吃掉大部分分钟，任务停在 0/10
        #    不翻转（2026-11-17 实测重叠 4/5 分钟）。详见 reading.free_windows。
        #    "L" 是独立计数器，不占 R 的分钟池，因此只在 res="R" 时记录占用。
        from .reading import free_windows, mark_used, report_once
        is_read = res.upper() == "R"
        if is_read:
            try:
                wins = free_windows(minutes, step=int(max(1, minutes)))
            except Exception as e:
                out["msg"] = f"规划上报时段失败: {e}"
                return out
            if not wins:
                out["msg"] = ("今天可铺时段已被主流程占满，"
                              "无法再为该任务腾出未报过的分钟")
                return out
            start_ms, end_ms = wins[0]
        else:
            # 收听是独立计数器，随便一段真实区间都算，不受 R 池去重影响，
            # 也不需要占用 read_pc_state.json（否则会白扣主流程 10 分钟额度）。
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - int(minutes * 60000)
        try:
            report_once(self.cred.usr, self.cred.zysid, books[0],
                        start_ms, end_ms, res=res)
        except Exception as e:
            out["msg"] = f"read/time 失败: {e}"
            return out
        # 上报成功后再记账（失败不能提前占坑，否则这几分钟白丢）
        if is_read:
            try:
                mark_used(start_ms, end_ms)
            except Exception:
                pass

        # ③ 复查
        after = self.quality_read_task(keyword)
        out["task"] = after
        if after.get("status") in (1, 2) or \
                (after.get("quota") and (after.get("current") or 0) >= after["quota"]):
            out["ok"] = True
        else:
            out["msg"] = "时长已上报但任务未翻转（可能延迟落账）"
        return out


# 阅读时长抽奖的活动号（福利页「去抽奖」按钮跳转时带的 activeId）
READ_LOTTERY_ACT_ID = "16416"

# 跨 App 换量任务的下发渠道号。
# 默认的 PAGE_PARAMS["p2"]（104583）不会下发换量任务；
# 116298 会额外下发「打开携程做任务领取现金」，其余任务与默认渠道一致。
CROSS_APP_CHANNEL = "116298"

# 等级成长任务的 UA：必须带 zyApp 标识，否则服务端按「非 App 环境」降级
LEVEL_UA = (
    "Mozilla/5.0 (Linux; U; Android 15; 2211133C Build/AP3A.240905.015) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/100.0 "
    "Mobile Safari/537.36 zyApp/8.8.1"
)

# 「品质好书」任务的运营书单页 / 埋点上报用的移动端 UA
_TASK_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/100.0 Mobile Safari/537.36"
)

VIDEO_POSITIONS = (
    "VIDEO_PAYTASK",
    "SIGN_SHARE",
    "MARKET_SIGN_OBTAIN_VOUCHER",
    "MARKET_READ_OBTAIN_VOUCHER",
)

# share_report 的 query 需要带 App 的全套设备参数（缺了只回 OK 不落账）。
# 值取自模拟器 8.8.1 抓包（saad.ms.zhangyue.net/ad/cfg 的真实全参数）；
# p1（设备指纹）与 p3（客户端版本）实测是关键项，其余参数保持齐全最稳。
APP_DEVICE_PARAMS = {
    "pc": "10",
    "p1": "ffffffffffffffffffffffff",
    "p2": "116298",
    "p3": "18080103",     # 8.8.1 的版本号（App 内 welfare 页面透传的就是它）
    "p4": "501603",
    "p5": "19",
    "p7": "__5a10c5b990da8855",
    "p9": "2",
    "p16": "2211133C",
    "p21": "3",
    "p22": "15",
    "p25": "80810",
    "p28": "",
    "p29": "zye5b814",
    "p31": "__",
    "p33": "com.chaozh.iReaderFree",
    "p34": "Xiaomi",
}

# 各分享渠道的 open_uid = 掌阅为该渠道申请的 AppID
# （反编译自 8.8.1 `thirdAuthor.d.k`，包名 com.chaozh.iReaderFree 对应的默认值）
SHARE_APP_IDS = {
    "weixin": "wxe3c6d2c99cabd542",
    "weixin_friend": "wxe3c6d2c99cabd542",
    "qq": "100467046",
    "qq_zone": "100467046",
    "weibo": "727783337",
    "ap_share": "2015071300166986",
    "douyin": "awvazciwgugr15or",
}