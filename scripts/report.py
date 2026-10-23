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
                        channel_desc: str, nutrition_summary: dict | None = None,
                        kcal_budget: int = 0) -> str:
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
    if nutrition_summary:
        lines.append(render_cart_nutrition(nutrition_summary, kcal_budget))
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
# 新命令渲染器
# --------------------------------------------------------------------- #
def render_wallet_report(wallet: list) -> str:
    """卡包券明细 + 临期预警（按紧急程度排序）。"""
    import wallet as wl

    lines = ["# 麦算子 · 我的卡包", ""]
    if not wallet:
        lines.append("卡包是空的。先跑 `mcoperator.py claim` 一键领券吧。")
        return "\n".join(lines)

    urgent = [c for c in wallet if wl.expiry_alert(c)]
    if urgent:
        urgent.sort(key=lambda c: c.get("expiresInDays") or 0)
        lines.append("## ⚠ 临期提醒")
        lines.append("")
        for c in urgent:
            lines.append("- **%s**（%s）—— %s" % (
                c["title"], wl.format_validity_left(c), wl.expiry_alert(c)))
        lines.append("")

    lines.append("## 全部券（%d 张）" % len(wallet))
    lines.append("")
    lines.append("| 券 | 用券价 | 状态 | 有效期至 |")
    lines.append("|---|---|---|---|")
    for c in sorted(wallet, key=lambda x: (x.get("expiresInDays") is None,
                                           x.get("expiresInDays") or 0)):
        tags = "、".join(c.get("tags", []))
        lines.append("| %s | %s | %s | %s |" % (
            c["title"], c.get("price", "?"),
            wl.expiry_alert(c) or "有效", c.get("validTo", "?")[:10]))
        if tags:
            lines.append("| ↳ 标签 | %s | | |" % tags)
    lines.append("")
    lines.append("---")
    lines.append("*「今日到期」的券别浪费，`mcoperator.py plan` 会自动把它们算进最优方案。*")
    return "\n".join(lines)


def render_claim_report(claimable: list, claimed: bool, bind_msg: str) -> str:
    lines = ["# 麦算子 · 领券中心", ""]
    if claimable:
        lines.append("## 麦麦省可领（%d 张）" % len(claimable))
        lines.append("")
        for c in claimable:
            mark = "" if c.get("status") == "已领取" else " 🆕"
            lines.append("- %s（%s）%s" % (c.get("title", "?"),
                                          c.get("status", "?"), mark))
        lines.append("")
    else:
        lines.append("当前没有可领的麦麦省券。")
        lines.append("")
    if claimed:
        lines.append("已执行一键领取：%s" % (bind_msg or "完成"))
        lines.append("")
    lines.append("> 提示：`claim --claim` 先领券再盘点；领到的券会自动进入 `plan` 的优化池。")
    return "\n".join(lines)


def render_account_report(account: dict, lottery: dict | None) -> str:
    lines = ["# 麦算子 · 麦享会账户", ""]
    if not account:
        lines.append("没有拿到积分账户数据。")
        return "\n".join(lines)
    lines.append("- 可用积分：**%s**" % account.get("availablePoint", "0"))
    lines.append("- 累计积分：%s（已用 %s，已过期 %s）" % (
        account.get("accumulativePoint", "0"),
        account.get("usedPoint", "0"), account.get("expiredPoint", "0")))
    expiring = account.get("currentMouthExpirePoint", "0")
    if expiring and str(expiring) != "0":
        lines.append("- ⚠ 本月即将过期：**%s** 分" % expiring)
    lines.append("- 积分类型：%s" % account.get("currency", "麦享会积分"))
    if lottery:
        lines.append("")
        lines.append("## 积分抽奖")
        lines.append("")
        lines.append("- 活动：%s（%s）" % (lottery.get("activityName", "?"),
                                        lottery.get("activityStatusText", "")))
        lines.append("- 单次消耗：%s 积分" % lottery.get("drawPoint", "?"))
        decision = lottery.get("drawDecision") or {}
        if decision:
            lines.append("- 当前资格：%s" % ("可以抽" if decision.get("resourceEligible")
                                            else decision.get("reason", "不可抽")))
        prizes = lottery.get("prizes") or []
        if prizes:
            names = "、".join(p.get("name", "?") for p in prizes[:6])
            lines.append("- 奖品池（%d 项）：%s" % (len(prizes), names))
    lines.append("")
    lines.append("---")
    lines.append("*积分可在麦麦商城兑换餐品券（MCP `mall-points-products` 查看）。*")
    return "\n".join(lines)


def render_campaign_report(days: list) -> str:
    lines = ["# 麦算子 · 营销活动日历", ""]
    if not days:
        lines.append("本月没有查询到营销活动。")
        return "\n".join(lines)
    for d in days:
        head = d.get("date") or "未知日期"
        if d.get("status"):
            head += "（%s）" % d["status"]
        lines.append("## %s" % head)
        lines.append("")
        acts = d.get("activities", [])
        if not acts:
            lines.append("- （无活动详情）")
        for a in acts:
            if a.get("content"):
                lines.append("- **%s**：%s" % (a["title"], a["content"][:120]))
            else:
                lines.append("- **%s**" % a["title"])
        lines.append("")
    lines.append("---")
    lines.append("*活动以麦当劳官方渠道为准；`campaign --date 2026-10-24` 可查指定日期前后三天。*")
    return "\n".join(lines)


def render_nutrition_report(items: list, query: str = "") -> str:
    lines = ["# 麦算子 · 营养成分表", ""]
    if query:
        lines.append("查询：**%s**" % query)
        lines.append("")
        lines.append("| 餐品 | 热量(kcal) | 蛋白质(g) | 脂肪(g) | 碳水(g) | 钠(mg) |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for it in items:
            lines.append("| %s | %d | %s | %s | %s | %s |" % (
                it.name, it.energy_kcal, it.protein, it.fat,
                it.carbohydrate, it.sodium))
        if not items:
            lines.append("| （没有匹配的餐品） | | | | | |")
    else:
        lines.append("共 %d 个餐品，按热量从高到低：" % len(items))
        lines.append("")
        lines.append("| 餐品 | 热量(kcal) | 蛋白质(g) | 脂肪(g) | 碳水(g) |")
        lines.append("|---|---:|---:|---:|---:|")
        for it in sorted(items, key=lambda x: -x.energy_kcal)[:40]:
            lines.append("| %s | %d | %s | %s | %s |" % (
                it.name, it.energy_kcal, it.protein, it.fat, it.carbohydrate))
    lines.append("")
    lines.append("---")
    lines.append("*数据来自麦当劳 MCP `list-nutrition-foods`；`plan --kcal 800` 可在精算时核对热量预算。*")
    return "\n".join(lines)


def render_cart_nutrition(summary: dict, budget: int = 0) -> str:
    """plan 命令里的热量小结。"""
    t = summary["total"]
    lines = ["", "## 热量速览（kcal）", ""]
    lines.append("| 餐品 | 单价热量 | 小计 |")
    lines.append("|---|---:|---:|")
    for line, item in summary["matched"]:
        qty = max(1, line.quantity)
        lines.append("| %s × %d | %d | %d |" % (
            line.name, qty, item.energy_kcal, item.energy_kcal * qty))
    lines.append("| **合计** | | **%d** |" % t["kcal"])
    lines.append("")
    nut = ("蛋白质 %.0fg · 脂肪 %.0fg · 碳水 %.0fg · 钠 %.0fmg"
           % (t["protein"], t["fat"], t["carbohydrate"], t["sodium"]))
    lines.append("- %s" % nut)
    if budget:
        if t["kcal"] > budget:
            lines.append("- ⚠ 超出热量预算 **%d kcal**（预算 %d），考虑换小杯饮料或去掉一样？"
                         % (t["kcal"] - budget, budget))
        else:
            lines.append("- ✓ 在热量预算 %d kcal 之内，还剩 %d kcal 余量。"
                         % (budget, budget - t["kcal"]))
    if summary["missing"]:
        names = "、".join(l.name for l in summary["missing"])
        lines.append("- ⚠ %s 未查到营养数据（官方表未收录或名称对不上）。" % names)
    return "\n".join(lines)


def render_compare_report(rows: list, items_desc: str) -> str:
    """多渠道比价。rows: [{channel, store, ok, price, original, saved, note}]"""
    lines = ["# 麦算子 · 多渠道比价", ""]
    lines.append("- 购物车：%s" % items_desc)
    lines.append("")
    lines.append("| 渠道 | 门店 | 原价 | 最优实付 | 节省 |")
    lines.append("|---|---|---:|---:|---:|")
    for r in rows:
        if not r.get("ok"):
            lines.append("| %s | %s | — | — | %s |" % (
                r["channel"], r.get("store", "—"), r.get("note", "不可用")))
            continue
        lines.append("| %s | %s | %s | **%s** | %s |" % (
            r["channel"], r.get("store", "—"), yuan(r["original"]),
            yuan(r["price"]), yuan(r["saved"])))
    lines.append("")
    ok_rows = [r for r in rows if r.get("ok")]
    if ok_rows:
        best = min(ok_rows, key=lambda r: r["price"])
        lines.append("**结论：%s 最便宜，实付 %s。**" % (best["channel"], yuan(best["price"])))
    lines.append("")
    lines.append("---")
    lines.append("*各渠道分别经过完整券组合精算；外送可能另收配送费，以结算页为准。*")
    return "\n".join(lines)


def render_order_report(order: dict) -> str:
    lines = ["# 麦算子 · 订单详情", ""]
    if not order:
        lines.append("没有拿到订单数据。")
        return "\n".join(lines)
    lines.append("- 订单号：**%s**" % order.get("orderId", "?"))
    lines.append("- 状态：%s" % order.get("status", order.get("orderStatus", "?")))
    if order.get("storeName"):
        lines.append("- 门店：%s" % order["storeName"])
    if order.get("createTime"):
        lines.append("- 下单时间：%s" % order["createTime"])
    items = order.get("items") or order.get("productList") or []
    if items:
        lines.append("")
        lines.append("| 餐品 | 数量 | 小计 |")
        lines.append("|---|---:|---:|")
        for it in items:
            name = it.get("productName") or it.get("name") or it.get("productCode", "?")
            lines.append("| %s | %s | %s |" % (
                name, it.get("quantity", "?"),
                yuan(it.get("subtotal", it.get("price", 0)))))
    lines.append("")
    lines.append("---")
    lines.append("*取消订单：`orders --order-id <订单号> --cancel`（需再次确认）。*")
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
