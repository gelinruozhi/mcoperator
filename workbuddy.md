# workbuddy.md — 麦算子开发过程记录（WorkBuddy 对话上下文）

> 本文件记录麦算子（McOperator）使用腾讯 WorkBuddy 智能体平台的完整开发过程，
> 用于麦当劳程序员创意开发大赛 WorkBuddy 专项奖励核验。
> 开发时间：2026-10-23（北京时间），开发环境：WorkBuddy（Windows 10）。

---

## 第一阶段：需求分析与规则研究（约 15 分钟）

**输入**：大赛海报 + 两个官方仓库地址（mcd-developer-innovation-challenge、mcd-mcp-server）+ 一枚 MCP Token。

**WorkBuddy 执行**：
1. 下载并通读大赛规则仓库（README / activityGuidelines / CONTEST_DECLARATION / RANKING），
   提炼出硬性要求：仓库必备 5 个规定文件、报名 Issue 标准格式、排名依据为 GitHub Star、
   截止时间 2026-10-25 23:59；
2. 下载并通读 MCP Server 仓库，整理出 29 个文档在册工具的能力地图；
3. 拉取大赛官方 Issue 列表（当时已 1093 个）与 RANKING.md，分析竞争格局：
   「省钱精算」赛道已有多个高星项目（997★/876★），最终确定差异化定位——
   **全自动链路 + 券商品处理 + 官方真值试算 + 省钱战报**，命名「麦算子 McOperator」。

## 第二阶段：MCP 能力实测（约 30 分钟）

**WorkBuddy 执行**（通过临时脚本直接调用 https://mcp.mcd.cn）：
1. `initialize` 握手成功，实测开放工具 **35 个**（比文档多 6 个，含 query-survey-coupon / query-promotions 等）；
2. 探测核心工具返回结构：`query-nearby-stores`（章丘 3 家门店）、`query-meals`
   （95 个餐品含价格）、`query-store-coupons`（券ID/券码/适用商品编码）、
   `query-my-coupons`、`query-my-account`、`campaign-calendar`；
3. **关键发现**：带券 vs 不带券试算「龙焰鸡腿堡两件套」时，不带券返回全 0——
   证明 `9900016523` 这类编码是**券商品（coupon-only SKU）**，只能随券购买。
   这一发现直接决定了项目最核心的技术设计（券商品识别与单独口径）；
4. `auto-bind-coupons` 一键领取麦麦省 5 张券，使演示具备多券场景。

## 第三阶段：开发（约 90 分钟）

**WorkBuddy 执行**：在对话中完成全部代码与文档，模块及决策如下：

| 模块 | 实现要点 |
|---|---|
| `mcd_client.py` | Streamable HTTP 客户端：initialize/notifications/tools-call 握手、mcp-session-id 保持、SSE 与 JSON 双格式解析、瞬时失败重试、401/429 中文提示 |
| `matcher.py` | 餐品名匹配：归一化 + 精确/子串/序列相似度三级评分；数量解析（`薯条x2`、`2个薯条`、`两份薯条`） |
| `optimizer.py` | 券组合优化：可挂券/非可挂券分组、子集枚举（≤10 全枚举，否则贪心+交换）、原子行拆行挂券、券商品加购建议 |
| `report.py` | Markdown 省钱报告 + 本地账本 + 七段位体系 |
| `mcoperator.py` | CLI：stores/menu/coupons/plan/order/report/tools 七个子命令，支持到店/得来速/麦乐送三渠道 |

**调试过程实录**（均由 WorkBuddy 在真实调用中发现并修复）：
1. `notifications/initialized` 返回 202 空体导致 JSON 解析崩溃 → 增加空响应处理；
2. `query-store-coupons` 响应无 `Original Response` 标记，且字段说明含
   `{data[].title}` 模板占位符，导致内嵌 JSON 提取失败（券数显示 0）→
   重写提取器为「逐候选位置括号配对 + 优先取含 success/data 的业务对象」；
3. 「龙焰鸡腿堡两件套」被模糊匹配到菜单单品「龙焰鸡腿堡」→
   确立匹配优先级：菜单精确 > 券商品精确 > 菜单模糊 > 券商品模糊；
4. 纯券商品购物车的基准价口径失真（节省额算出负数）→
   改为「最优方案原价口径」并在报告中以脚注说明。

## 第四阶段：验证与交付（约 30 分钟）

**WorkBuddy 执行**：
1. 两个真实场景端到端验证通过：
   - 麦辣鸡腿汉堡+薯条+可乐：¥47.00 → **¥35.90**（省 ¥11.10，23.6%）
   - 龙焰鸡腿堡两件套+薯条+可乐：¥58.50 → **¥44.48**（省 ¥14.02，24.0%）
2. 编写 17 个离线单元测试（录制真实响应快照，不访问网络），全部通过；
3. 生成参赛必备文件：README.md、MCP_INTEGRATION.md、mcp-config.example.json
   （仅环境变量占位符）、CONTEST_DECLARATION.md（官方原文，MD5 校验一致）、SKILL.md；
4. 通过 `gh` CLI 以 gelinruozhi 账号创建公开仓库并推送；
5. 在大赛官方仓库提交标准格式报名 Issue。

## 使用的 WorkBuddy 能力

- 多文件并行读写、Bash/PowerShell 命令执行（7z 解压、Python 脚本、gh CLI）
- WebFetch/WebSearch 备用信息通道
- 任务管理（TaskCreate/TaskUpdate）跟踪五阶段进度
- 本地文件系统操作与 Git/GitHub 全流程

---

*本记录由参赛者使用 WorkBuddy 开发完成后整理生成，内容与实际开发过程一致。*
