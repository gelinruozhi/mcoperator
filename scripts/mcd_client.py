# -*- coding: utf-8 -*-
"""麦当劳 MCP Server 客户端（Streamable HTTP，零第三方依赖）。

封装 initialize / notifications/initialized / tools/call 握手与调用，
负责：
- mcp-session-id 会话保持
- SSE（text/event-stream）与普通 JSON 两种响应体解析
- 从 MCP 工具返回的 Markdown 文本中提取内嵌的业务 JSON
- 瞬时失败重试与限流退避

麦当劳 MCP 服务规则见 https://cdn.mcd.cn/cms/pages/MCPServerRules.html
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2024-11-05"
MIN_CALL_INTERVAL = 0.12  # 秒；官方限流 600 次/分钟，保守留出余量


class McdMcpError(RuntimeError):
    """MCP 调用失败（网络、协议、业务错误）。"""


def _try_parse_balanced(text: str, start: int):
    """从 start 处的 '{' 开始做括号配对，尝试解析为 JSON，失败返回 None。"""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def extract_embedded_json(text: str):
    """从 MCP 工具返回的文本中提取内嵌的业务 JSON。

    工具返回通常是「字段说明 + {json}」或「字段说明 + Original Response + {json}」
    的 Markdown，字段说明里常含 `{data[].title}` 之类的模板占位符，
    因此逐个候选位置尝试解析，优先返回带 success/data 键的业务对象。
    """
    if not text:
        return None
    positions = []
    marker = text.find("Original Response")
    if marker >= 0:
        positions.append(text.find("{", marker))
    positions.extend(i for i, ch in enumerate(text) if ch == "{")

    fallback = None
    seen = set()
    for start in positions:
        if start < 0 or start in seen:
            continue
        seen.add(start)
        obj = _try_parse_balanced(text, start)
        if not isinstance(obj, dict):
            continue
        if "success" in obj or "data" in obj:
            return obj
        if fallback is None:
            fallback = obj
    return fallback


class McdMcpClient:
    """极简 MCP Streamable HTTP 客户端。"""

    def __init__(self, token: str | None = None, url: str = DEFAULT_URL,
                 timeout: int = 30, verbose: bool = False):
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise McdMcpError(
                "未提供 MCP Token：请设置环境变量 MCD_MCP_TOKEN，"
                "或通过 --token 传入（参考 mcp-config.example.json）。"
            )
        self.url = url
        self.timeout = timeout
        self.verbose = verbose
        self._session_id = None
        self._req_id = 0
        self._initialized = False
        self._last_call_ts = 0.0
        self.call_count = 0

    # ------------------------------------------------------------------ #
    # 底层协议
    # ------------------------------------------------------------------ #
    def _post(self, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.token,
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self._session_id = sid
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 401:
                raise McdMcpError("MCP Token 无效或已过期（HTTP 401），请重新申请 Token。")
            if e.code == 429:
                raise McdMcpError("触发 MCP 限流（HTTP 429，600 次/分钟），请稍后重试。")
            raise McdMcpError("MCP 请求失败 HTTP %s：%s" % (e.code, detail))
        except urllib.error.URLError as e:
            raise McdMcpError("MCP 网络错误：%s" % e.reason)

        # 流式响应：取 data: 行；普通响应直接是 JSON；通知类返回 202 空体
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise McdMcpError("MCP 返回了无法解析的内容：%s" % raw[:200])

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._req_id += 1
        payload = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            payload["params"] = params
        resp = self._post(payload)
        if "error" in resp:
            err = resp["error"]
            raise McdMcpError("MCP RPC 错误 %s：%s" % (err.get("code"), err.get("message")))
        return resp.get("result", {})

    def initialize(self) -> "McdMcpClient":
        if self._initialized:
            return self
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcoperator", "version": "1.0.0"},
        })
        server = result.get("serverInfo", {})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True
        if self.verbose:
            print("[mcp] 已连接 %s v%s" % (server.get("name"), server.get("version")))
        return self

    # ------------------------------------------------------------------ #
    # 工具调用
    # ------------------------------------------------------------------ #
    def _throttle(self):
        wait = MIN_CALL_INTERVAL - (time.time() - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.time()

    def call_tool(self, name: str, arguments: dict | None = None,
                  retries: int = 2) -> dict:
        """调用工具并返回解析后的业务 JSON（data 层由调用方按需取）。

        失败重试针对「空响应 / 无内嵌 JSON」这类瞬时情况。
        """
        self.initialize()
        last_err = None
        for attempt in range(retries + 1):
            self._throttle()
            self.call_count += 1
            try:
                result = self._rpc("tools/call", {
                    "name": name, "arguments": arguments or {},
                })
            except McdMcpError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            content = result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            text = "\n".join(texts)
            data = extract_embedded_json(text)
            if data is not None:
                return data
            # 极少数工具直接返回纯文本（无内嵌 JSON），原样带回
            if text.strip():
                return {"_raw_text": text}
            last_err = McdMcpError("工具 %s 返回为空（第 %d 次尝试）" % (name, attempt + 1))
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_err

    def list_tools(self) -> list:
        self.initialize()
        result = self._rpc("tools/list", {})
        return result.get("tools", [])
