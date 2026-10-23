#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦算子 McOperator —— 麦当劳点餐精算 Skill（CLI 入口）

基于麦当劳中国 MCP（https://mcp.mcd.cn）：
    stores   查附近门店
    menu     看门店菜单
    coupons  盘点券包 / 一键领券
    plan     精算最优券组合，输出省钱报告   ← 核心命令
    order    按最优方案真实下单（需 --confirm）
    report   省钱战报（本地累计战绩）
    tools    列出 MCP 全部工具（探险模式）

用法示例：
    python scripts/mcoperator.py stores --city 济南市 --keyword 章丘
    python scripts/mcoperator.py plan --city 济南市 --keyword 章丘 \\
        --items "麦辣鸡腿汉堡,薯条,可乐"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_client import McdMcpClient, McdMcpError          # noqa: E402
from matcher import match_menu, normalize, parse_quantity  # noqa: E402
from optimizer import CartLine, Coupon, Optimizer         # noqa: E402
import report as rpt                                      # noqa: E402

CHANNELS = {
    # 名称: (orderType, beType, 需要门店来源, 需要地址)
    "takeaway": (1, 1, "nearby", False),      # 到店自取
    "drivethru": (1, 5, "nearby", False),     # 得来速
    "delivery": (2, 2, "delivery", True),     # 麦乐送
}


def _err(msg: str):
    print("✗ %s" % msg, file=sys.stderr)
    sys.exit(1)


def _pick_stores_nearby(client: McdMcpClient, city: str, keyword: str, be_type: int) -> list:
    resp = client.call_tool("query-nearby-stores", {
        "searchType": 2, "city": city, "keyword": keyword, "beType": be_type,
    })
    return (resp.get("data") or [])


def _pick_stores_delivery(client: McdMcpClient, address_id: str) -> list:
    resp = client.call_tool("delivery-query-stores", {
        "addressId": address_id, "beType": 2,
    })
    return (resp.get("data") or [])


def _get_menu(client: McdMcpClient, store_code: str, order_type: int,
              be_type: int, be_code: str = "") -> dict:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    resp = client.call_tool("query-meals", args)
    data = resp.get("data") or {}
    return data.get("meals") or {}


def _resolve_store(client, args, be_type: int):
    """返回 (store_dict, order_type, be_type, be_code, address_id, channel_name)。"""
    channel = "delivery" if args.delivery else ("drivethru" if args.be_type == 5 else "takeaway")
    order_type, be_type, source, need_address = CHANNELS[channel]

    address_id = getattr(args, "address_id", "") or ""
    if channel == "delivery":
        if not address_id:
            addrs = (client.call_tool("delivery-query-addresses", {}) or {}).get("data") or []
            if not addrs:
                _err("没有可用配送地址，请先在麦当劳 App 添加，或用 --address-id 指定。")
            address_id = addrs[0].get("addressId", "")
            print("· 使用配送地址：%s（--address-id 可切换）" % addrs[0].get("address", ""))
        stores = _pick_stores_delivery(client, address_id)
    else:
        if not (args.city and args.keyword) and not args.store:
            _err("请提供 --city 和 --keyword 定位门店，或直接用 --store 指定门店编码。")
        stores = _pick_stores_nearby(client, args.city or "", args.keyword or "", be_type)

    if not stores:
        _err("附近没有找到可用的麦当劳门店。")

    store = None
    if args.store:
        for s in stores:
            if str(s.get("storeCode")) == str(args.store):
                store = s
                break
        if store is None:
            _err("门店 %s 不在当前可配送/可点餐列表中。" % args.store)
    else:
        store = stores[0]

    if len(stores) > 1 and not args.store:
        print("· 附近 %d 家门店，默认选最近的：%s（用 --store 指定其他）"
              % (len(stores), store.get("storeName")))
    be_code = store.get("beCode") or ""
    return store, order_type, be_type, be_code, address_id, channel


def _parse_items(items_arg: str) -> list:
    items = [x.strip() for x in (items_arg or "").replace("，", ",").split(",") if x.strip()]
    if not items:
        _err("--items 不能为空，例如：--items \"麦辣鸡腿汉堡,薯条,可乐\"")
    return items


def _build_cart(client, args, order_type, be_type, be_code, coupons=None):
    """构建购物车。

    匹配优先级（从高到低）：
      1. 菜单精确匹配   2. 券商品精确匹配
      3. 菜单模糊匹配   4. 券商品模糊匹配
    「券商品」是只能随券购买的专属 SKU（如 9900016523），必须走券路径。
    """
    menu = _get_menu(client, args.store, order_type, be_type, be_code)
    if not menu:
        _err("门店 %s 当前没有可售菜单（可能未营业）。" % args.store)
    coupons = coupons or []

    # 券商品池：(name, code, coupon)
    coupon_pool = []
    for c in coupons:
        for p in c.products:
            coupon_pool.append((p.get("productName", ""), p.get("productCode", ""), c))

    def exact_match(name, pool):
        n = normalize(name)
        for item in pool:
            if normalize(item[0]) == n:
                return item
        return None

    def fuzzy_coupon(name):
        from matcher import _score
        best, best_score = None, 0.0
        for item in coupon_pool:
            s = _score(name, item[0])
            if s > best_score:
                best, best_score = item, s
        return best if best_score >= 0.6 else None

    cart, unmatched = [], []
    for q in _parse_items(args.items):
        # 1. 菜单精确
        m = None
        nq = normalize(q)
        for code, meal in menu.items():
            if normalize(meal.get("name", "")) == nq:
                m = match_menu(q, menu)
                break
        # 2. 券商品精确
        if m is None:
            hit = exact_match(q, coupon_pool)
            if hit:
                _, qty = parse_quantity(q)
                cart.append(CartLine(hit[1], hit[0], qty, source="coupon"))
                print("· 「%s」是券商品，只能使用「%s」购买，已自动带券。" % (hit[0], hit[2].title))
                continue
        # 3. 菜单模糊
        if m is None:
            m = match_menu(q, menu)
        if m is not None:
            cart.append(CartLine(m["code"], m["name"], m["quantity"],
                                 current_price=m["currentPrice"],
                                 original_price=m["originalPrice"],
                                 source="menu"))
            continue
        # 4. 券商品模糊
        hit = fuzzy_coupon(q)
        if hit:
            _, qty = parse_quantity(q)
            cart.append(CartLine(hit[1], hit[0], qty, source="coupon"))
            print("· 「%s」是券商品，只能使用「%s」购买，已自动带券。" % (hit[0], hit[2].title))
            continue
        unmatched.append(q)
    if not cart:
        _err("没能识别任何餐品：%s" % "、".join(unmatched))
    if unmatched:
        print("⚠ 未识别的餐品已忽略：%s" % "、".join(unmatched))
    return menu, cart


def _get_store_coupons(client, store_code, order_type, be_type, be_code) -> list:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    resp = client.call_tool("query-store-coupons", args)
    return [Coupon(c) for c in (resp.get("data") or [])]


CHANNEL_DESC = {
    "takeaway": "到店自取",
    "drivethru": "得来速（车道取餐）",
    "delivery": "麦乐送（外卖配送）",
}


# --------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------- #
def cmd_stores(client, args):
    store, *_ = _resolve_store(client, args, args.be_type or 1)
    print("\n已选定门店：%s" % store.get("storeName"))
    print("  地址：%s" % store.get("address"))
    print("  编码：%s%s" % (store.get("storeCode"),
                          " / beCode %s" % store["beCode"] if store.get("beCode") else ""))
    print("  营业：%s-%s" % (store.get("businessStartTime"), store.get("businessEndTime")))


def _disp_width(s: str) -> int:
    """字符串显示宽度（CJK 记 2）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    """按显示宽度左对齐补空格。"""
    return s + " " * max(0, width - _disp_width(s))


def cmd_menu(client, args):
    if not args.store:
        _err("menu 需要 --store 指定门店编码。")
    order_type = 2 if args.delivery else 1
    be_type = args.be_type if not args.delivery else 2
    be_code = args.be_code or ""
    menu = _get_menu(client, args.store, order_type, be_type, be_code)
    if not menu:
        _err("没有拿到菜单数据。")
    print("\n门店 %s 菜单（%d 个餐品）：\n" % (args.store, len(menu)))
    rows = []
    for code, m in menu.items():
        name = m.get("name", "")
        if args.filter and args.filter not in name:
            continue
        price = m.get("currentPrice") or "—"
        orig = m.get("originalPrice") or ""
        tag = m.get("discountType") or ""
        rows.append((name, code, price, orig, tag))
    rows.sort(key=lambda r: str(r[0]))
    for name, code, price, orig, tag in rows:
        line = "  %s %s %s" % (_pad(name, 30), _pad(code, 11), price)
        if orig and orig != price:
            line += "（原价 %s）" % orig
        if tag:
            line += " [%s]" % tag
        print(line)


def cmd_coupons(client, args):
    if args.claim:
        resp = client.call_tool("auto-bind-coupons", {})
        text = json.dumps(resp, ensure_ascii=False)
        print("已执行一键领券。\n")
    my = client.call_tool("query-my-coupons", {"page": "1", "pageSize": "200"})
    # query-my-coupons 返回的是 Markdown 文本，_raw_text 形式
    raw = my.get("_raw_text", "")
    if raw:
        print(raw[:4000])
    else:
        data = my.get("data") or []
        parsed = []
        for c in data:
            parsed.append({
                "title": c.get("title") or c.get("couponTitle") or "?",
                "price": c.get("price") or "",
                "validity": c.get("validity") or c.get("tradeDateTime") or "",
            })
        print(rpt.render_coupon_report(parsed, []))


def cmd_plan(client, args):
    store, order_type, be_type, be_code, address_id, channel = _resolve_store(
        client, args, args.be_type or 1)
    args.store = store.get("storeCode")
    coupons = _get_store_coupons(client, args.store, order_type, be_type, be_code)
    menu, cart = _build_cart(client, args, order_type, be_type, be_code, coupons)

    print("· 门店：%s（%s）" % (store.get("storeName"), CHANNEL_DESC[channel]))
    print("· 购物车：%s" % "、".join("%s×%d" % (l.name, l.quantity) for l in cart))
    print("· 门店可用券：%d 张%s" % (len(coupons),
          "（%s）" % "、".join(c.title for c in coupons) if coupons else ""))

    opt = Optimizer(client, args.store, order_type, be_type, be_code, verbose=args.verbose)
    result = opt.evaluate(cart, coupons)

    md = rpt.render_plan_report(result, store, [l.to_dict() for l in cart],
                                CHANNEL_DESC[channel])
    print("\n" + md)

    if args.json:
        print("\n" + json.dumps(result, ensure_ascii=False, indent=1))

    # 记账（参考总价 = 最优方案原价口径，含券商品原价）
    best = result["best"]
    if best and not args.no_record:
        saved = best["original"] - best["price"]
        if saved > 0:
            rpt.record_saving(saved, note="、".join(c["title"] for c in best.get("coupons", [])))
            print("\n· 本次节省 %s 已记入省钱战报（mcoperator.py report 查看）" % rpt.yuan(saved))


def cmd_order(client, args):
    store, order_type, be_type, be_code, address_id, channel = _resolve_store(
        client, args, args.be_type or 1)
    args.store = store.get("storeCode")
    coupons = _get_store_coupons(client, args.store, order_type, be_type, be_code)
    menu, cart = _build_cart(client, args, order_type, be_type, be_code, coupons)
    opt = Optimizer(client, args.store, order_type, be_type, be_code)
    result = opt.evaluate(cart, coupons)
    best = result["best"]

    items = [{"productCode": l.code, "quantity": l.quantity} for l in cart]
    used = []
    if best:
        from optimizer import build_items
        items = build_items(cart, [Coupon(c) for c in best.get("coupons", [])])
        used = [c["title"] for c in best.get("coupons", [])]

    name_by_code = {l.code: l.name for l in cart}
    print("\n即将创建订单：")
    print("  门店：%s（%s）" % (store.get("storeName"), CHANNEL_DESC[channel]))
    for it in items:
        name = name_by_code.get(it["productCode"], it["productCode"])
        print("  - %s × %d%s" % (name, it["quantity"],
                                 "（用券：%s）" % it.get("couponCode", "") if it.get("couponId") else ""))
    if used:
        print("  优惠券：%s" % "、".join(used))
    print("  预计实付：%s" % rpt.yuan(best["price"] if best else result["baseline"]["price"]))

    if not args.confirm:
        print("\n⚠ 这是真实下单（产生真实支付）。确认请追加 --confirm。")
        return

    order_args = {
        "storeCode": args.store,
        "orderType": order_type,
        "beType": be_type,
        "items": items,
    }
    if be_code:
        order_args["beCode"] = be_code
    if address_id:
        order_args["addressId"] = address_id
    resp = client.call_tool("create-order", order_args)
    data = resp.get("data") or {}
    print("\n✓ 订单已创建")
    if data.get("orderId"):
        print("  订单号：%s" % data.get("orderId"))
    if data.get("payUrl") or data.get("paymentUrl"):
        print("  支付链接：%s" % (data.get("payUrl") or data.get("paymentUrl")))
    print(json.dumps(data, ensure_ascii=False)[:800])


def cmd_report(client, args):
    ledger = rpt._load_ledger()
    print(rpt.render_battle_report(ledger))


def cmd_tools(client, args):
    tools = client.list_tools()
    print("麦当劳 MCP 当前开放 %d 个工具：\n" % len(tools))
    for t in tools:
        print("- %s" % t.get("name"))
        desc = (t.get("description") or "").strip().splitlines()
        if desc:
            print("    %s" % desc[0][:80])


# --------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcoperator",
        description="麦算子 McOperator —— 麦当劳点餐精算 Skill（基于麦当劳中国 MCP）")
    p.add_argument("--token", help="MCP Token（默认读环境变量 MCD_MCP_TOKEN）")
    p.add_argument("--verbose", action="store_true", help="打印调试信息")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, need_items=False, need_store=False):
        sp.add_argument("--city", help="城市名（定位门店）")
        sp.add_argument("--keyword", help="位置关键词（定位门店）")
        sp.add_argument("--store", help="门店编码 storeCode")
        sp.add_argument("--be-code", dest="be_code", default="", help="业务编码 beCode（得来速/外送）")
        sp.add_argument("--be-type", dest="be_type", type=int, default=1,
                        choices=[1, 5], help="1-到店自取（默认），5-得来速")
        sp.add_argument("--delivery", action="store_true", help="麦乐送外送场景")
        sp.add_argument("--address-id", dest="address_id", default="", help="配送地址 ID")
        if need_items:
            sp.add_argument("--items", help="想吃的餐品，逗号分隔，如 \"麦辣鸡腿汉堡,薯条x2,可乐\"")
        if need_store:
            pass

    sp = sub.add_parser("stores", help="查附近门店")
    add_common(sp)
    sp.set_defaults(func=cmd_stores)

    sp = sub.add_parser("menu", help="看门店菜单")
    add_common(sp, need_store=True)
    sp.add_argument("--filter", help="按名称过滤")
    sp.set_defaults(func=cmd_menu)

    sp = sub.add_parser("coupons", help="盘点券包 / 一键领券")
    sp.add_argument("--claim", action="store_true", help="先一键领取麦麦省优惠券")
    sp.set_defaults(func=cmd_coupons)

    sp = sub.add_parser("plan", help="精算最优券组合（核心命令）")
    add_common(sp, need_items=True)
    sp.add_argument("--json", action="store_true", help="输出原始 JSON 结果")
    sp.add_argument("--no-record", action="store_true", help="不记入省钱战报")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("order", help="按最优方案真实下单")
    add_common(sp, need_items=True)
    sp.add_argument("--confirm", action="store_true", help="确认真实下单（否则只预览）")
    sp.set_defaults(func=cmd_order)

    sp = sub.add_parser("report", help="省钱战报")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("tools", help="列出 MCP 全部工具")
    sp.set_defaults(func=cmd_tools)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        client = McdMcpClient(token=args.token, verbose=args.verbose)
        client.initialize()
        args.func(client, args)
    except McdMcpError as e:
        _err(str(e))
    except KeyboardInterrupt:
        _err("已取消。")


if __name__ == "__main__":
    main()
