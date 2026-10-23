# -*- coding: utf-8 -*-
"""餐品名模糊匹配：把用户口语化的「麦辣鸡腿堡」「薯条x2」映射到菜单商品编码。

纯标准库实现：
- 名称归一化（去空格/标点、大小写）
- 匹配得分 = 精确 > 子串包含 > 序列相似度（difflib）
- 数量解析：`薯条x2` / `2个薯条` / `薯条*2` / `两份薯条`
"""

from __future__ import annotations

import difflib
import re

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_QTY_PATTERNS = [
    re.compile(r"[x\*×]\s*(\d+)\s*$", re.I),          # 薯条x2
    re.compile(r"(\d+)\s*$"),                          # 薯条2
    re.compile(r"^(\d+)\s*[个份份]"),                   # 2个薯条
    re.compile(r"^([一二两三四五六七八九十])\s*[个份]"),  # 两份薯条
]


def normalize(text: str) -> str:
    return re.sub(r"[\s，,。.、!！?？:：;；\"'“”‘’()（）\[\]【】]", "", text or "").lower()


def parse_quantity(query: str) -> tuple[str, int]:
    """从查询词中解析数量，返回 (去掉数量后的名称, 数量)。"""
    q = (query or "").strip()
    for pat in _QTY_PATTERNS:
        m = pat.search(q)
        if m:
            token = m.group(1)
            n = int(token) if token.isdigit() else _CN_NUM.get(token, 1)
            name = (q[:m.start()] + q[m.end():]).strip(" 的+")
            if name:
                return name, max(1, n)
    return q, 1


def _score(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.9 + 0.05 * (min(len(q), len(n)) / max(len(q), len(n)))
    return difflib.SequenceMatcher(None, q, n).ratio() * 0.8


def match_menu(query: str, meals: dict, threshold: float = 0.45) -> dict | None:
    """在菜单中找最匹配的餐品。

    meals: {code: {"name": ..., "currentPrice": ..., ...}}
    返回 {"code", "name", "currentPrice", "originalPrice", "score", "quantity"} 或 None。
    """
    name, quantity = parse_quantity(query)
    best, best_score = None, 0.0
    for code, meal in meals.items():
        score = _score(name, meal.get("name", ""))
        if score > best_score:
            best, best_score = meal, score
            best_code = code
    if best is None or best_score < threshold:
        return None
    return {
        "code": best_code,
        "name": best.get("name"),
        "currentPrice": best.get("currentPrice"),
        "originalPrice": best.get("originalPrice"),
        "score": round(best_score, 3),
        "quantity": quantity,
    }
