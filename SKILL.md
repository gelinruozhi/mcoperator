---
name: mcoperator
description: 麦当劳点餐精算 Skill（麦算子）。当用户想点麦当劳、问"怎么点最便宜/最划算"、"有什么券能用"、"这个套餐多少钱"、"帮我领券"、"附近麦当劳"、"哪个渠道便宜"、"这个多少大卡"、"我的积分"时使用。基于麦当劳中国 MCP：自动查门店、对菜单、收集可用优惠券、用官方 calculate-price 精算最优券组合，输出省钱报告；支持多渠道比价、热量预算、临期券提醒、积分账户、订单管理与营销日历。也可用于真实下单（需用户明确确认）和查看省钱战报。
---

# 麦算子 McOperator — 麦当劳点餐精算 Skill

点餐先问麦算子：同样的麦，算得更省。

## 能力概览

基于麦当劳中国 MCP（https://mcp.mcd.cn）的完整点餐决策链：

1. **找门店**：按城市/关键词查附近麦当劳（支持到店自取、得来速、麦乐送）
2. **对菜单**：把口语化餐品名（"麦辣鸡腿堡"、"薯条x2"）映射到门店菜单编码
3. **凑优惠**：收集门店可用券，识别「券商品」（只能随券购买的专属 SKU）
4. **精算**：用官方 `calculate-price` 逐一试算券组合，取实付最低
5. **报告**：输出省钱报告（原价/实付/节省/用券明细/加购建议/热量速览）
6. **比价**：同一购物车在自取/得来速/麦乐送三渠道分别精算比价
7. **下单**：用户明确确认后按最优方案创建真实订单
8. **战报**：本地记录省钱战绩，累计解锁麦门段位

生活信息能力：卡包临期提醒、一键领券、营养成分与热量预算、
麦享会积分、订单查询/取消、营销活动日历。

## 前置条件

- 已申请麦当劳 MCP Token（open.mcd.cn/mcp 控制台激活）
- 环境变量 `MCD_MCP_TOKEN` 已设置，或调用时传 `--token`
- Python 3.8+，零第三方依赖

## 使用方式

Skill 脚本位于 `scripts/mcoperator.py`。常见任务对应命令：

| 用户意图 | 命令 |
|---|---|
| 附近有哪些麦当劳 | `python scripts/mcoperator.py stores --city 城市 --keyword 位置` |
| 看看菜单 | `python scripts/mcoperator.py menu --city 城市 --keyword 位置 [--filter 关键词]` |
| 盘点/领取优惠券 | `python scripts/mcoperator.py coupons [--claim]` 或 `claim --claim` |
| **怎么点最便宜** | `python scripts/mcoperator.py plan --city 城市 --keyword 位置 --items "麦辣鸡腿汉堡,薯条,可乐"` |
| 热量/减脂期怎么点 | `plan ... --kcal 800` |
| 自取/外卖/得来速哪个便宜 | `python scripts/mcoperator.py compare --city 城市 --keyword 位置 --items "..."` |
| 就按最优方案下单 | `python scripts/mcoperator.py order ... --confirm` |
| 查/取消订单 | `python scripts/mcoperator.py orders --order-id <订单号> [--cancel --confirm]` |
| 这个多少大卡 | `python scripts/mcoperator.py nutrition --query 巨无霸` |
| 我的积分 | `python scripts/mcoperator.py account` |
| 最近有什么活动 | `python scripts/mcoperator.py campaign` |
| 我的省钱战报 | `python scripts/mcoperator.py report` |
| MCP 都能干嘛 | `python scripts/mcoperator.py tools` |

`plan` / `compare` 输出 Markdown 报告，可直接展示给用户；任何命令加 `--json`
输出原始结构；加 `--offline` 只用本地缓存（适合 MCP 抖动时演示）。

## 执行准则（Agent 必读）

1. **先问清门店**：用户没说位置时，先问城市+大致位置（或关键词），不要猜。
2. **餐品名模糊没关系**：脚本内置匹配（精确 > 模糊），匹配不到会提示，把提示转告用户。
3. **下单必须显式确认**：`order` 不加 `--confirm` 只预览；得到用户明确同意后才加
   `--confirm`。下单产生真实支付，严禁代替用户确认。取消订单同理。
4. **价格口径**：报告中的「参考原价」以官方接口为准；券商品不领券无法单独定价，
   已在报告中注明，不要按「不使用优惠券」口径向用户解释节省额。
5. **热量数据是参考**：营养表未收录的商品会标注「未查到」，不要编造热量。
6. **Token 安全**：只从环境变量读取，绝不写入任何文件或日志，绝不在回复中回显。
7. **限流**：脚本已内置调用间隔；批量使用时不要并发多个 plan / compare。

## 故障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 401 | Token 无效/过期 | 去 open.mcd.cn/mcp 重新激活 |
| 429 | 超 600 次/分钟 | 等一分钟再试 |
| 门店列表为空 | 关键词太偏或城市写法不同 | 换更常见的道路/商圈名 |
| 餐品未识别 | 门店当日未售或名称差异 | 用 `menu --filter` 看实际菜单名 |
| MCP 超时/5xx | 网络或服务端抖动 | 自动重试后回退缓存；`--offline` 可直接用缓存 |
| 热量缺失 | 官方营养表未收录该餐品 | 报告中会标注，不影响价格精算 |
