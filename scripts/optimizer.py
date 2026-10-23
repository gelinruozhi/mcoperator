# -*- coding: utf-8 -*-
"""优惠券组合优化器：用官方 calculate-price 真值试算，找出实付最低的券组合。

核心思路
--------
麦当劳 calculate-price 接受「商品行 + 每行挂一张券」的结构，因此：

1. **可挂券**（适用商品在购物车中）：枚举其子集，每个子集构建一次 items
   调用 calculate-price 试算，取实付最低。子集规模 2^k，k 通常为 1~3；
   超过 MAX_EXHAUSTIVE 时退化为贪心 + 一次两两交换改进。
2. **券商品**（适用商品编码不在常规菜单中，如 9900016523 龙焰鸡腿堡两件套）：
   不带券时接口根本无法定价，只能随券购买。它们只会增加总价，
   因此不作为优化变量，而是单独计算「加购价」生成省钱建议。
3. 同一商品使用多张同款券时，自动拆成多行 quantity=1 分别挂券
   （calculate-price 每行只接受一张券）。

所有价格判定均以官方接口返回为准，不做本地估算。
"""

from __future__ import annotations

import itertools

MAX_EXHAUSTIVE = 10  # 可挂券全枚举上限，超过则贪心


class CartLine:
    """购物车中的一行。

    source: "menu"（常规菜单商品）或 "coupon"（券商品，只能随券购买）
    """

    def __init__(self, code: str, name: str, quantity: int,
                 current_price=None, original_price=None, source: str = "menu"):
        self.code = code
        self.name = name
        self.quantity = quantity
        self.current_price = current_price
        self.original_price = original_price
        self.source = source

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "quantity": self.quantity,
                "currentPrice": self.current_price, "originalPrice": self.original_price,
                "source": self.source}


class Coupon:
    """一张券（来自 query-store-coupons）。"""

    def __init__(self, raw: dict):
        self.coupon_id = raw.get("couponId", "")
        self.coupon_code = raw.get("couponCode", "")
        self.title = raw.get("title", "")
        self.validity = raw.get("tradeDateTime", "")
        self.products = raw.get("products", []) or []
        self.promotion_id = raw.get("promotionId", "")

    @property
    def product_codes(self) -> list:
        return [p.get("productCode", "") for p in self.products]

    @property
    def product_names(self) -> str:
        return "、".join(p.get("productName", "") for p in self.products)

    def to_dict(self) -> dict:
        return {"couponId": self.coupon_id, "couponCode": self.coupon_code,
                "title": self.title, "validity": self.validity,
                "products": self.products}


def split_coupons(coupons: list, cart: list, menu_codes: set) -> tuple[list, list]:
    """把券分成 (可挂券, 非可挂券)。

    可挂券：适用商品编码命中购物车中的某一行。
    非可挂券：适用商品是菜单商品但不在购物车（可作加购建议），
    或适用商品编码不在菜单中（券商品，只能随券购买）。
    """
    attachable, others = [], []
    cart_codes = {line.code for line in cart}
    for c in coupons:
        if any(pc in cart_codes for pc in c.product_codes):
            attachable.append(c)
        else:
            others.append(c)
    return attachable, others


def build_items(cart: list, chosen: list) -> list:
    """构建 calculate-price 的 items。

    - 购物车每行按 quantity 展开成 qty=1 的原子行，方便挂多张券
    - 每张被选中的券挂到其适用商品的某个未挂券原子行上（一张券一行）
    """
    atoms = []
    for line in cart:
        for _ in range(max(1, line.quantity)):
            atoms.append({"productCode": line.code, "quantity": 1})

    used = [False] * len(atoms)
    assignment = {}
    for c in chosen:
        for i, atom in enumerate(atoms):
            if not used[i] and atom["productCode"] in c.product_codes:
                assignment[c.coupon_id] = i
                used[i] = True
                break

    items = []
    for i, atom in enumerate(atoms):
        item = {"productCode": atom["productCode"], "quantity": atom["quantity"]}
        for c in chosen:
            if assignment.get(c.coupon_id) == i:
                item["couponId"] = c.coupon_id
                item["couponCode"] = c.coupon_code
                break
        items.append(item)
    return items


def _price_of(resp: dict) -> dict:
    data = resp.get("data", resp)
    return {
        "original": data.get("originalPrice", 0),
        "price": data.get("price", 0),
        "discount": data.get("discount", 0),
        "delivery": data.get("deliveryPrice", 0),
        "productList": data.get("productList", []),
    }


class Optimizer:
    """券组合优化器。client 为 McdMcpClient。"""

    def __init__(self, client, store_code: str, order_type: int = 1,
                 be_type: int = 1, be_code: str = "", verbose: bool = False):
        self.client = client
        self.store_code = store_code
        self.order_type = order_type
        self.be_type = be_type
        self.be_code = be_code
        self.verbose = verbose
        self.trials = 0

    def _calc(self, items: list) -> dict:
        args = {
            "storeCode": self.store_code,
            "orderType": self.order_type,
            "beType": self.be_type,
            "items": items,
        }
        if self.be_code:
            args["beCode"] = self.be_code
        resp = self.client.call_tool("calculate-price", args)
        self.trials += 1
        if self.verbose:
            print("[optimizer] 第 %d 次试算：%d 行商品" % (self.trials, len(items)))
        return _price_of(resp)

    def _try(self, cart: list, subset: list) -> dict | None:
        """试算一个券子集；有任何一张券挂不上就跳过（返回 None）。"""
        if not subset:
            return None
        items = build_items(cart, subset)
        attached = {it.get("couponId") for it in items if it.get("couponId")}
        if attached != {c.coupon_id for c in subset}:
            return None
        price = self._calc(items)
        price["coupons"] = [c.to_dict() for c in subset]
        return price

    def evaluate(self, cart: list, coupons: list) -> dict:
        """主入口：返回基准价、最优组合、全部成功试算与加购建议。"""
        base_items = [{"productCode": l.code, "quantity": l.quantity} for l in cart]
        baseline = self._calc(base_items)

        attachable, others = split_coupons(coupons, cart, set())
        results = []
        for subset in self._candidate_subsets(attachable, cart):
            r = self._try(cart, subset)
            if r is not None:
                results.append(r)
        results.sort(key=lambda r: r["price"])
        best = results[0] if results else None

        # 非可挂券：计算随券加购价，生成「加购建议」
        addon_suggestions = []
        for c in others:
            if not c.product_codes:
                continue
            try:
                resp = self.client.call_tool("calculate-price", {
                    "storeCode": self.store_code,
                    "orderType": self.order_type,
                    "beType": self.be_type,
                    "items": [{"productCode": c.product_codes[0], "quantity": 1,
                               "couponId": c.coupon_id, "couponCode": c.coupon_code}],
                })
                p = _price_of(resp)
                addon_suggestions.append({
                    "coupon": c.to_dict(),
                    "addonPrice": p["price"],
                    "originalPrice": p["original"],
                })
            except Exception:
                continue

        return {
            "baseline": baseline,
            "best": best,
            "results": results,
            "attachable": [c.to_dict() for c in attachable],
            "addonSuggestions": addon_suggestions,
            "trials": self.trials,
        }

    def _candidate_subsets(self, coupons: list, cart: list) -> list:
        """生成待试算券子集：小规模全枚举，大规模贪心+交换。"""
        n = len(coupons)
        if n == 0:
            return []
        if n <= MAX_EXHAUSTIVE:
            subsets = []
            for k in range(n, 0, -1):
                for combo in itertools.combinations(coupons, k):
                    subsets.append(list(combo))
            return subsets
        # 贪心：从空集开始，每次尝试加入剩余券中最能降价的
        greedy, remaining = [], list(coupons)
        while remaining:
            best_combo, best_price = None, None
            for c in remaining:
                trial = greedy + [c]
                r = self._try(cart, trial)
                if r is not None and (best_price is None or r["price"] < best_price):
                    best_combo, best_price = trial, r["price"]
            if best_combo is None:
                break
            greedy = best_combo
            remaining = [c for c in remaining if c not in greedy]
        # 一次两两交换改进：换出贪心解中的一张，换入一张未用的
        improved = True
        while improved:
            improved = False
            cur = self._try(cart, greedy)
            if cur is None:
                break
            for c in remaining:
                for i in range(len(greedy)):
                    trial = greedy[:i] + greedy[i + 1:] + [c]
                    r_new = self._try(cart, trial)
                    if r_new is not None and r_new["price"] < cur["price"]:
                        greedy, improved = trial, True
                        remaining = [x for x in remaining if x not in greedy]
                        break
                if improved:
                    break
        return [greedy] if greedy else [[c] for c in coupons[:1]]


def cents_to_yuan(cents) -> str:
    """接口价格单位为分，格式化为 ¥ 字符串。"""
    try:
        return "¥%.2f" % (float(cents) / 100.0)
    except (TypeError, ValueError):
        return "¥?"
