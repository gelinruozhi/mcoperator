# 麦算子 McOperator

> **点餐先问麦算子：同样的麦，算得更省。**
>
> 一个基于麦当劳中国 MCP 的点餐精算 Skill——自动找门店、对菜单、凑优惠，
> 用官方 `calculate-price` 真值逐一试算优惠券组合，给出实付最低的下单方案。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-McDonald's%20China-red.svg)](https://open.mcd.cn/mcp)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#)
[![Tests](https://img.shields.io/badge/tests-17%20passed-brightgreen.svg)](#测试)

**1024 程序员节这天，麦当劳发行了一张程序员专属券。** 我们写了麦算子，让每一张券都不被浪费。

---

## 为什么是麦算子

点麦当劳的人分两种：一种打开 App 直接下单，一种先翻遍优惠券。

第二种人往往以为自己在省钱，其实只是累。真正的问题是：**优惠券的叠加规则是个组合优化问题**——
哪张券挂哪个商品、券商品要不要加购、到店还是外送，穷举起来比点餐本身复杂得多。

麦算子把这件事交给程序：

1. **全自动链路**：城市+关键词 → 门店 → 菜单 → 门店可用券 → 精算 → 报告，一条命令跑完；
2. **官方真值，不做估算**：每一种券组合都真实调用麦当劳 `calculate-price`，实付多少是算出来的，不是猜的；
3. **懂「券商品」**：像 `1024"码"力翻倍` 这种券，兑换的「龙焰鸡腿堡两件套」根本不在常规菜单里——不带券，接口连价都报不出来。麦算子能识别这类专属 SKU，自动带券，并告诉你加购是否划算；
4. **省钱战报**：每次精算省下的钱记入本地账本，累计解锁「省钱学徒 → 券界传说」七段位。

## 真实演示（2026-10-23，济南 · 麦当劳章丘汇泉路餐厅）

**场景一：常规点餐，把券用对**

```console
$ python scripts/mcoperator.py plan --city 济南市 --keyword 章丘 --items "麦辣鸡腿汉堡,薯条,可乐"

· 门店：麦当劳章丘汇泉路餐厅（到店自取）
· 购物车：麦辣鸡腿汉堡×1、薯条×1、可乐×1
· 门店可用券：3 张（19.9元王牌炸鸡任选2、11.9元麦辣鸡腿堡、1024"码"力翻倍）

# 麦算子 · 点餐精算报告

| 方案 | 原价 | 实付 | 节省 |
|---|---:|---:|---:|
| 不使用优惠券 | ¥47.00 | ¥47.00 | — |
| **最优券组合** | ¥47.00 | **¥35.90** | **¥11.10（23.6%）** |
```

最优方案用了 `11.9元麦辣鸡腿堡`——麦辣鸡腿汉堡 ¥23 → ¥11.9。

**场景二：券商品，用券比单点还便宜**

```console
$ python scripts/mcoperator.py plan --city 济南市 --keyword 章丘 --items "龙焰鸡腿堡两件套,薯条,可乐"

· 「龙焰鸡腿堡两件套」是券商品，只能使用「1024"码"力翻倍」购买，已自动带券。

| 方案 | 原价 | 实付 | 节省 |
|---|---:|---:|---:|
| 不使用优惠券 | — | 无法单独定价* | — |
| **最优券组合** | ¥58.50 | **¥44.48** | **¥14.02（24.0%）** |
```

> \* 购物车含「券商品」——只能随对应优惠券购买的专属 SKU，不领券时麦当劳接口无法单独定价。

完整输出见 [`examples/`](examples/)。所有数字来自真实 MCP 调用，你可以自己复现。

## 快速开始

### 1. 准备 MCP Token

前往[麦当劳 MCP 开放平台](https://open.mcd.cn/mcp)，手机号登录后在控制台激活 Token，然后：

```bash
export MCD_MCP_TOKEN="你的Token"
```

### 2. 跑起来（零依赖，Python 3.8+）

```bash
git clone https://github.com/gelinruozhi/mcoperator.git
cd mcoperator

# 看看附近有什么店
python scripts/mcoperator.py stores --city 济南市 --keyword 章丘

# 精算一单（核心命令）
python scripts/mcoperator.py plan --city 济南市 --keyword 章丘 --items "麦辣鸡腿汉堡,薯条,可乐"
```

### 3. 常用命令

| 命令 | 作用 |
|---|---|
| `stores --city X --keyword Y` | 查附近门店（自动选最近） |
| `menu --store <code>` | 看门店完整菜单与价格 |
| `coupons [--claim]` | 盘点券包；`--claim` 一键领取麦麦省券 |
| `plan --items "A,B"` | **精算最优券组合，输出省钱报告** |
| `order --items "A,B" --confirm` | 按最优方案真实下单（不加 `--confirm` 只预览） |
| `report` | 查看省钱战报与当前段位 |
| `tools` | 列出麦当劳 MCP 当前开放的全部工具 |

外送场景加 `--delivery`（自动取第一个配送地址，或用 `--address-id` 指定）；
得来速加 `--be-type 5`。

### 4. 作为 WorkBuddy Skill 使用

本仓库同时是一个 WorkBuddy Skill。复制到用户技能目录即可在对话中直接使用：

```bash
cp -r mcoperator ~/.workbuddy/skills/
```

然后在 WorkBuddy 里说：「帮我看看章丘汇泉路麦当劳，麦辣鸡腿堡套餐怎么点最便宜」——
Agent 会按 `SKILL.md` 的规程调用本 Skill 完成精算。

## 它是怎么精算的

```
用户：想吃什么
   │
   ▼
query-nearby-stores ──► 门店 storeCode / beCode
query-meals          ──► 门店菜单 {编码: 名称/现价/原价}
query-store-coupons  ──► 门店可用券 {券ID/券码/适用商品}
   │
   ▼
本地：餐品名匹配菜单（精确>模糊），识别券商品，构建购物车
   │
   ▼
calculate-price 基准试算（不带券）
   │
   ▼
枚举可挂券子集 ──► 每个子集真实调用 calculate-price ──► 取实付最低
   │
   ▼
省钱报告 + 本地账本记账
```

几个值得展开的实现细节：

- **每行一券**：`calculate-price` 的每个商品行只接受一张券，所以同一商品用两张同款券时，
  购物车会自动拆成多行 `quantity=1` 分别挂券；
- **券商品单列**：专属 SKU 只会推高总价，不作为优化变量，而是单独试算「随券加购价」，
  以加购建议的形式呈现——加不加，你说了算；
- **真值试算**：不本地估算任何折扣规则，优惠叠加的全部可能性都以官方接口返回为准；
- **MCP 文本解析**：工具返回是「字段说明 Markdown + 业务 JSON」的混合文本，说明里还混着
  `{data[].title}` 模板占位符——提取器逐个候选位置做括号配对解析，优先取带 `success`/`data`
  的业务对象（有单测覆盖）。

## 项目结构

```
mcoperator/
├── SKILL.md                  # WorkBuddy Skill 定义（触发词、执行准则）
├── scripts/
│   ├── mcoperator.py         # CLI 入口（stores/menu/coupons/plan/order/report/tools）
│   ├── mcd_client.py         # MCP Streamable HTTP 客户端（握手/会话/重试/JSON 提取）
│   ├── matcher.py            # 餐品名模糊匹配（含数量解析：薯条x2/两份薯条）
│   ├── optimizer.py          # 券组合优化器（子集枚举/贪心/拆行挂券）
│   └── report.py             # 省钱报告渲染 + 本地账本与段位
├── tests/
│   └── test_mcoperator.py    # 17 个离线单测（真实响应快照，不访问网络）
├── examples/                 # 真实演示输出
├── MCP_INTEGRATION.md        # MCP 工具/流程/业务价值说明
├── mcp-config.example.json   # 脱敏 MCP 配置（仅环境变量占位符）
├── CONTEST_DECLARATION.md    # 参赛声明（官方模板原文）
└── workbuddy.md              # 使用 WorkBuddy 的开发过程记录
```

## 测试

```bash
python tests/test_mcoperator.py
# Ran 17 tests in 0.002s — OK
```

测试全部离线运行，使用录制的真实 MCP 响应快照，覆盖 JSON 提取、
餐品匹配、券分组、拆行挂券、券商品口径与报告渲染。

## 安全与边界

- **Token 只从环境变量读取**，不写入任何文件、不出现在日志与输出中；
- 省钱账本仅存本地 `~/.mcoperator/ledger.json`，可随时删除；
- `order` 命令默认只预览，必须显式 `--confirm` 才创建真实订单；
- 本项目为个人参赛作品，非麦当劳官方产品；餐品、价格与供应状态以麦当劳官方渠道实时结果为准。

## 致谢

- [麦当劳中国 MCP](https://open.mcd.cn/mcp) 与 [mcd-mcp-server](https://github.com/M-China/mcd-mcp-server) 提供的能力
- [麦当劳程序员创意开发大赛](https://github.com/M-China/mcd-developer-innovation-challenge) 与合作伙伴腾讯 WorkBuddy

## License

[MIT](LICENSE) © 2026 gelinruozhi
