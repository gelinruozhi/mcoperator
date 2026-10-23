#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦算子 McOperator —— 麦当劳点餐精算 Skill（CLI 入口）

基于麦当劳中国 MCP（https://mcp.mcd.cn）：

    stores     查附近门店
    menu       看门店菜单（支持自动定位）
    coupons    盘点卡包（临期提醒 + 可领券）
    claim      一键领取麦麦省优惠券
    plan       精算最优券组合，输出省钱报告   ← 核心命令
    compare    多渠道比价（自取 / 得来速 / 麦乐送）
    order      按最优方案真实下单（需 --confirm）
    orders     查订单 / 取消订单
    nutrition  营养成分查询（支持热量预算）
    account    麦享会积分账户
    campaign   营销活动日历
    report     省钱战报（本地累计战绩）
    tools      列出 MCP 全部工具（探险模式）
    cache      本地缓存管理

用法示例：
    python scripts/mcoperator.py stores --city 济南市 --keyword 章丘
    python scripts/mcoperator.py plan --city 济南市 --keyword 章丘 \\
        --items "麦辣鸡腿汉堡,薯条,可乐" --kcal 800
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_client import McdMcpClient, McdMcpError          # noqa: E402
from matcher import match_menu, normalize, parse_quantity  # noqa: E402
from optimizer import CartLine, Coupon, Optimizer          # noqa: E402
import report as rpt                                       # noqa: E402
import nutrition as nut                                    # noqa: E402
import wallet as wl                                        # noqa: E402
import cache as app_cache                                  # noqa: E402

CHANNELS = {
    # 名称: (orderType, beType, 门店来源, 需要地址)
    "takeaway": (1, 1, "nearby", False),      # 到店自取
    "drivethru": (1, 5, "nearby", False),     # 得来速
    "delivery": (2, 2, "delivery", True),     # 麦乐送
}

CHANNEL_DESC = {
    "takeaway": "到店自取",
    "drivethru": "得来速（车道取餐）",
    "delivery": "麦乐送（外卖配送）",
}


def _err(msg: str):
    print("✗ %s" % msg, file=sys.stderr)
    sys.exit(1)


def _as_list(data, *keys) -> list:
    """MCP 列表数据兼容提取：data 可能是 list，也可能是 {key: [...]} 的 dict。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
        # 兜底：取第一个 list 值
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def _pick_stores_nearby(client: McdMcpClient, city: str, keyword: str, be_type: int) -> list:
    resp = client.call_tool_cached("query-nearby-stores", {
        "searchType": 2, "city": city, "keyword": keyword, "beType": be_type,
    })
    return _as_list(resp.get("data"), "stores", "storeList")


def _pick_stores_delivery(client: McdMcpClient, address_id: str) -> list:
    resp = client.call_tool("delivery-query-stores", {
        "addressId": address_id, "beType": 2,
    })
    return _as_list(resp.get("data"), "stores", "storeList")


def _get_menu(client: McdMcpClient, store_code: str, order_type: int,
              be_type: int, be_code: str = "") -> dict:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    resp = client.call_tool_cached("query-meals", args)
    data = resp.get("data") or {}
    return data.get("meals") or {}


def _resolve_store(client, args, channel: str, soft: bool = False):
    """按渠道解析门店。返回 (store_dict, order_type, be_type, be_code, address_id, channel)。

    soft=True 时不直接退出，而是抛 McdMcpError（供 compare 逐渠道降级）。
    """
    def fail(msg):
        if soft:
            raise McdMcpError(msg)
        _err(msg)

    order_type, be_type, source, need_address = CHANNELS[channel]

    address_id = getattr(args, "address_id", "") or ""
    if channel == "delivery":
        if not address_id:
            addrs = _as_list(
                (client.call_tool("delivery-query-addresses", {}) or {}).get("data"),
                "addresses", "addressList")
            if not addrs:
                fail("没有可用配送地址，请先在麦当劳 App 添加，或用 --address-id 指定。")
            address_id = addrs[0].get("addressId", "")
            print("· 使用配送地址：%s（--address-id 可切换）" % addrs[0].get("address", ""))
        stores = _pick_stores_delivery(client, address_id)
    else:
        if not (args.city and args.keyword) and not args.store:
            fail("请提供 --city 和 --keyword 定位门店，或直接用 --store 指定门店编码。")
        stores = _pick_stores_nearby(client, args.city or "", args.keyword or "", be_type)

    if not stores:
        fail("附近没有找到可用的麦当劳门店。")

    store = None
    if args.store:
        for s in stores:
            if str(s.get("storeCode")) == str(args.store):
                store = s
                break
        if store is None:
            fail("门店 %s 不在当前可配送/可点餐列表中。" % args.store)
    else:
        store = stores[0]

    if len(stores) > 1 and not args.store:
        print("· 附近 %d 家门店，默认选最近的：%s（用 --store 指定其他）"
              % (len(stores), store.get("storeName")))
    be_code = store.get("beCode") or ""
    return store, order_type, be_type, be_code, address_id, channel


def _channel_of(args) -> str:
    if getattr(args, "delivery", False):
        return "delivery"
    if getattr(args, "be_type", 1) == 5:
        return "drivethru"
    return "takeaway"


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
    resp = client.call_tool_cached("query-store-coupons", args)
    return [Coupon(c) for c in (resp.get("data") or [])]


def _load_nutrition(client) -> list:
    """加载营养表（带缓存；失败返回空列表，不影响主流程）。"""
    try:
        resp = client.call_tool_cached("list-nutrition-foods", {})
        raw = resp.get("data")
        if isinstance(raw, str):
            return nut.parse_nutrition_table(raw)
        if isinstance(raw, list):
            return [nut.NutritionItem(d.get("productName", ""), "",
                                      d.get("energyKj", 0), d.get("energyKcal", 0),
                                      d.get("protein", 0), d.get("fat", 0),
                                      d.get("carbohydrate", 0), d.get("sodium", 0),
                                      d.get("calcium", 0)) for d in raw]
    except McdMcpError:
        pass
    return []


def _plan_pipeline(client, args, channel=None, soft=False):
    """完整精算流水线，返回 plan / order / compare 共用的上下文。"""
    channel = channel or _channel_of(args)
    store, order_type, be_type, be_code, address_id, channel = _resolve_store(
        client, args, channel, soft=soft)
    args.store = store.get("storeCode")
    coupons = _get_store_coupons(client, args.store, order_type, be_type, be_code)
    menu, cart = _build_cart(client, args, order_type, be_type, be_code, coupons)
    opt = Optimizer(client, args.store, order_type, be_type, be_code, verbose=args.verbose)
    result = opt.evaluate(cart, coupons)
    return {
        "store": store, "order_type": order_type, "be_type": be_type,
        "be_code": be_code, "address_id": address_id, "channel": channel,
        "coupons": coupons, "menu": menu, "cart": cart, "result": result,
    }


# --------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------- #
def cmd_stores(client, args):
    store, *_ = _resolve_store(client, args, _channel_of(args))
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
    channel = _channel_of(args)
    if not args.store:
        # 没给门店编码时自动定位（和 stores 一样）
        store, *_ = _resolve_store(client, args, channel)
        args.store = store.get("storeCode")
        print("· 自动定位门店：%s" % store.get("storeName"))
    order_type, be_type, _src, _need = CHANNELS[channel]
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
    if args.json:
        print(json.dumps(menu, ensure_ascii=False, indent=1))


def cmd_coupons(client, args):
    """卡包盘点：结构化展示 + 临期预警 + 可领券提示。"""
    if args.claim:
        _do_bind(client, args)
    raw = client.call_tool_cached("query-my-coupons", {"page": "1", "pageSize": "200"})
    text = raw.get("_raw_text", "")
    wallet = wl.parse_my_coupons(text) if text else []
    try:
        avail = client.call_tool_cached("available-coupons", {})
        claimable = wl.parse_available_coupons(avail.get("_raw_text", ""))
    except McdMcpError:
        claimable = []

    if args.json:
        print(json.dumps({"wallet": wallet, "claimable": claimable},
                         ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_wallet_report(wallet))
    unclaimed = [c for c in claimable if c.get("status") != "已领取"]
    if unclaimed:
        print("\n💡 麦麦省还有 %d 张券没领：%s" % (
            len(unclaimed), "、".join(c["title"] for c in unclaimed)))
        print("   跑 `mcoperator.py claim --claim` 一键领取。")


def _do_bind(client, args) -> str:
    """一键领券，返回结果摘要。"""
    resp = client.call_tool("auto-bind-coupons", {})
    if resp.get("success") is False:
        msg = resp.get("message") or "领券失败"
        print("⚠ 一键领券未成功：%s" % msg)
        return msg
    data = resp.get("data")
    if isinstance(data, str) and data.strip():
        print("✓ %s" % data.strip()[:200])
        return data.strip()[:200]
    if isinstance(data, dict):
        cnt = data.get("count") or data.get("num") or ""
        msg = "已领取 %s 张券" % cnt if cnt else "领取完成"
        print("✓ %s" % msg)
        return msg
    print("✓ 一键领券完成")
    return "完成"


def cmd_claim(client, args):
    claimable = []
    try:
        avail = client.call_tool_cached("available-coupons", {})
        claimable = wl.parse_available_coupons(avail.get("_raw_text", ""))
    except McdMcpError as e:
        print("⚠ 查询可领券失败：%s" % e)
    bind_msg = ""
    if args.claim:
        bind_msg = _do_bind(client, args)
    if args.json:
        print(json.dumps({"claimable": claimable, "claimed": bool(args.claim)},
                         ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_claim_report(claimable, bool(args.claim), bind_msg))


def cmd_plan(client, args):
    ctx = _plan_pipeline(client, args)
    store, channel, cart, result = (ctx["store"], ctx["channel"],
                                    ctx["cart"], ctx["result"])

    print("· 门店：%s（%s）" % (store.get("storeName"), CHANNEL_DESC[channel]))
    print("· 购物车：%s" % "、".join("%s×%d" % (l.name, l.quantity) for l in cart))
    print("· 门店可用券：%d 张%s" % (len(ctx["coupons"]),
          "（%s）" % "、".join(c.title for c in ctx["coupons"]) if ctx["coupons"] else ""))

    # 热量预算（可选）
    nutrition_summary = None
    if args.kcal:
        items = _load_nutrition(client)
        if items:
            nutrition_summary = nut.cart_nutrition(cart, items)
        else:
            print("⚠ 营养数据不可用，跳过热量速览。")

    md = rpt.render_plan_report(result, store, [l.to_dict() for l in cart],
                                CHANNEL_DESC[channel],
                                nutrition_summary=nutrition_summary,
                                kcal_budget=args.kcal or 0)
    print("\n" + md)

    if args.json:
        out = dict(result)
        if nutrition_summary:
            out["nutrition"] = {
                "total": nutrition_summary["total"],
                "missing": [l.name for l in nutrition_summary["missing"]],
            }
        print("\n" + json.dumps(out, ensure_ascii=False, indent=1))

    # 记账（参考总价 = 最优方案原价口径，含券商品原价）
    best = result["best"]
    if best and not args.no_record:
        saved = best["original"] - best["price"]
        if saved > 0:
            rpt.record_saving(saved, note="、".join(c["title"] for c in best.get("coupons", [])))
            print("\n· 本次节省 %s 已记入省钱战报（mcoperator.py report 查看）" % rpt.yuan(saved))


def cmd_compare(client, args):
    """同一购物车在三个渠道分别精算，比出最便宜的。"""
    rows = []
    items_desc = "、".join(_parse_items(args.items))
    for channel in ("takeaway", "drivethru", "delivery"):
        row = {"channel": CHANNEL_DESC[channel], "ok": False, "note": ""}
        # 每个渠道独立定位门店：清掉上一个渠道选定的 store / be-code
        args.store = ""
        args.be_code = ""
        try:
            ctx = _plan_pipeline(client, args, channel=channel, soft=True)
        except McdMcpError as e:
            row["note"] = str(e).split("，")[0][:20] or "不可用"
            rows.append(row)
            print("· %s：不可用（%s）" % (row["channel"], row["note"]))
            continue
        store = ctx["store"]
        best = ctx["result"]["best"]
        base = ctx["result"]["baseline"]
        row["store"] = store.get("storeName", "?")
        if best:
            row.update(ok=True, price=best["price"], original=best["original"],
                       saved=best["original"] - best["price"])
        else:
            row.update(ok=True, price=base["price"], original=base["original"], saved=0)
        rows.append(row)
        print("· %s：%s，最优实付 %s" % (row["channel"], row["store"],
                                        rpt.yuan(row["price"])))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_compare_report(rows, items_desc))


def cmd_order(client, args):
    ctx = _plan_pipeline(client, args)
    store = ctx["store"]
    order_type, be_type, be_code = ctx["order_type"], ctx["be_type"], ctx["be_code"]
    address_id, channel = ctx["address_id"], ctx["channel"]
    cart, result = ctx["cart"], ctx["result"]
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


def cmd_orders(client, args):
    """查订单详情 / 取消订单。"""
    if not args.order_id:
        _err("请提供 --order-id <订单号>。")
    if args.cancel:
        if not args.confirm:
            print("\n即将取消订单 %s。" % args.order_id)
            print("⚠ 取消不可撤销。确认请追加 --confirm。")
            return
        resp = client.call_tool("cancel-order", {
            "orderId": args.order_id,
            "cancelReasonCode": args.reason or "1",
        })
        if resp.get("success") is False:
            _err("取消失败：%s" % (resp.get("message") or "未知错误"))
        print("\n✓ 订单 %s 已取消。" % args.order_id)
        return
    resp = client.call_tool("query-order", {"orderId": args.order_id})
    order = resp.get("data") or {}
    if args.json:
        print(json.dumps(order, ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_order_report(order))


def cmd_nutrition(client, args):
    items = _load_nutrition(client)
    if not items:
        _err("没有拿到营养数据（可先联网跑一次生成缓存）。")
    if args.query:
        hits = []
        for q in _parse_items(args.query):
            hit = nut.match_nutrition(q, items)
            if hit:
                hits.append(hit[0])
            else:
                print("⚠ 没找到「%s」的营养数据" % q)
        if args.json:
            print(json.dumps([i.to_dict() for i in hits], ensure_ascii=False, indent=1))
            return
        print("\n" + rpt.render_nutrition_report(hits, args.query))
    else:
        if args.json:
            print(json.dumps([i.to_dict() for i in
                              sorted(items, key=lambda x: -x.energy_kcal)],
                             ensure_ascii=False, indent=1))
            return
        print("\n" + rpt.render_nutrition_report(items))


def cmd_account(client, args):
    resp = client.call_tool_cached("query-my-account", {})
    account = resp.get("data") or {}
    lottery = None
    try:
        lresp = client.call_tool_cached("query-lottery-info", {})
        lottery = lresp.get("data") or None
    except McdMcpError:
        pass
    if args.json:
        print(json.dumps({"account": account, "lottery": lottery},
                         ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_account_report(account, lottery))


def cmd_campaign(client, args):
    tool_args = {}
    if args.date:
        tool_args["specifiedDate"] = args.date
    resp = client.call_tool_cached("campaign-calendar", tool_args)
    days = wl.parse_campaign_calendar(resp.get("_raw_text", ""))
    if args.json:
        print(json.dumps(days, ensure_ascii=False, indent=1))
        return
    print("\n" + rpt.render_campaign_report(days))


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


def cmd_cache(client, args):
    if args.clear:
        n = app_cache.clear()
        print("✓ 已清空本地缓存（%d 个文件）。" % n)
    else:
        print("缓存目录：%s" % app_cache.CACHE_DIR)
        print("用 `mcoperator.py cache --clear` 清空。")


# --------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    # 全局参数同时挂主解析器与每个子解析器（SUPPRESS 避免子命令默认值覆盖主命令取值），
    # 这样 `mcoperator --verbose plan` 和 `mcoperator plan --verbose` 都能用。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    p = argparse.ArgumentParser(
        prog="mcoperator",
        description="麦算子 McOperator —— 麦当劳点餐精算 Skill（基于麦当劳中国 MCP）")
    p.add_argument("--token", help="MCP Token（默认读环境变量 MCD_MCP_TOKEN）")
    p.add_argument("--verbose", action="store_true", help="打印调试信息")
    p.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    p.add_argument("--offline", action="store_true",
                   help="离线模式：只用本地缓存，不访问网络")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, need_items=False):
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

    sp = sub.add_parser("stores", help="查附近门店", parents=[common])
    add_common(sp)
    sp.set_defaults(func=cmd_stores)

    sp = sub.add_parser("menu", help="看门店菜单（不给 --store 时自动定位）",
                        parents=[common])
    add_common(sp)
    sp.add_argument("--filter", help="按名称过滤")
    sp.set_defaults(func=cmd_menu)

    sp = sub.add_parser("coupons", help="盘点卡包：临期提醒 + 可领券提示",
                        parents=[common])
    sp.add_argument("--claim", action="store_true", help="先一键领取麦麦省优惠券")
    sp.set_defaults(func=cmd_coupons)

    sp = sub.add_parser("claim", help="一键领取麦麦省优惠券", parents=[common])
    sp.add_argument("--claim", action="store_true", help="执行一键领取（否则只看列表）")
    sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("plan", help="精算最优券组合（核心命令）", parents=[common])
    add_common(sp, need_items=True)
    sp.add_argument("--kcal", type=int, default=0, help="热量预算（kcal），超出会提醒")
    sp.add_argument("--no-record", action="store_true", help="不记入省钱战报")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("compare", help="多渠道比价：自取/得来速/麦乐送哪个便宜",
                        parents=[common])
    add_common(sp, need_items=True)
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("order", help="按最优方案真实下单", parents=[common])
    add_common(sp, need_items=True)
    sp.add_argument("--confirm", action="store_true", help="确认真实下单（否则只预览）")
    sp.set_defaults(func=cmd_order)

    sp = sub.add_parser("orders", help="查订单 / 取消订单", parents=[common])
    sp.add_argument("--order-id", dest="order_id", default="", help="订单号")
    sp.add_argument("--cancel", action="store_true", help="取消订单（需 --confirm）")
    sp.add_argument("--reason", default="1", help="取消原因码，默认 1")
    sp.add_argument("--confirm", action="store_true", help="确认取消")
    sp.set_defaults(func=cmd_orders)

    sp = sub.add_parser("nutrition", help="营养成分查询", parents=[common])
    sp.add_argument("--query", help="餐品名，逗号分隔；不给则列出全部")
    sp.set_defaults(func=cmd_nutrition)

    sp = sub.add_parser("account", help="麦享会积分账户", parents=[common])
    sp.set_defaults(func=cmd_account)

    sp = sub.add_parser("campaign", help="营销活动日历", parents=[common])
    sp.add_argument("--date", help="查询日期 yyyy-MM-dd（默认当月全部）")
    sp.set_defaults(func=cmd_campaign)

    sp = sub.add_parser("report", help="省钱战报", parents=[common])
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("tools", help="列出 MCP 全部工具", parents=[common])
    sp.set_defaults(func=cmd_tools)

    sp = sub.add_parser("cache", help="本地缓存管理", parents=[common])
    sp.add_argument("--clear", action="store_true", help="清空缓存")
    sp.set_defaults(func=cmd_cache)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        client = McdMcpClient(token=args.token, verbose=args.verbose,
                              offline=args.offline)
        if not args.offline:
            client.initialize()
        args.func(client, args)
    except McdMcpError as e:
        _err(str(e))
    except KeyboardInterrupt:
        _err("已取消。")
    except Exception as e:  # 兜底：给出可执行的排障提示，而不是裸 traceback
        if args.verbose:
            raise
        _err("发生了未预期的错误：%s: %s\n"
             "（加 --verbose 查看完整堆栈；若是网络问题，可加 --offline 用缓存重试）"
             % (type(e).__name__, e))


if __name__ == "__main__":
    main()
