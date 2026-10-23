# -*- coding: utf-8 -*-
r"""麦享会钱包 / 可领券 / 营销活动日历的 Markdown 解析。

这些 MCP 工具返回的是给人看的 Markdown（不是 JSON），
但结构稳定，可以解析成结构化数据：

- query-my-coupons：    `## 券标题` + `- **优惠/有效期/领取时间/标签**: ...`
- available-coupons：   `- 优惠券标题：X \ 状态：Y \ 优惠券图片：...`
- campaign-calendar：   `#### 日期 状态` + `- **活动标题**：... **活动内容介绍**：...`
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_VALIDITY_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*[-~]\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")
_IMG_RE = re.compile(r"<img[^>]*>", re.S)
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _strip_md(text: str) -> str:
    """去掉图片标签与 Markdown 链接语法，压平空白。"""
    text = _IMG_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_date(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- #
# 我的卡包（query-my-coupons）
# --------------------------------------------------------------------- #
def parse_my_coupons(text: str) -> list:
    """解析卡包券 Markdown → [dict]。

    每条：title / price / validityText / validFrom / validTo /
          receivedAt / tags / expiresInDays（距到期天数，None 表示无法解析）
    """
    coupons = []
    if not text:
        return coupons
    blocks = re.split(r"^##\s+", text, flags=re.M)[1:]
    for block in blocks:
        lines = block.splitlines()
        title = _strip_md(lines[0]) if lines else ""
        if not title:
            continue
        entry = {"title": title, "price": "", "validityText": "",
                 "validFrom": "", "validTo": "", "receivedAt": "", "tags": [],
                 "expiresInDays": None}
        for ln in lines[1:]:
            m = re.match(r"^-\s+\*\*([^*]+)\*\*\s*[:：]\s*(.*)$", ln.strip())
            if not m:
                continue
            key, val = m.group(1).strip(), _strip_md(m.group(2))
            if key == "优惠":
                entry["price"] = val
            elif key == "有效期":
                entry["validityText"] = val
                vm = _VALIDITY_RE.search(val)
                if vm:
                    entry["validFrom"] = "%s %s" % (vm.group(1), vm.group(2))
                    entry["validTo"] = "%s %s" % (vm.group(3), vm.group(4))
            elif key == "领取时间":
                entry["receivedAt"] = val
            elif key == "标签":
                entry["tags"] = [t.strip() for t in
                                 re.split(r"[、,，]\s*", val) if t.strip()]
        if entry["validTo"]:
            d = _to_date(entry["validTo"][:10])
            if d is not None:
                entry["expiresInDays"] = (d.date() - datetime.now().date()).days
        coupons.append(entry)
    return coupons


def expiry_alert(coupon: dict, within_days: int = 3) -> str:
    """返回临期提醒文案；不紧急返回空串。"""
    days = coupon.get("expiresInDays")
    if days is None:
        return ""
    if days < 0:
        return "已过期"
    if days == 0:
        return "今日到期"
    if days <= within_days:
        return "%d 天后到期" % days
    return ""


# --------------------------------------------------------------------- #
# 麦麦省可领券（available-coupons）
# --------------------------------------------------------------------- #
def parse_available_coupons(text: str) -> list:
    """解析 available-coupons Markdown → [{title, status}]。"""
    out = []
    if not text:
        return out
    # 形如：- 优惠券标题：X \n  状态：Y \n  优惠券图片：<img ...>
    for m in re.finditer(r"-\s*优惠券标题[:：]\s*(.*?)\s*\\?\s*\n\s*状态[:：]\s*([^\n\\]*)",
                         text):
        title = _strip_md(m.group(1))
        status = _strip_md(m.group(2))
        if title:
            out.append({"title": title, "status": status})
    return out


# --------------------------------------------------------------------- #
# 营销活动日历（campaign-calendar）
# --------------------------------------------------------------------- #
def parse_campaign_calendar(text: str) -> list:
    """解析活动日历 Markdown → [{date, status, activities:[{title, content}]}]。

    结构：`#### 2026年10月20日 往期回顾` 之后跟若干活动条目；
    每个条目以 `-` 开头，字段可能写在缩进续行上：

        -   **活动标题**：蓝莓爆爆珠麦旋风上新
            **活动内容介绍**：一口爆汁
    """
    days = []
    if not text:
        return days
    sections = re.split(r"^####\s+", text, flags=re.M)[1:]
    for sec in sections:
        lines = sec.splitlines()
        head = _strip_md(lines[0]) if lines else ""
        dm = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(.*)", head)
        if dm:
            date = "%04d-%02d-%02d" % (int(dm.group(1)), int(dm.group(2)),
                                       int(dm.group(3)))
            status = dm.group(4).strip()
        else:
            date, status = "", head
        # 先把「- 」条目及其缩进续行拼成一条
        entries, cur = [], None
        for ln in lines[1:]:
            if ln.strip().startswith("-"):
                if cur:
                    entries.append(cur)
                cur = ln.strip().lstrip("-").strip()
            elif cur is not None and ln.strip():
                cur += " " + ln.strip()
        if cur:
            entries.append(cur)

        activities = []
        for body in entries:
            body = _strip_md(body)
            tm = re.search(
                r"活动标题\*\*\s*[:：]\s*(.*?)(?:\s*\*\*活动内容介绍\*\*\s*[:：]\s*(.*))?$",
                body)
            if tm:
                activities.append({
                    "title": (tm.group(1) or "").strip(" *"),
                    "content": (tm.group(2) or "").strip(" *"),
                })
        days.append({"date": date, "status": status,
                     "activities": activities})
    return days


def format_validity_left(coupon: dict) -> str:
    """「还剩几天」的人话。"""
    days = coupon.get("expiresInDays")
    if days is None:
        return coupon.get("validityText", "")[:24]
    if days < 0:
        return "已过期"
    if days == 0:
        return "今天最后一天"
    return "还剩 %d 天" % days
