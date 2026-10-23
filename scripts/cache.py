# -*- coding: utf-8 -*-
"""本地 TTL 缓存：MCP 抖动时的降级数据源。

菜单、营养表、门店券这类「短时间不会变」的响应，缓存到
`~/.mcoperator/cache/`（可用 MCOPERATOR_HOME 覆盖）。
网络失败时先读缓存，保证 CLI 在 MCP 抽风时依然可用；
`--offline` 则完全离线，只读缓存。

缓存文件为 JSON：{"ts":  epoch, "data":  <工具返回的业务数据>}
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = os.path.join(
    os.environ.get("MCOPERATOR_HOME", str(Path.home() / ".mcoperator")),
    "cache",
)

DEFAULT_TTL = {
    "query-meals": 1800,          # 菜单 30 分钟
    "query-store-coupons": 600,   # 门店券 10 分钟
    "query-nearby-stores": 3600,  # 门店列表 1 小时
    "list-nutrition-foods": 86400,  # 营养表 1 天
    "query-my-coupons": 300,      # 卡包 5 分钟
    "query-my-account": 300,
    "available-coupons": 600,
    "campaign-calendar": 1800,
    "now-time-info": 60,
}


def _key(name: str, arguments: dict) -> str:
    raw = json.dumps({"name": name, "args": arguments or {}},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _path(name: str, arguments: dict) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return os.path.join(CACHE_DIR, "%s-%s.json" % (safe, _key(name, arguments)))


def get(name: str, arguments: dict, max_age: int | None = None):
    """读缓存；过期或不存在返回 None。"""
    if max_age is None:
        max_age = DEFAULT_TTL.get(name, 600)
    p = _path(name, arguments)
    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if time.time() - payload.get("ts", 0) > max_age:
            return None
        return payload.get("data")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def put(name: str, arguments: dict, data) -> None:
    """写缓存；失败静默（缓存是优化，不是必需品）。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_path(name, arguments), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data},
                      f, ensure_ascii=False)
    except OSError:
        pass


def clear() -> int:
    """清空缓存目录，返回删除的文件数。"""
    n = 0
    try:
        for fn in os.listdir(CACHE_DIR):
            fp = os.path.join(CACHE_DIR, fn)
            if os.path.isfile(fp):
                os.remove(fp)
                n += 1
    except OSError:
        pass
    return n
