#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阅读时长任务探测：找出「阅读赚代金券」的分钟数到底来自哪里。

背景
----
福利中心有两套阅读任务接口：

  /welfare/sign/v2/read          旧版活动（10 档，差额分钟数）
  /welfare/market/task/read_get  新版活动（H5 里 isNewActivity 为真时走这个）

此前用 `/postal/reading_time/report` 伪造上报，接口回 code:0，但旧接口的
`current` 一直不动。要判断这是「两套数据源」还是「伪造报文被服务端丢弃」，
必须先看清真实阅读时是哪一路在变。

本脚本只读，不发送任何伪造上报
------------------------------
  --dump    拉取两套阅读接口 + 日常任务，落盘原始 JSON 并打印结构
  --watch   轮询阅读分钟数，用于在真机上边读边观察（定位计数何时、因何变化）

用法
----
  python src/read_probe.py                 # 等价于 --dump
  python src/read_probe.py --watch 300     # 连续观察 300 秒

配合真机阅读的判读方式见 docs/阅读时长探测方案.md。

凭证：与主项目一致（命令行 → 环境变量 → config.yaml）。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ireader_welfare import auth, config
from ireader_welfare.welfare import WelfareClient

OUT_DIR = "output"


# ---------------------------- 工具 ----------------------------
def flatten(obj, prefix="", depth=0, max_depth=3):
    """把嵌套 JSON 压成 (路径, 值) 列表，便于肉眼比对字段名。"""
    rows = []
    if depth > max_depth:
        return rows
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)) and depth < max_depth:
                rows.extend(flatten(v, path, depth + 1, max_depth))
            else:
                rows.append((path, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            path = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)) and depth < max_depth:
                rows.extend(flatten(v, path, depth + 1, max_depth))
            else:
                rows.append((path, v))
        if len(obj) > 3:
            rows.append((f"{prefix}[…]", f"共 {len(obj)} 项，仅展示前 3 项"))
    return rows


def find_minutes(obj, key_hit=None):
    """递归找出所有像「分钟数」的字段，返回 [(路径, 值)]。"""
    hits = []
    KEYWORDS = ("current", "minute", "time", "progress", "read_num", "duration")

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{path}.{k}" if path else str(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if any(kw in str(k).lower() for kw in KEYWORDS):
                        hits.append((p, v))
                elif isinstance(v, (dict, list)):
                    walk(v, p)
        elif isinstance(o, list):
            for i, v in enumerate(o[:20]):
                walk(v, f"{path}[{i}]")

    walk(obj)
    if key_hit:
        hits = [h for h in hits if key_hit in h[0]]
    return hits


def read_current(client):
    """返回（旧接口 current, 新接口分钟数列表），取不到时返回 None。"""
    old = None
    new = []
    try:
        body = client.read_tasks().get("body") or {}
        for row in find_minutes(body):
            if row[0].endswith("current"):
                old = row[1]
                break
    except Exception as e:
        print(f"  旧接口读取失败: {e}")
    try:
        body = client.read_tasks_v2().get("body") or {}
        new = find_minutes(body)
    except Exception as e:
        print(f"  新接口读取失败: {e}")
    return old, new


def save_raw(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUT_DIR, f"read_probe_{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------- 动作 ----------------------------
def do_dump(client):
    print("\n=== 旧接口 /welfare/sign/v2/read ===")
    old = client.read_tasks()
    body = old.get("body") or {}
    for path, val in flatten(body):
        print(f"  {path:<42} {val}")
    print(f"  原始 JSON → {save_raw('old', old)}")

    print("\n=== 新接口 /welfare/market/task/read_get ===")
    new = client.read_tasks_v2()
    print(f"  code={new.get('code')}  msg={new.get('msg')}")
    nbody = new.get("body")
    if nbody in (None, [], {}):
        print("  body 为空 —— 该账号可能仍走旧活动（H5 的 isNewActivity 为假）")
    else:
        for path, val in flatten(nbody):
            print(f"  {path:<42} {val}")
    print(f"  原始 JSON → {save_raw('new', new)}")

    print("\n=== 日常任务 /welfare/sign/v2/task ===")
    tasks = client.tasks()
    for t in (tasks.get("body") or []):
        tgt = t.get("target_list") or [{}]
        print(f"  [{t.get('status')}] {t.get('name'):<22} "
              f"目标 {tgt[0].get('value')}  奖励 {t.get('award')}")
    print(f"  原始 JSON → {save_raw('tasks', tasks)}")

    print("\n判读：")
    print("  新接口有体 → 数据源很可能就在它的字段里，先看有没有 read_time/current 之外的来源标识")
    print("  新接口为空 → 账号在旧活动，计数由服务端另行汇总，需真机抓包定位上报入口")


def do_watch(client, seconds, interval):
    print(f"\n每 {interval}s 采一次，持续 {seconds}s。现在开始读书，观察哪一路先变。\n")
    print(f"{'用时':>6}  {'旧接口 current':>14}  {'新接口分钟字段':<40}")
    start = time.time()
    base_old = None
    while True:
        elapsed = int(time.time() - start)
        old, new = read_current(client)
        if base_old is None:
            base_old = old
        delta = "" if (old is None or base_old is None) else f"（{old - base_old:+d}）"
        new_txt = ", ".join(f"{p.split('.')[-1]}={v}" for p, v in new[:3]) or "-"
        print(f"{elapsed:>5}s  {str(old):>14}{delta:<8} {new_txt:<40}")
        if elapsed >= seconds:
            break
        time.sleep(min(interval, max(1, seconds - elapsed)))


# ---------------------------- 入口 ----------------------------
def main():
    ap = argparse.ArgumentParser(description="阅读时长任务探测（只读）")
    ap.add_argument("--phone", help="手机号，默认读环境变量 / config.yaml")
    ap.add_argument("--password", help="密码")
    ap.add_argument("--config", help="配置文件路径")
    ap.add_argument("--dump", action="store_true", help="拉取阅读接口原始结构（默认）")
    ap.add_argument("--watch", type=int, metavar="秒", help="轮询阅读分钟数，观察变化")
    ap.add_argument("--interval", type=int, default=15, help="轮询间隔秒数，默认 15")
    args = ap.parse_args()

    cfg = config.load(args.config)
    phone, password = config.resolve_credentials(cfg, args.phone, args.password)
    if not phone or not password:
        print("缺少账号：请设置 IREADER_PHONE / IREADER_PWD，或填 config.yaml")
        return 2

    print(f"登录中 ... ({phone})")
    cred = auth.login(phone, password)
    client = WelfareClient(cred)
    print(f"登录成功: {cred.nick}  ({cred.usr})")

    if args.watch:
        do_watch(client, args.watch, args.interval)
    else:
        do_dump(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
