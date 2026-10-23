# -*- coding: utf-8 -*-
"""麦算子离线单元测试（二）：营养 / 钱包 / 缓存 / 新渲染器 / CLI 解析。

全部使用录制的真实 MCP 响应快照，不访问网络。
运行：python tests/test_extras.py
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import nutrition as nut                                    # noqa: E402
import wallet as wl                                        # noqa: E402
import cache as app_cache                                  # noqa: E402
import report as rpt                                       # noqa: E402
import mcoperator as mo                                    # noqa: E402


# ---------------- 录制的真实响应快照 ----------------
NUTRITION_RAW = """[3]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  麦辣鸡腿汉堡,null,2025,485,24,24,42,1208,96
  中薯条,null,1210,289,4,12,38,165,18
  大薯条,null,1587,379,6,16,50,216,23
  小薯条,null,877,210,3,9,28,120,13
  可乐中杯,null,614,147,0,0,36,0,0"""

MY_COUPONS_RAW = """# 您的优惠券列表

共 3 张可用优惠券 | 第 1/1 页 | 每页 50 条

## 11.9元麦辣鸡腿堡
- **优惠**: ¥11.9 (用券价格)
- **有效期**: 2026-10-19 10:30-2026-10-23 23:59 周一、二、三、四、五 10:30-23:59
- **领取时间**: 今日收到
- **标签**: 今日到期、到店专用、外送专用

<img src="https://example.com/a.png" alt="x" height="auto" width="300">

## 1024"码"力翻倍
- **优惠**: ¥20.48 (用券价格)
- **有效期**: 2026-10-23 00:00-2026-10-29 23:59 10:30-23:59
- **标签**: 到店专用

## 19.9元王牌炸鸡任选2
- **优惠**: ¥19.9 (用券价格)
- **有效期**: 2026-11-01 10:30-2026-11-30 23:59 10:30-23:59
- **标签**: 外送专用"""

AVAILABLE_RAW = """### 麦麦省优惠券列表：
- 优惠券标题：11.9元麦辣鸡腿堡 \\
  状态：已领取 \\
  优惠券图片：\\
    <img src="https://img.mcd.cn/a.png" alt="11.9元麦辣鸡腿堡" height="auto" width="300">
- 优惠券标题：免费脆薯饼 \\
  状态：未领取 \\
  优惠券图片：\\
    <img src="https://img.mcd.cn/b.png" alt="免费脆薯饼" height="auto" width="300">"""

CAMPAIGN_RAW = """### 当前时间：2026-10-23 14:26:33

### 活动列表：


#### 2026年10月20日 往期回顾

-   **活动标题**：麦当劳 X PEACEMINUSONE
    **活动内容介绍**：GD同款联名复古棒球帽
任意餐品消费加39.9元即可得

#### 2026年10月23日 进行中

-   **活动标题**：蓝莓爆爆珠麦旋风上新
    **活动内容介绍**：一口爆汁"""


class TestNutrition(unittest.TestCase):
    def test_parse_table(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        self.assertEqual(len(items), 5)
        burger = items[0]
        self.assertEqual(burger.name, "麦辣鸡腿汉堡")
        self.assertEqual(burger.energy_kcal, 485)
        self.assertEqual(burger.protein, 24)
        self.assertEqual(burger.sodium, 1208)
        self.assertEqual(burger.description, "")  # null → 空

    def test_parse_table_skips_garbage(self):
        items = nut.parse_nutrition_table("not a header\n\n坏行,只有两段\n  巨无霸,null,2139,513,27,26,42,961,140")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "巨无霸")

    def test_match_exact_and_quantity(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        hit = nut.match_nutrition("中薯条x2", items)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0].name, "中薯条")
        self.assertEqual(hit[1], 2)

    def test_fuzzy_tie_prefers_table_order(self):
        """「薯条」同时命中中/大/小薯条时，取表中靠前的中薯条（经典默认）。"""
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        hit = nut.match_nutrition("薯条", items)
        self.assertEqual(hit[0].name, "中薯条")

    def test_no_match(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        self.assertIsNone(nut.match_nutrition("不存在的食物xyz", items))

    def test_cart_nutrition(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        cart = [mo.CartLine("1440", "麦辣鸡腿汉堡", 1),
                mo.CartLine("4810", "中薯条", 2),
                mo.CartLine("9999", "神秘新品", 1)]
        summary = nut.cart_nutrition(cart, items)
        self.assertEqual(summary["total"]["kcal"], 485 + 289 * 2)
        self.assertEqual(len(summary["matched"]), 2)
        self.assertEqual([l.name for l in summary["missing"]], ["神秘新品"])


class TestWalletParsing(unittest.TestCase):
    def test_parse_my_coupons(self):
        coupons = wl.parse_my_coupons(MY_COUPONS_RAW)
        self.assertEqual(len(coupons), 3)
        first = coupons[0]
        self.assertEqual(first["title"], "11.9元麦辣鸡腿堡")
        self.assertEqual(first["price"], "¥11.9 (用券价格)")
        self.assertEqual(first["validFrom"], "2026-10-19 10:30")
        self.assertEqual(first["validTo"], "2026-10-23 23:59")
        self.assertIn("到店专用", first["tags"])
        self.assertIsNotNone(first["expiresInDays"])

    def test_expiry_alert(self):
        today = {"expiresInDays": 0}
        soon = {"expiresInDays": 2}
        later = {"expiresInDays": 10}
        gone = {"expiresInDays": -1}
        unknown = {"expiresInDays": None}
        self.assertEqual(wl.expiry_alert(today), "今日到期")
        self.assertEqual(wl.expiry_alert(soon), "2 天后到期")
        self.assertEqual(wl.expiry_alert(later), "")
        self.assertEqual(wl.expiry_alert(gone), "已过期")
        self.assertEqual(wl.expiry_alert(unknown), "")

    def test_parse_available_coupons(self):
        claimable = wl.parse_available_coupons(AVAILABLE_RAW)
        self.assertEqual(len(claimable), 2)
        self.assertEqual(claimable[0]["title"], "11.9元麦辣鸡腿堡")
        self.assertEqual(claimable[0]["status"], "已领取")
        self.assertEqual(claimable[1]["status"], "未领取")

    def test_parse_campaign_calendar(self):
        days = wl.parse_campaign_calendar(CAMPAIGN_RAW)
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0]["date"], "2026-10-20")
        self.assertEqual(days[0]["status"], "往期回顾")
        self.assertEqual(days[0]["activities"][0]["title"], "麦当劳 X PEACEMINUSONE")
        self.assertEqual(days[1]["date"], "2026-10-23")
        self.assertEqual(days[1]["status"], "进行中")
        self.assertIn("一口爆汁", days[1]["activities"][0]["content"])

    def test_format_validity_left(self):
        self.assertEqual(wl.format_validity_left({"expiresInDays": 0}), "今天最后一天")
        self.assertEqual(wl.format_validity_left({"expiresInDays": 5}), "还剩 5 天")


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_cache")
        self._orig = app_cache.CACHE_DIR
        app_cache.CACHE_DIR = self.tmp

    def tearDown(self):
        app_cache.CACHE_DIR = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get_roundtrip(self):
        app_cache.put("query-meals", {"storeCode": "3350100"}, {"meals": {"1": {}}})
        got = app_cache.get("query-meals", {"storeCode": "3350100"})
        self.assertEqual(got, {"meals": {"1": {}}})

    def test_miss_returns_none(self):
        self.assertIsNone(app_cache.get("query-meals", {"storeCode": "nope"}))

    def test_ttl_expiry(self):
        app_cache.put("now-time-info", {}, {"t": 1})
        self.assertIsNone(app_cache.get("now-time-info", {}, max_age=-1))

    def test_clear(self):
        app_cache.put("a", {}, 1)
        app_cache.put("b", {}, 2)
        n = app_cache.clear()
        self.assertEqual(n, 2)


class TestNewRenderers(unittest.TestCase):
    def test_wallet_report_shows_expiry_alert(self):
        wallet = wl.parse_my_coupons(MY_COUPONS_RAW)
        md = rpt.render_wallet_report(wallet)
        self.assertIn("临期提醒", md)
        self.assertIn("11.9元麦辣鸡腿堡", md)
        self.assertIn("今日到期", md)

    def test_compare_report_picks_cheapest(self):
        rows = [
            {"channel": "到店自取", "ok": True, "store": "A店",
             "price": 3590, "original": 4700, "saved": 1110},
            {"channel": "得来速", "ok": True, "store": "B店",
             "price": 3540, "original": 4600, "saved": 1060},
            {"channel": "麦乐送", "ok": False, "store": "—", "note": "定位失败"},
        ]
        md = rpt.render_compare_report(rows, "麦辣鸡腿汉堡、薯条、可乐")
        self.assertIn("得来速", md)
        self.assertIn("¥35.40", md)
        self.assertIn("定位失败", md)

    def test_cart_nutrition_over_budget(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        cart = [mo.CartLine("1440", "麦辣鸡腿汉堡", 1),
                mo.CartLine("4810", "中薯条", 2)]
        summary = nut.cart_nutrition(cart, items)
        md = rpt.render_cart_nutrition(summary, budget=800)
        self.assertIn("超出热量预算", md)
        self.assertIn("1063", md)  # 485 + 289*2

    def test_cart_nutrition_within_budget(self):
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        cart = [mo.CartLine("4810", "中薯条", 1)]
        summary = nut.cart_nutrition(cart, items)
        md = rpt.render_cart_nutrition(summary, budget=800)
        self.assertIn("热量预算 800 kcal 之内", md)

    def test_account_report(self):
        md = rpt.render_account_report(
            {"availablePoint": "1200", "accumulativePoint": "5000",
             "usedPoint": "3000", "expiredPoint": "800", "currency": "麦享会积分",
             "currentMouthExpirePoint": "200"},
            {"activityName": "麦麦积分抽奖", "activityStatusText": "进行中",
             "drawPoint": "24", "prizes": [{"name": "立减3元券"}]})
        self.assertIn("1200", md)
        self.assertIn("本月即将过期", md)
        self.assertIn("麦麦积分抽奖", md)

    def test_claim_report(self):
        claimable = wl.parse_available_coupons(AVAILABLE_RAW)
        md = rpt.render_claim_report(claimable, True, "已领取 1 张券")
        self.assertIn("未领取", md)
        self.assertIn("已领取 1 张券", md)

    def test_plan_report_with_nutrition(self):
        plan = {
            "baseline": {"original": 4700, "price": 4700},
            "best": {"original": 4700, "price": 3590,
                     "coupons": [{"title": "11.9元麦辣鸡腿堡", "validity": "x"}],
                     "productList": []},
            "addonSuggestions": [],
        }
        cart = [{"name": "麦辣鸡腿汉堡", "quantity": 1, "source": "menu",
                 "code": "1440", "currentPrice": "23"}]
        items = nut.parse_nutrition_table(NUTRITION_RAW)
        summary = nut.cart_nutrition([mo.CartLine("1440", "麦辣鸡腿汉堡", 1)], items)
        md = rpt.render_plan_report(plan, {"storeName": "测试餐厅", "address": "x"},
                                    cart, "到店自取",
                                    nutrition_summary=summary, kcal_budget=400)
        self.assertIn("热量速览", md)
        self.assertIn("485", md)
        self.assertIn("超出热量预算", md)

    def test_order_report(self):
        md = rpt.render_order_report({"orderId": "123", "status": "已支付",
                                      "items": [{"productName": "薯条",
                                                 "quantity": 1, "subtotal": 1450}]})
        self.assertIn("123", md)
        self.assertIn("薯条", md)
        self.assertIn("¥14.50", md)


class TestCliParser(unittest.TestCase):
    def test_global_flags_before_and_after_subcommand(self):
        p = mo.build_parser()
        a = p.parse_args(["--verbose", "plan", "--items", "薯条"])
        self.assertTrue(a.verbose)
        b = p.parse_args(["plan", "--items", "薯条", "--verbose"])
        self.assertTrue(b.verbose)
        c = p.parse_args(["plan", "--items", "薯条"])
        self.assertFalse(c.verbose)

    def test_all_commands_registered(self):
        p = mo.build_parser()
        # 触发 usage 打印即说明解析器构造成功
        for cmd in ["stores", "menu", "coupons", "claim", "plan", "compare",
                    "order", "orders", "nutrition", "account", "campaign",
                    "report", "tools", "cache"]:
            args = p.parse_args([cmd] + (["--items", "薯条"] if cmd in
                                         ("plan", "compare", "order") else []))
            self.assertTrue(hasattr(args, "func"))

    def test_as_list_helper(self):
        self.assertEqual(mo._as_list([1, 2]), [1, 2])
        self.assertEqual(mo._as_list({"addresses": [3]}, "addresses"), [3])
        self.assertEqual(mo._as_list({"stores": [4]}, "stores", "storeList"), [4])
        self.assertEqual(mo._as_list({}), [])
        self.assertEqual(mo._as_list(None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
