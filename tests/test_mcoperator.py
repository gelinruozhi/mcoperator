# -*- coding: utf-8 -*-
"""麦算子离线单元测试：全部使用录制的真实 MCP 响应快照，不访问网络。

运行：python tests/test_mcoperator.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from mcd_client import extract_embedded_json                     # noqa: E402
from matcher import match_menu, normalize, parse_quantity        # noqa: E402
from optimizer import (CartLine, Coupon, build_items,            # noqa: E402
                       cents_to_yuan, split_coupons)
import report as rpt                                             # noqa: E402


# ---------------- 录制的真实响应快照（2026-10-23，门店 3350100） ----------------
STORE_COUPONS_RESPONSE = """## Response Structure
- **重要**: 同一券ID可对应多个券码，展示时按券ID分组
## 输出格式
## {data[].title}(共{券码}张)
|券ID|券码|有效期|适用商品|商品编码|
|:---:|:---:|:---:|:---:|:---:|
|{data[].couponId}|{首个data[].couponCode}|{data[].tradeDateTime}|{data[].products[].productName}|{data[].products[].productCode}|
{"success":true,"code":200,"data":[{"title":"11.9元麦辣鸡腿堡","couponId":"A255280326E3328D179C362B7B8DDA65","couponCode":"MCD61949000G35102V006","products":[{"productCode":"1440","productName":"麦辣鸡腿汉堡"}]},{"title":"1024码力翻倍","couponId":"5FBE3138F2B0B0026A2F163B2AE8211F","couponCode":"MCD62007F1002L40659J6","products":[{"productCode":"9900016523","productName":"龙焰鸡腿堡两件套"}]}]}"""

MENU = {
    "1440": {"name": "麦辣鸡腿汉堡", "currentPrice": "23", "originalPrice": "23"},
    "4810": {"name": "薯条", "currentPrice": "14.5", "originalPrice": "14.5"},
    "9900008751": {"name": "可乐", "currentPrice": "9.5", "originalPrice": "9.5"},
    "9900005456": {"name": "麦辣鸡腿汉堡三件套", "currentPrice": "34", "originalPrice": "47.5"},
}


class TestExtractJson(unittest.TestCase):
    def test_markdown_with_template_placeholder(self):
        """含 {data[].title} 模板占位符的响应也能正确提取业务 JSON。"""
        data = extract_embedded_json(STORE_COUPONS_RESPONSE)
        self.assertIsNotNone(data)
        self.assertTrue(data["success"])
        self.assertEqual(len(data["data"]), 2)
        self.assertEqual(data["data"][0]["couponId"], "A255280326E3328D179C362B7B8DDA65")

    def test_original_response_marker(self):
        text = "字段说明...\n## Original Response\n\n{\"success\":true,\"data\":{\"a\":1}}"
        data = extract_embedded_json(text)
        self.assertEqual(data["data"]["a"], 1)

    def test_plain_json(self):
        self.assertEqual(extract_embedded_json('{"success":true}')["success"], True)

    def test_no_json(self):
        self.assertIsNone(extract_embedded_json("纯文本，没有 JSON"))
        self.assertIsNone(extract_embedded_json(""))


class TestMatcher(unittest.TestCase):
    def test_parse_quantity(self):
        self.assertEqual(parse_quantity("薯条x2"), ("薯条", 2))
        self.assertEqual(parse_quantity("薯条*3"), ("薯条", 3))
        self.assertEqual(parse_quantity("2个薯条"), ("薯条", 2))
        self.assertEqual(parse_quantity("两份薯条"), ("薯条", 2))
        self.assertEqual(parse_quantity("薯条"), ("薯条", 1))

    def test_match_menu(self):
        m = match_menu("麦辣鸡腿堡", MENU)
        self.assertEqual(m["code"], "1440")  # 口语简称也能命中
        m2 = match_menu("薯条", MENU)
        self.assertEqual(m2["code"], "4810")

    def test_match_with_quantity(self):
        m = match_menu("可乐x2", MENU)
        self.assertEqual(m["code"], "9900008751")
        self.assertEqual(m["quantity"], 2)

    def test_no_match(self):
        self.assertIsNone(match_menu("不存在的餐品xyz", MENU))

    def test_normalize(self):
        self.assertEqual(normalize(" 麦辣，鸡腿堡！ "), "麦辣鸡腿堡")


class TestOptimizer(unittest.TestCase):
    def _coupons(self):
        return [
            Coupon({"couponId": "C1", "couponCode": "K1", "title": "11.9元麦辣鸡腿堡",
                    "products": [{"productCode": "1440", "productName": "麦辣鸡腿汉堡"}]}),
            Coupon({"couponId": "C2", "couponCode": "K2", "title": "1024券",
                    "products": [{"productCode": "9900016523", "productName": "龙焰鸡腿堡两件套"}]}),
            Coupon({"couponId": "C3", "couponCode": "K3", "title": "店外券",
                    "products": [{"productCode": "9999", "productName": "别家店的券"}]}),
        ]

    def test_split_coupons(self):
        cart = [CartLine("1440", "麦辣鸡腿汉堡", 1)]
        attachable, others = split_coupons(self._coupons(), cart, set())
        self.assertEqual([c.coupon_id for c in attachable], ["C1"])
        self.assertEqual(len(others), 2)

    def test_build_items_attaches_coupon(self):
        cart = [CartLine("1440", "麦辣鸡腿汉堡", 1), CartLine("4810", "薯条", 1)]
        c1 = self._coupons()[0]
        items = build_items(cart, [c1])
        self.assertEqual(len(items), 2)
        burger = next(i for i in items if i["productCode"] == "1440")
        self.assertEqual(burger["couponId"], "C1")
        fries = next(i for i in items if i["productCode"] == "4810")
        self.assertNotIn("couponId", fries)

    def test_build_items_splits_quantity_for_multiple_coupons(self):
        """两张同款券 + 数量 2 → 拆成两行各挂一张券。"""
        cart = [CartLine("1440", "麦辣鸡腿汉堡", 2)]
        coupons = [Coupon({"couponId": "C1", "couponCode": "K1", "title": "券A",
                           "products": [{"productCode": "1440"}]}),
                   Coupon({"couponId": "C2", "couponCode": "K2", "title": "券B",
                           "products": [{"productCode": "1440"}]})]
        items = build_items(cart, coupons)
        self.assertEqual(len(items), 2)
        self.assertEqual({i.get("couponId") for i in items}, {"C1", "C2"})

    def test_coupon_sku_in_cart_is_attachable(self):
        """券商品在购物车里时，其券属于可挂券。"""
        cart = [CartLine("9900016523", "龙焰鸡腿堡两件套", 1, source="coupon")]
        attachable, _ = split_coupons(self._coupons(), cart, set())
        self.assertEqual([c.coupon_id for c in attachable], ["C2"])

    def test_cents_to_yuan(self):
        self.assertEqual(cents_to_yuan(2048), "¥20.48")
        self.assertEqual(cents_to_yuan(0), "¥0.00")


class TestReport(unittest.TestCase):
    def test_rank_of(self):
        self.assertEqual(rpt.rank_of(0)[1], "省钱学徒")
        self.assertEqual(rpt.rank_of(4999)[1], "省钱学徒")
        self.assertEqual(rpt.rank_of(5000)[1], "券场入门")
        self.assertEqual(rpt.rank_of(999999)[1], "券界传说")

    def test_yuan(self):
        self.assertEqual(rpt.yuan(1110), "¥11.10")

    def test_render_plan_report_with_coupon_sku(self):
        plan = {
            "baseline": {"original": 2400, "price": 2400, "discount": 0},
            "best": {"original": 5850, "price": 4448, "discount": 1402,
                     "coupons": [{"title": "1024券", "validity": "2026-10-23 至 10-29"}],
                     "productList": [{"productCode": "9900016523", "originalSubtotal": 3450},
                                     {"productCode": "4810", "originalSubtotal": 1450},
                                     {"productCode": "9900008751", "originalSubtotal": 950}]},
            "addonSuggestions": [],
        }
        cart = [{"name": "龙焰鸡腿堡两件套", "quantity": 1, "source": "coupon", "code": "9900016523"},
                {"name": "薯条", "quantity": 1, "source": "menu", "currentPrice": "14.5"},
                {"name": "可乐", "quantity": 1, "source": "menu", "currentPrice": "9.5"}]
        md = rpt.render_plan_report(plan, {"storeName": "测试餐厅", "address": "测试地址"},
                                    cart, "到店自取")
        self.assertIn("¥58.50", md)
        self.assertIn("¥44.48", md)
        self.assertIn("¥14.02", md)
        self.assertIn("券商品", md)
        self.assertIn("¥34.50", md)  # 券商品参考原价


if __name__ == "__main__":
    unittest.main(verbosity=2)
