# MCP_INTEGRATION.md — 麦算子如何调用麦当劳 MCP

本文档说明麦算子（McOperator）实际使用的麦当劳 MCP Server、工具、调用流程与业务价值。

## 1. MCP Server

| 项 | 值 |
|---|---|
| 服务名称 | 麦当劳 MCP Server（mcd-mcp） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（MCP 2024-11-05） |
| 认证方式 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 限流 | 每 Token 600 次/分钟 |
| 开放平台 | https://open.mcd.cn/mcp |
| 官方文档 | https://github.com/M-China/mcd-mcp-server |

麦算子不持有、不硬编码任何 Token：运行时从环境变量 `MCD_MCP_TOKEN` 读取，
配置文件仅使用环境变量占位符（见 `mcp-config.example.json`）。

## 2. 使用的工具清单

麦算子在 MCP 当前开放的 35 个工具中接入了 **14 个**，按命令分组：

### plan / order / compare（点餐精算主线）

| 顺序 | 工具 | 用途 | 必要性 |
|---:|---|---|---|
| 1 | `query-nearby-stores` | 按城市+关键词查附近门店，拿到 `storeCode` / `beCode` | 必需 |
| 2 | `delivery-query-addresses` / `delivery-query-stores` | 麦乐送场景：选配送地址与该地址可配送门店 | 外送场景必需 |
| 3 | `query-meals` | 拉取门店完整菜单（分类、餐品编码、现价/原价、优惠类型） | 必需 |
| 4 | `query-store-coupons` | 查询「该门店 + 该订单类型」下可用的券（券ID、券码、适用商品编码） | 必需 |
| 5 | `calculate-price` | **精算核心**：对「商品列表 + 每行挂券」计算原价、优惠、实付 | 必需，多次调用 |
| 6 | `create-order` | 按最优方案创建真实订单，返回支付链接（`order --confirm`） | 可选 |

### coupons / claim（券包管家）

| 工具 | 用途 |
|---|---|
| `query-my-coupons` | 盘点卡包券：标题、用券价、有效期、渠道标签 |
| `available-coupons` | 列出麦麦省当前可领的券及领取状态 |
| `auto-bind-coupons` | 一键领取全部可领麦麦省券 |

### 生活信息服务

| 命令 | 工具 | 用途 |
|---|---|---|
| `nutrition` / `plan --kcal` | `list-nutrition-foods` | 160 个餐品的能量/蛋白质/脂肪/碳水/钠/钙，支持购物车热量汇总与预算核对 |
| `account` | `query-my-account` + `query-lottery-info` | 麦享会积分余额、过期预警、积分抽奖活动与奖品池 |
| `orders` | `query-order` / `cancel-order` | 订单详情查询与取消（取消需二次确认） |
| `campaign` | `campaign-calendar` | 当月营销活动日历（进行中/往期/未来），支持指定日期锚点 |
| `now-time-info` | `now-time-info` | 服务器时间（营销日历锚点、缓存新鲜度参考） |

## 3. 调用流程（plan 命令）

```
用户输入（城市/关键词 + 想吃的餐品）
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ① query-nearby-stores(city, keyword, beType)                │
│    → 选定门店 storeCode / beCode                             │
│ ② query-meals(storeCode, orderType, beType[, beCode])       │
│    → 门店菜单 {code: {name, currentPrice, originalPrice}}    │
│ ③ query-store-coupons(storeCode, orderType, beType[, beCode])│
│    → 门店可用券 [{couponId, couponCode, products[]}]         │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ④ 本地：餐品名 → 菜单编码（精确 > 模糊），构建购物车          │
│    券商品识别：编码不在菜单中的 SKU 只能随券购买              │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ⑤ calculate-price 基准试算（不带券）                          │
│ ⑥ 枚举可挂券子集，逐一 calculate-price 试算（官方真值）        │
│ ⑦ 取实付最低组合；券商品单独试算生成「加购建议」              │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ⑧（可选）list-nutrition-foods → 购物车热量汇总，核对 --kcal 预算 │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
   省钱报告（Markdown）+ 本地省钱账本记账
```

`order` 命令在 ⑦ 的最优组合基础上调用 `create-order` 完成真实下单，
必须显式 `--confirm` 才会执行，防止误触。

`compare` 命令把 ①~⑦ 在到店自取 / 得来速 / 麦乐送三个渠道各跑一遍
（门店、菜单、券池各自独立解析），输出比价表——不同渠道的门店与券池
不同，实付可能差出好几块钱。

## 4. 关键技术细节

### 4.1 价格单位
`calculate-price` 返回的所有金额单位为**分**（`originalPrice` / `price` /
`discount` / `productList[].subtotal` 等），麦算子在展示层统一格式化为 ¥。

### 4.2 MCP 文本响应中的内嵌 JSON
麦当劳 MCP 工具返回的是「字段说明 Markdown + 业务 JSON」的混合文本，
且说明文本里含有 `{data[].title}` 这类模板占位符。
`mcd_client.extract_embedded_json()` 逐个候选花括号位置做括号配对解析，
优先返回带 `success`/`data` 键的业务对象（单测覆盖，见 `tests/`）。

### 4.3 券商品（coupon-only SKU）
部分商品编码（如 `9900016523` 龙焰鸡腿堡两件套）不在门店菜单中，
不带对应券调用 `calculate-price` 会返回 0 元——它们是只能随券购买的专属 SKU。
麦算子的处理：
- 用户点到券商品时，自动从门店券的适用商品中反查并带券；
- 报告中单列「加购建议」，注明随券实付价与参考原价；
- 「不使用优惠券」基准不含券商品，避免口径失真。

### 4.4 每行一券与拆行
`calculate-price` 的每个 item 只接受一张券（`couponId` + `couponCode`）。
同一商品用多张同款券时，麦算子自动拆成多行 `quantity=1` 分别挂券。

### 4.5 会话与容错
- `initialize` → `notifications/initialized` → `tools/call` 标准握手，
  全程携带 `mcp-session-id`；
- 空响应 / 瞬时错误自动重试（指数退避 + 抖动，上限 8s）；
- 调用间隔 ≥ 120ms，远离 600 次/分钟限流；
- HTTP 401（Token 失效）与业务错误（`success=false`）不重试，直接给出中文原因；
- HTTP 429 / 5xx 按退避重试；
- 菜单、门店券、营养表等读操作带本地 TTL 缓存：MCP 抖动时自动回退到
  上次成功的数据，`--offline` 可完全离线跑（缓存未命中会明确报错）；
- 外送地址/门店等接口的列表数据同时兼容 `[...]` 与 `{key: [...]}` 两种形态。

## 5. 业务价值

- **对用户**：把「券怎么叠最划算」从经验玄学变成官方真值精算，
  每一次 `plan` 都输出可验证的省钱金额；券商品加购建议避免
  「有券却点不了」的困惑。
- **对麦当劳**：提升优惠券核销率与点餐转化，降低用户比价成本。
- **对开发者**：展示了 MCP 工具链组合成一个完整决策 Skill 的工程范式
  （协议客户端 → 数据提取 → 模糊匹配 → 组合优化 → 报告渲染）。

## 6. 合规说明

- 本项目为非商业用途的个人参赛作品，不构成麦当劳官方产品；
- 不存储、不上传任何用户 Token 与个人数据，省钱账本仅存本地
  （`~/.mcoperator/ledger.json`）；
- 餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。
