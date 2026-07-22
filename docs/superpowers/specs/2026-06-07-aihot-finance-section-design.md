# AIHOT 金融板块 · 设计文档

- 日期：2026-06-07
- 状态：已通过 brainstorming 评审，待写实施计划
- 适用站点：AIHOT（https://aihot.virxact.com）

## 0. 背景与目标

AIHOT 目前是 AI 热点监控站，已开放 Skill / RSS / API 三种 Agent 接入。本设计在其之上新增一个**金融板块**，作为 AI 板块的**同构平行板块**：复用同一套「抓取 → 去重 → 打分 → 分类 → 精选 → 开放」引擎，数据进**同一个条目库**，金融只是一个 `channel`（频道）。

目标：用户在主站点「金融」入口、或通过 Skill / RSS / API，免费获取经过抓取、去重、打分、分类的财经/金融信息，能力与 AI 板块对齐（日报 + 精选 + 全量 + 搜索），并额外提供金融特有的「按标的查 / 按市场查 / 突发快讯流」。

非目标（本期不做）：完整的辟谣/证实关联链路（只做轻量 `unverified` 标记）；投资建议或量化信号；单做某个细分赛道（本期覆盖全市场）。

## 1. 范围决策（已与用户确认）

| 决策点 | 选择 |
|---|---|
| 与 AI 板块的关系 | **完整平行复制**：日报 + 精选 + 全量 + 搜索，并开放 Skill / RSS / API，同一套架构 |
| 顶层分类 | **6 类，按资产主题** |
| AI×金融重叠 | **一库多标签**：单一条目库，多频道、多标签，不重复存储、不重复抓取 |
| 信源接入 | **中文快讯主干 + 精选 X**，地缘 OSINT 仅进风险预警且标「未证实」 |
| 金融特有能力 | **按标的/代码查、按市场/资产查、突发快讯流 + 热度**（未选「完整辟谣链路」） |

## 2. 分类体系（6 大版块）

日报的顶层版块，也是全量 / 精选的主分类：

1. **宏观·政策** —— 美国就业（ADP / 初请 / 裁员）、美元指数、美联储、各国央行（含央行黄金储备）、货币政策、关税
2. **大宗·能源** —— 原油（OPEC+ / 油价 / 产量配额）、黄金（金价 / 黄金 ETF）、锂 / 储能、天然气、电力
3. **股指·汇率** —— 全球股指（KOSPI、日经 225、A股、港股…）、汇率（USD/JPY 等）、债市
4. **行业·公司** —— 半导体（AMD / 英伟达 / 台积电 / 三星）、AI 基建（算力 / 光纤 / PCB / 数据中心 / 电力）、新能源车 / 智能驾驶 / 机器人、银行、个股财报 / 融资
5. **地缘·风险** —— 中东冲突、霍尔木兹海峡、航运、军工、影响行情的地缘事件
6. **A股·公告** —— A股公告速递、涨停 / 异动、概念板块联动、IPO

细分 `tags` 沿用财联社标签体系（原油市场动态 / 黄金 / 半导体芯片 / 光纤光缆 / 锂电池 / 银行业动态 / 航运期货 / 数据中心 / 储能 / 钠离子电池 / 智能驾驶 / 机器人 …），可随 prompt 迭代扩充。

## 3. 数据模型（一库多标签）

单一条目库 `items`，每条新闻一条记录。关键字段：

```
id              条目唯一 id
published_at    发布时间（统一存北京时间）
title_zh        中文标题（LLM 生成/归一）
summary_zh      一句话中文摘要
body_excerpt    原文摘录
source          信源名（财联社 / 金十 / Walter Bloomberg …）
source_url      原文链接
lang            原文语言（zh / en …）

channels[]      频道：[ai] / [finance] / [ai, finance]   ← AI 财经新闻同时属两频道
categories[]    金融 6 大类（多值）
tags[]          细分标签（多值）
entities[]      标的/公司/代码：[{name, code, market}]   ← 支撑「按标的查」
markets[]       A股/美股/港股/原油/黄金/汇率/债（多值）  ← 支撑「按市场查」

heat            热度：沸/爆/火/热（或数值）
read_count      阅读量（有则存）
engage          评论/分享（有则存）

verified        confirmed / unverified                   ← 地缘 OSINT 默认 unverified
dedup_group     去重簇 id，同一事件多源归并
score           精选打分（热度 × 信源权重 × 时效）
```

设计原则：AI 板块与金融板块是**同一张表的两个频道视图**。一条「英伟达康宁合作」既 `channels=[ai,finance]`，又同时出现在 AI 行业动态和金融「行业·公司」里，但只存一份、只抓一次。

## 4. 信源（分层接入）

### 主干（结构化，做基准）
- **财联社电报**（cls.cn）：时间戳 / 标题 / 正文 / 分类标签 / 阅读量 / 评论 / 分享 齐全
- **金十数据 7×24 快讯**：时间戳 / 标题 / 正文 / 热度标记（沸/爆/火/热）

### 精选 X 补充（英文自动翻译成中文）
- Walter Bloomberg(@DeItaone)、Bloomberg、华尔街日报中文、联合早报、Kobeissi Letter
- 偏市场 / 公司 / 宏观

### 地缘 OSINT（仅风险预警，默认未证实）
- BRICS News、Clash Report、Iran Observer、Daily Iran News、Globe Eye News、OSINTWarfare、Defense Intelligence
- 默认 `verified=unverified`，只进「地缘·风险」与突发流，**不进日报正文**

> 信源权重：主干 > 精选 X > 地缘 OSINT。权重参与精选打分。

## 5. 处理管线（每条新闻过一遍 LLM）

```
抓取
  → 归一化（时间统一北京时间；英文翻译成中文）
  → 去重（标题 + 时间窗 + 语义相似度，并入 dedup_group；
          保留信息最全的一条，并列出该事件的多个来源）
  → LLM 统一打标（一次 prompt 产出：
          title_zh, summary_zh, categories[], tags[],
          entities[], markets[], heat, verified）
  → 打分（score = 热度 × 信源权重 × 时效衰减）→ 是否进精选
  → 入库（写入单一条目库，标 channels）
```

**打标方案：LLM 统一打标（推荐）。** 一条 prompt 同时产出分类 / 标签 / 实体 / 真实性。新增分类只改 prompt，不维护词典。

**A股代码校准（混合）：** 额外配一个轻量「股票名 ↔ 代码」词典，对 LLM 抽取的 A股 `entities` 做代码校准，避免编错代码（如「新易盛→300502」「红板科技→603459」）。

## 6. 对外数据视图（AI 同款 + 金融增量）

| 视图 | 说明 | AI 板块是否已有 |
|---|---|---|
| **日报** | 每天北京时间 8:00 自动生成，按 6 版块打包；每条含中文标题 + 一句话摘要 + 信源 + 原文链接；支持看历史某天 / 最近 N 天 | 有 |
| **精选** | 跨分类高分条目，时间流 Feed，**默认数据源** | 有 |
| **全量** | 按 6 大分类 + 时间窗（最长 7 天）查询 | 有 |
| **搜索** | 关键词搜索 | 有 |
| **按标的/代码查** 🆕 | `entities` 维度，「新易盛 / 英伟达 最近的消息」 | 金融新增 |
| **按市场/资产查** 🆕 | `markets` 维度，「A股 / 原油 / 黄金 今天动态」 | 金融新增 |
| **突发快讯流 + 热度** 🆕 | 单独实时流，带 沸/爆/火/热，盯盘用 | 金融新增 |

> 默认数据源约定：未明确说「日报」「全部 / 全量」时，一律以**精选**回答，保护注意力（沿用 AI 板块约定）。
> 时间窗最长 7 天（保护服务器）。

## 7. 三种接入（对齐现有 aihot skill / API 真实约定）

现有 aihot 公开 API 约定（已从开源仓库 github.com/KKKKhazix/khazix-skills 的 `aihot/SKILL.md` + 线上确认）：

- 基址 `https://aihot.virxact.com/api/public/*`，**必须带浏览器 User-Agent**，否则 403
- `GET /api/public/daily`、`/api/public/daily/{YYYY-MM-DD}`、`/api/public/dailies?take=N`（take 1–180，默认 30）
- `GET /api/public/items?mode=&category=&since=&take=&cursor=&q=`
  - `since` 最多回看 7 天；`take` ≤ 100；限流 600/分钟；无需鉴权
  - `mode`：`selected`（精选，默认）/ `all`（全量）
- AI 分类 slug：`ai-models` / `ai-products` / `industry` / `paper` / `tip`
- 输出：可读中文 markdown，**不暴露**端点/参数/cursor/限流等 API 内部；时间转北京时间
- OpenAPI：`https://aihot.virxact.com/openapi.yaml`

金融板块**复用同一套端点，新增 `channel` 维度与金融参数**，向后兼容（`channel` 缺省 = `ai`）：

### 频道与金融分类 slug
- 频道：`channel=ai`（默认） / `channel=finance`
- 金融 6 大类 slug：

| slug | 版块 |
|---|---|
| `macro` | 宏观·政策 |
| `commodity` | 大宗·能源 |
| `equity` | 股指·汇率 |
| `sector` | 行业·公司 |
| `geo` | 地缘·风险 |
| `ashare` | A股·公告 |

### 端点（金融）
- 日报：`GET /api/public/daily?channel=finance`、`/api/public/daily/{YYYY-MM-DD}?channel=finance`、`/api/public/dailies?channel=finance&take=N`
- 全量/精选：`GET /api/public/items?channel=finance&mode=selected|all&category=<slug>&since=&take=&cursor=&q=`
- 🆕 按标的/代码：`GET /api/public/items?channel=finance&entity=<名称或代码>`（如 `entity=新易盛` 或 `entity=300502`）
- 🆕 按市场/资产：`GET /api/public/items?channel=finance&market=<a-share|us|hk|oil|gold|fx|bond>`
- 🆕 突发快讯流：`GET /api/public/items?channel=finance&mode=breaking`（时间倒序，含 `heat` 字段；可选 `heat=` 过滤）
- 未证实控制：地缘 OSINT 默认 `verified=unverified`；可选 `include_unverified=false` 排除

### Skill
- **扩展现有 `aihot` skill**（不新建独立 skill），在路由表加入 `channel=finance` 分支、金融端点与分类 slug
- 复用同样的：浏览器 User-Agent、输出规则（中文 md、不露 API 内部、时间转北京时间）、默认精选约定
- 路由增量：
  - 「今天金融/财经」「最近行情」→ `items?channel=finance&mode=selected&since=<窗口>`
  - 「金融日报」→ `daily?channel=finance`；指定某天 → `daily/{YYYY-MM-DD}?channel=finance`
  - 「XX股/某公司 消息」→ `items?channel=finance&entity=<名称或代码>`
  - 「A股/原油/黄金 动态」→ `items?channel=finance&market=<...>`
  - 「盯盘/突发/快讯」→ `items?channel=finance&mode=breaking`
  - 「全部/全量」→ `mode=all`；关键词 → `q=`

### RSS
- 三个 Feed：金融精选 / 金融全量 / 金融日报（对应 `channel=finance` 的 `selected` / `all` / `daily`）
- Agent 接入页一键复制

### API
- 在现有 `openapi.yaml` 增补：`channel` 参数、金融分类 slug、`entity` / `market` / `mode=breaking` / `include_unverified` 参数

## 8. 风控与免责

- **地缘假消息**：默认 `unverified` + 不进日报正文；只在「地缘·风险 / 突发」出现并注明「未证实」。同一 `dedup_group` 后续若出现官方源，可升级 `confirmed`。本期为**轻量版**，不做完整辟谣 / 证实关联链路。
- **免责声明**：沿用「本内容基于客观公开资料整理，仅供参考，不代表平台立场，不构成任何投资建议」。
- **服务器保护**：时间窗最长 7 天；按需限流。

## 9. 验收标准（高层）

1. 同一条 AI 财经新闻只存一份，能同时在 AI 与金融两个频道正确出现。
2. 6 大分类能把第 4 节信源的样本新闻一一正确归类。
3. 日报每天 8:00（北京时间）自动产出，6 版块齐全，每条有标题 / 摘要 / 信源 / 链接。
4. 精选 / 全量 / 搜索可用；时间窗 ≤ 7 天生效。
5. 「按标的查」对 A股个股能返回正确代码；「按市场查」「突发流」可用。
6. 地缘 OSINT 条目默认带「未证实」标记，且不出现在日报正文。
7. Skill / RSS / API 三种接入均能取到金融数据，Skill 输出为 md。

## 10. 开放问题 / 待实现时确认

- ~~Skill 新建还是加参数~~ → 已定：**扩展现有 `aihot` skill，加 `channel=finance`**。
- 后端 `items`/`daily` 当前是否已天然支持 `channel`，还是需要加该维度（取决于现有库表结构，实现时确认）。
- A股「股票名 ↔ 代码」词典的来源与更新方式。
- X 信源的抓取方式与频率（受限于现有 AIHOT 抓取基础设施，实现时按现状对接）。
- 金融日报生成时间是否与 AI 日报一致（北京时间 8:00），还是错峰。
