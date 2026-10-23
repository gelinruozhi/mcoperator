# -*- coding: utf-8 -*-
"""营养成分解析与购物车热量统计。

麦当劳 MCP 的 list-nutrition-foods 返回形如下面的内嵌 CSV 文本：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,
    fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      ...

本模块把它解析成结构化列表，支持按名称模糊匹配，
并可对购物车（餐品名 + 数量）汇总热量与三大营养素。
"""

from __future__ import annotations

import re

from matcher import normalize, parse_quantity, _score

# 表头行：[160]{productName,nutritionDescription,...}:
_HEADER_RE = re.compile(r"^\[\d+\]\{[^}]*\}\s*:\s*$")

# 数据行：名称,描述,kj,kcal,蛋白质,脂肪,碳水,钠,钙
_FIELD_COUNT = 9


class NutritionItem:
    """一份餐品的营养数据（能量单位 kcal，其余为克/毫克）。"""

    __slots__ = ("name", "description", "energy_kj", "energy_kcal",
                 "protein", "fat", "carbohydrate", "sodium", "calcium")

    def __init__(self, name, description="", energy_kj=0, energy_kcal=0,
                 protein=0, fat=0, carbohydrate=0, sodium=0, calcium=0):
        self.name = name
        self.description = description or ""
        self.energy_kj = _num(energy_kj)
        self.energy_kcal = _num(energy_kcal)
        self.protein = _num(protein)
        self.fat = _num(fat)
        self.carbohydrate = _num(carbohydrate)
        self.sodium = _num(sodium)
        self.calcium = _num(calcium)

    def to_dict(self) -> dict:
        return {"name": self.name, "energyKcal": self.energy_kcal,
                "protein": self.protein, "fat": self.fat,
                "carbohydrate": self.carbohydrate, "sodium": self.sodium,
                "calcium": self.calcium}

    def __repr__(self):
        return "NutritionItem(%s, %dkcal)" % (self.name, self.energy_kcal)


def _num(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return 0


def parse_nutrition_table(text: str) -> list:
    """解析 list-nutrition-foods 的 data 字符串为 [NutritionItem]。

    容错：跳过表头、空行、字段数不符的行与 null 描述。
    """
    items = []
    if not text:
        return items
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _HEADER_RE.match(line):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < _FIELD_COUNT:
            continue
        name = parts[0]
        if not name:
            continue
        desc = parts[1] if parts[1] != "null" else ""
        items.append(NutritionItem(name, desc, *parts[2:_FIELD_COUNT]))
    return items


def match_nutrition(query: str, items: list, threshold: float = 0.45):
    """按名称匹配营养数据；支持「薯条x2」这类带数量的查询。

    返回 (NutritionItem, quantity) 或 None。优先精确匹配，
    其次按 matcher._score 取最高分（需过阈值）。
    """
    if not items:
        return None
    _, qty = parse_quantity(query)
    nq = normalize(query)
    # 1. 精确
    for it in items:
        if normalize(it.name) == nq:
            return it, qty
    # 2. 模糊：取最高分；平分取营养表中靠前的一条
    # （官方表按「中/大/小」排列，中号在前，正好对应经典默认规格）
    best, best_score = None, 0.0
    for it in items:
        s = _score(query, it.name)
        if s > best_score:
            best, best_score = it, s
    if best is not None and best_score >= threshold:
        return best, qty
    return None


def cart_nutrition(cart: list, items: list, threshold: float = 0.45) -> dict:
    """汇总购物车热量。

    cart: CartLine 列表（需有 .name/.quantity）。
    items: [NutritionItem]。
    返回 {"matched": [(line, item)], "missing": [line],
          "kcal": 总热量, "protein": ..., ...}。
    """
    matched, missing = [], []
    total = {"kcal": 0, "protein": 0.0, "fat": 0.0, "carbohydrate": 0.0,
             "sodium": 0}
    for line in cart:
        hit = match_nutrition(line.name, items, threshold)
        if hit is None:
            missing.append(line)
            continue
        item, _ = hit
        qty = max(1, line.quantity)
        matched.append((line, item))
        total["kcal"] += item.energy_kcal * qty
        total["protein"] += item.protein * qty
        total["fat"] += item.fat * qty
        total["carbohydrate"] += item.carbohydrate * qty
        total["sodium"] += item.sodium * qty
    return {"matched": matched, "missing": missing, "total": total}
