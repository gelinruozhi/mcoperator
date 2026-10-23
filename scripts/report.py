# -*- coding: utf-8 -*-
"""省钱报告渲染 + 本地省钱账本（ledger）。

报告为 Markdown 格式，终端直接可读，也可粘贴到 Issue / 聊天里分享。
账本记录每一次 plan 的省钱战绩，累计成「麦门段位」。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

LEDGER_DIR = os.environ.get(
    "MCOPERATOR_HOME",
    str(Path.home() / ".mcoperator"),
)
LEDGER_FILE = os.path.join(LEDGER_DIR, "ledger.json")

# 累计节省金额（分）→ 段位
RANKS = [
    (0, "省钱学徒", "刚领到算盘，先从看清价格开始"),
    (5000, "券场入门", "知道优惠券是要用的"),
    (20000, "精算伙计", "点餐前会问一句麦算子"),
    (50000, "麦门账房", "每一单都算得明明白白"),
    (100000, "券场精英", "朋友点餐前都会来问你"),
    (200000, "省钱大师", "麦当劳看见你都紧张"),
    (500000, "券界传说", "关于你的传说已在麦门流传"),
]


def yuan(cents) -> str:
    try:
        return "¥%.2f" % (float(cents) / 100.0)
    except (TypeError, ValueError):
        return "¥?"


def rank_of(total_saved_cents: int) -> tuple:
    current = RANKS[0]
    for threshold, name, desc in RANKS:
        if total_saved_cents >= threshold:
            current = (threshold, name, desc)
    return current


# --------------------------------------------------------------------- #
# 报告渲染
# --------------------------------------------------------------------- #
def render_plan_report(plan: dict, store: dict, cart_lines: list,
                        channel_desc: str) -> str:
    base = plan["baseline"]
    best = plan["best"]

    # 参考总价口径：最优方案的原价（含券商品的原价），
    # 因为券商品不领券时接口无法单独定价，不能放进「不使用优惠券」基准。
    reference = best["original"] if best else base["original"]
    has_coupon_sku = any(l.get("source") == "coupon" for l in cart_lines)

    # 餐品原价映射（来自最优方案的 productList）
    orig_by_code = {}
    if best:
        for p in best.get("productList", []):
            orig_by_code[p.get("productCode")] = p.get("originalSubtotal", 0)

    lines = []
    lines.append("# 麦算子 · 点餐精算报告")
    lines.append("")
    lines.append("- 门店：**%s**（%s）" % (store.get("storeName", "?"), store.get("address", "")))
    lines.append("- 渠道：%s" % channel_desc)
    lines.append("- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    lines.append("## 购物车")
    lines.append("")
    lines.append("| 餐品 | 数量 | 参考价 |")
    lines.append("|---|---:|---:|")
    for l in cart_lines:
        if l.get("source") == "coupon":
            ref = orig_by_code.get(l["code"])
            price = yuan(ref) + "（券商品）" if ref else "随券购买"
        else:
            price = l.get("currentPrice") or l.get("originalPrice") or "?"
        lines.append("| %s | %d | %s |" % (l["name"], l["quantity"], price))
    lines.append("")
    lines.append("## 精算结果")
    lines.append("")
    lines.append("| 方案 | 原价 | 实付 | 节省 |")
    lines.append("|---|---:|---:|---:|")
    if has_coupon_sku:
        lines.append("| 不使用优惠券 | — | 无法单独定价* | — |")
    else:
        lines.append("| 不使用优惠券 | %s | %s | — |" % (yuan(base["original"]), yuan(base["price"])))
    if best:
        saved = reference - best["price"]
        pct = (saved / reference * 100) if reference else 0
        lines.append("| **最优券组合** | %s | **%s** | **%s（%.1f%%）** |"
                     % (yuan(reference), yuan(best["price"]), yuan(saved), pct))
    else:
        lines.append("| 最优券组合 | — | 当前没有可用优惠券 | — |")
    lines.append("")
    if has_coupon_sku:
        lines.append("> \\* 购物车含「券商品」——只能随对应优惠券购买的专属 SKU，"
                     "不领券时麦当劳接口无法单独定价，因此不计入「不使用优惠券」基准。")
        lines.append("")
    if best:
        lines.append("### 最优方案用了这些券")
        lines.append("")
        for c in best.get("coupons", []):
            lines.append("- **%s**（%s）" % (c["title"], c.get("validity", "")))
        lines.append("")
    if plan.get("addonSuggestions"):
        lines.append("### 加购建议（券商品，只能随券购买）")
        lines.append("")
        for s in plan["addonSuggestions"]:
            c = s["coupon"]
            lines.append("- **%s**：加购实付 %s（参考原价 %s）—— 用 %s 兑换"
                         % (c["title"], yuan(s["addonPrice"]), yuan(s["originalPrice"]),
                            "、".join(p.get("productName", "") for p in c.get("products", []))))
        lines.append("")
    lines.append("---")
    lines.append("*价格与库存以麦当劳官方渠道实时结果为准；本报告由麦算子基于麦当劳 MCP 自动生成。*")
    return "\n".join(lines)


def render_coupon_report(my_coupons: list, claimable: list) -> str:
    lines = ["# 麦算子 · 券包盘点", ""]
    if my_coupons:
        lines.append("## 我的卡包（%d 张）" % len(my_coupons))
        lines.append("")
        lines.append("| 券 | 用券价 | 有效期 |")
        lines.append("|---|---|---|")
        for c in my_coupons:
            lines.append("| %s | %s | %s |" % (c.get("title", "?"),
                                               c.get("price", "?"), c.get("validity", "?")))
        lines.append("")
    if claimable:
        lines.append("## 麦麦省可领（%d 张）" % len(claimable))
        lines.append("")
        for c in claimable:
            lines.append("- %s" % c.get("title", "?"))
        lines.append("")
    if not my_coupons and not claimable:
        lines.append("券包是空的。先跑 `mcoperator.py claim` 一键领券吧。")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# 账本
# --------------------------------------------------------------------- #
def _load_ledger() -> dict:
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"entries": [], "totalSaved": 0}


def record_saving(saved_cents: int, note: str = "") -> dict:
    """记录一次省钱战绩，返回更新后的账本。"""
    ledger = _load_ledger()
    ledger["entries"].append({
        "ts": int(time.time()),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "saved": int(saved_cents),
        "note": note,
    })
    ledger["totalSaved"] = int(ledger.get("totalSaved", 0)) + int(saved_cents)
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
    return ledger


def render_battle_report(ledger: dict) -> str:
    total = int(ledger.get("totalSaved", 0))
    _, rank_name, rank_desc = rank_of(total)
    entries = ledger.get("entries", [])
    lines = ["# 麦算子 · 省钱战报", ""]
    lines.append("> %s" % rank_desc)
    lines.append("")
    lines.append("- 累计参战：**%d 单**" % len(entries))
    lines.append("- 累计节省：**%s**" % yuan(total))
    lines.append("- 当前段位：**%s**" % rank_name)
    lines.append("")
    if entries:
        lines.append("## 最近战绩")
        lines.append("")
        lines.append("| 时间 | 节省 | 备注 |")
        lines.append("|---|---:|---|")
        for e in entries[-10:][::-1]:
            lines.append("| %s | %s | %s |" % (e.get("time", "?"), yuan(e.get("saved", 0)),
                                               e.get("note", "")))
        lines.append("")
    lines.append("---")
    lines.append("*省下的每一分，都是赚的。*")
    return "\n".join(lines)
