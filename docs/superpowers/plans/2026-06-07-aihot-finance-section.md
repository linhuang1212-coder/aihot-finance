# AIHOT 金融板块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AIHOT 现有引擎上新增一个与 AI 板块同构的金融板块（日报 + 精选 + 全量 + 搜索 + 按标的/按市场/突发），数据进同一条目库（多频道多标签），并通过扩展现有 `aihot` skill、RSS、OpenAPI 对外开放。

**Architecture:** 一库多标签——单一条目库新增 `channel`（ai/finance）与金融标签维度；信源分层接入（中文快讯主干 + 精选 X + 地缘 OSINT 未证实）；每条新闻经「归一化→去重→LLM 统一打标→打分」管线入库；公开 API 复用现有 `/api/public/daily` 与 `/api/public/items`，新增 `channel` 及金融参数；Skill 扩展现有 `aihot/SKILL.md`。

**Tech Stack:** 沿用 AIHOT 现有后端栈（数据库 + 抓取 + 定时任务 + REST API，具体栈以现有代码为准）。新增/修改集中在：数据模型、信源 adapter、处理管线、日报生成、公开 API 参数、Skill/RSS/OpenAPI、前端「金融」入口。

---

## ⚠️ 执行前必读：本计划与你的代码库的映射

本计划的作者**没有 AIHOT 后端源码**。因此：

- **文件路径按职责描述**（如「条目库 schema/模型」「公开 API 路由」），执行方需映射到你后端的真实文件后再开工。第一步就是 Task 0：定位这些文件。
- **可精确定死的部分已写实**：数据模型字段、API 请求/响应契约、LLM 打标 prompt、完整 Skill 文本、契约测试的 curl 与期望 JSON 形状。这些不要改写，照搬。
- **测试策略**：以**契约测试**为主（构造请求 → 断言响应 JSON 形状/字段），因为 API 契约是确定的，与后端实现语言/框架无关。单元测试（去重、打标、词典校准）给出输入输出样例，执行方用本仓库测试框架落地。
- **频繁提交**：每个 Task 末尾提交一次。

参考资料（设计依据）：`docs/superpowers/specs/2026-06-07-aihot-finance-section-design.md`

---

## File Structure（按职责）

执行方在 Task 0 把下列职责映射到真实文件，后续任务引用这些职责名：

| 职责名 | 负责 | 预计 新建/修改 |
|---|---|---|
| `STORE_SCHEMA` | 条目库表结构 / 模型定义 + 迁移 | 修改 |
| `DAILY_MODEL` | 日报表结构 / 模型 | 修改 |
| `ADAPTER_CLS` | 财联社电报 抓取适配器 | 新建 |
| `ADAPTER_JIN10` | 金十 7×24 抓取适配器 | 新建 |
| `ADAPTER_X` | 精选 X 账号 抓取适配器（含翻译） | 新建 |
| `ADAPTER_OSINT` | 地缘 OSINT 抓取适配器（默认未证实） | 新建 |
| `PIPELINE_NORMALIZE` | 归一化（时间/语言/翻译） | 新建 |
| `PIPELINE_DEDUP` | 去重（dedup_group） | 新建 |
| `PIPELINE_TAGGER` | LLM 统一打标 | 新建 |
| `TICKER_DICT` | A股「股票名↔代码」词典 + 校准 | 新建 |
| `PIPELINE_SCORE` | 精选打分 | 新建 |
| `DAILY_GEN` | 金融日报生成（定时任务） | 修改/新建 |
| `API_ITEMS` | `GET /api/public/items` 路由/处理器 | 修改 |
| `API_DAILY` | `GET /api/public/daily*` 路由/处理器 | 修改 |
| `RSS_FEEDS` | RSS feed 生成 | 修改/新建 |
| `OPENAPI` | `openapi.yaml` | 修改 |
| `SKILL_AIHOT` | `aihot/SKILL.md`（skills 仓库） | 修改 |
| `WEB_NAV` | 主站「金融」入口 / Agent 接入页 | 修改 |

---

## 数据契约（全计划共用，先定死）

### 条目（item）字段

```
id            string   条目唯一 id
published_at  string   ISO8601（内部存 UTC，输出转北京时间）
title_zh      string   中文标题
summary_zh    string   一句话中文摘要
body_excerpt  string   原文摘录
source        string   信源名（"财联社" / "金十" / "Walter Bloomberg" ...）
source_url    string   原文链接
lang          string   原文语言 "zh" | "en" | ...
channels      string[] ["ai"] | ["finance"] | ["ai","finance"]
categories    string[] 金融分类 slug（见下）
tags          string[] 细分标签（自由扩展）
entities      object[] [{ name, code, market }]
markets       string[] ["a-share"|"us"|"hk"|"oil"|"gold"|"fx"|"bond"]
heat          string   "沸"|"爆"|"火"|"热"|""（无则空）
read_count    number?  阅读量（有则存）
verified      string   "confirmed" | "unverified"
dedup_group   string   去重簇 id
score         number    精选打分
```

### 金融分类 slug ↔ 版块（固定 6 个）

| slug | 版块 |
|---|---|
| `macro` | 宏观·政策 |
| `commodity` | 大宗·能源 |
| `equity` | 股指·汇率 |
| `sector` | 行业·公司 |
| `geo` | 地缘·风险 |
| `ashare` | A股·公告 |

### 公开 API 参数（在现有 items/daily 上新增）

- `channel`：`ai`（缺省，向后兼容） | `finance`
- `category`：finance 时取上表 slug
- `mode`：`selected`（缺省） | `all` | `breaking`（金融新增）
- `entity`：名称或代码（按标的）
- `market`：`a-share|us|hk|oil|gold|fx|bond`（按市场）
- `include_unverified`：`true`（缺省） | `false`
- 沿用现有：`since`（≤7 天）、`take`（≤100）、`cursor`、`q`

---

## Phase 0 — 定位与基线

### Task 0: 映射职责到真实文件 + 建立基线

**Files:**
- 只读浏览后端仓库

- [ ] **Step 1: 定位并记录上表每个职责名对应的真实文件路径**，写入 `docs/superpowers/plans/_file-map.md`（一行一个：`STORE_SCHEMA = src/...`）。
- [ ] **Step 2: 跑通现有测试套件**，记录命令与当前通过数，作为基线。
  - Run: 本仓库的测试命令（如 `npm test` / `pytest` / `go test ./...`）
  - Expected: 全绿（记录数量）
- [ ] **Step 3: 确认现有 `items`/`daily` 是否已支持 `channel`**；若已隐式只返回 AI 数据，记录如何加 `channel` 维度（加列 / 加索引）。
- [ ] **Step 4: Commit**
```bash
git add docs/superpowers/plans/_file-map.md
git commit -m "docs: map finance-section responsibilities to real files"
```

---

## Phase 1 — 数据模型（一库多标签）

### Task 1: 扩展条目库 schema

**Files:**
- Modify: `STORE_SCHEMA`
- Test: 本仓库迁移/模型测试位置

- [ ] **Step 1: 写失败测试** —— 断言新建一条 item 可写入并读回新字段。
```
test "item supports finance multi-label fields":
  item = create_item(
    title_zh="测试", channels=["ai","finance"],
    categories=["sector"], tags=["半导体"],
    entities=[{name:"英伟达", code:null, market:"us"}],
    markets=["us"], heat="火", verified="confirmed",
    dedup_group="g1", score=12.3)
  got = load_item(item.id)
  assert got.channels == ["ai","finance"]
  assert got.categories == ["sector"]
  assert got.entities[0].name == "英伟达"
  assert got.verified == "confirmed"
```
- [ ] **Step 2: 跑测试确认失败**（字段不存在）。
- [ ] **Step 3: 加字段 + 迁移**：按「数据契约」补齐 `channels, categories, tags, entities, markets, heat, read_count, verified, dedup_group, score`；为 `channels`、`categories`、`dedup_group`、`published_at` 建查询索引；`entities` 用可被检索的结构（如 JSON 列 + 名称/代码反查表，或全文索引），以支撑 `entity=` 查询。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(store): add finance multi-label fields to items`

### Task 2: 日报支持 channel

**Files:** Modify: `DAILY_MODEL`
- [ ] **Step 1: 写失败测试** —— 同一天可分别存在 `channel="ai"` 与 `channel="finance"` 两份日报且互不覆盖。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 给日报模型加 `channel` 字段**，唯一键改为 `(date, channel)`。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(daily): scope daily report by channel`

---

## Phase 2 — 信源接入（分层）

> 每个 adapter 的产物是「原始条目」（标题、正文、时间、source、source_url、lang、可得的 heat/read_count），不做分类/实体（那是 Phase 3 的 LLM 的活）。OSINT adapter 额外把 `verified` 预置为 `unverified`。

### Task 3: 财联社电报 adapter（ADAPTER_CLS）

**Files:** Create: `ADAPTER_CLS`; Test: adapter 测试位置
- [ ] **Step 1: 写失败测试** —— 喂一段保存好的财联社电报样本 HTML/JSON 夹具，断言解析出 `{title, body, published_at, source:"财联社", source_url, heat, read_count}`。用本仓库的「中美防长撤军」类样本（参见 specs 截图）做夹具。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现解析**：提取标题（去【】）、正文、时间（北京时间→存 UTC）、阅读量（"阅 xxW"→数值）、分类标签先保留为 `tags` 原文。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(ingest): add 财联社 adapter`

### Task 4: 金十 7×24 adapter（ADAPTER_JIN10）

**Files:** Create: `ADAPTER_JIN10`; Test: adapter 测试位置
- [ ] **Step 1: 写失败测试** —— 金十快讯样本 → `{title, body, published_at, source:"金十", heat(沸/爆/火/热)}`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现解析**（含 沸/爆/火/热 热度映射到 `heat`）。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(ingest): add 金十 adapter`

### Task 5: 精选 X adapter（ADAPTER_X，含翻译）

**Files:** Create: `ADAPTER_X`; Test: adapter 测试位置
- [ ] **Step 1: 写失败测试** —— 英文推文样本（如 Walter Bloomberg）→ `{title_zh(译), body_excerpt, source, source_url, lang:"en"}`；断言中文非空。允许白名单账号：`@DeItaone, Bloomberg, ChineseWSJ, zaobaosg, KobeissiLetter`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**：抓取白名单账号 + 调用现有翻译/LLM 把英文转中文摘要（翻译可复用 Phase 3 的 LLM 调用，避免双份成本——本步只需保证产出中文 body）。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(ingest): add curated X adapter with translation`

### Task 6: 地缘 OSINT adapter（ADAPTER_OSINT，默认未证实）

**Files:** Create: `ADAPTER_OSINT`; Test: adapter 测试位置
- [ ] **Step 1: 写失败测试** —— OSINT 样本（如 BRICS News/Clash Report）→ 条目 `verified == "unverified"` 且 `source` 在 OSINT 白名单。白名单：`BRICSinfo, clashreport, IranObserver0, DailyIranNews, GlobeEyeNews, OSINTWarfare, DI313_`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**：抓取 + 翻译 + 强制 `verified="unverified"`。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(ingest): add OSINT adapter (default unverified)`

---

## Phase 3 — 处理管线

### Task 7: 归一化（PIPELINE_NORMALIZE）

**Files:** Create: `PIPELINE_NORMALIZE`; Test: 管线测试位置
- [ ] **Step 1: 写失败测试** —— 输入混合时区/语言的原始条目，断言 `published_at` 统一为 UTC，`lang` 正确，缺中文标题的英文条目带 `needs_translation=true`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现归一化。**
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): normalize raw items`

### Task 8: 去重（PIPELINE_DEDUP）

**Files:** Create: `PIPELINE_DEDUP`; Test: 管线测试位置
- [ ] **Step 1: 写失败测试** —— 三条同一事件不同源（财联社/金十/X 的「霍尔木兹自由计划暂停」）在 30 分钟窗口内，断言归入同一 `dedup_group`，且代表条目为信息最全那条，`sources` 聚合三家。两条不相关新闻不并簇。
```
test "dedup groups same event across sources":
  g = dedup([cls_item, jin10_item, x_item, unrelated_item])
  assert cls_item.dedup_group == jin10_item.dedup_group == x_item.dedup_group
  assert unrelated_item.dedup_group != cls_item.dedup_group
```
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**：同 `(标题+正文)` 语义相似度（embedding 或现有相似度工具）+ 时间窗（默认 30 分钟，可配）聚簇；代表条目选 body 最长/信源权重最高者。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): dedup events into clusters`

### Task 9: LLM 统一打标（PIPELINE_TAGGER）

**Files:** Create: `PIPELINE_TAGGER`; Test: 管线测试位置（用录制的 LLM 响应做夹具，避免真调用）
- [ ] **Step 1: 写失败测试** —— 给定「红板科技子公司竞拍江西志浩100%股权」原文 + 录制的 LLM 响应夹具，断言解析出 `categories=["ashare","sector"]`、`entities` 含 `{name:"红板科技", code:"603459", market:"a-share"}`、`channels` 含 `finance`。再给「英伟达康宁合作」断言 `channels=["ai","finance"]`、`categories=["sector"]`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现打标**，prompt 固定如下（照搬）：
```
你是金融新闻打标器。读这条新闻，只输出 JSON，不要解释。
字段：
- title_zh: 简洁中文标题（≤30字）
- summary_zh: 一句话中文摘要（≤50字）
- channels: 数组，取值 ["ai","finance"]。涉及 AI 公司/模型/算力/AI芯片→含 "ai"；涉及行情/宏观/大宗/个股/地缘→含 "finance"；两者皆是则都给。
- categories: 数组，仅取 finance 时用，取值
  ["macro","commodity","equity","sector","geo","ashare"]
  宏观/央行/就业/汇率政策→macro；原油/黄金/锂/天然气/电力→commodity；
  股指/汇率/债→equity；半导体/AI基建/车/银行/个股财报融资→sector；
  中东/霍尔木兹/军工/航运/地缘→geo；A股公告/涨停/IPO→ashare。可多选。
- tags: 细分中文标签数组（如 ["原油","OPEC+"]）
- entities: 数组 [{name, code, market}]。market 取
  ["a-share","us","hk","oil","gold","fx","bond"] 或 null；非个股则空数组。
  代码不确定就给 null（后续词典校准），不要编造。
- markets: 数组，同 market 取值集合，标明涉及的市场/资产。
- 仅输出上述 JSON。
新闻原文：
<<<{body}>>>
```
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): LLM unified tagger`

### Task 10: A股代码校准（TICKER_DICT）

**Files:** Create: `TICKER_DICT`（名称→代码词典 + 校准函数）; Test: 管线测试位置
- [ ] **Step 1: 写失败测试** —— `entities` 含 `{name:"新易盛", code:null}` 经校准变为 `code:"300502"`；`{name:"红板科技"}`→`603459`；未知名称保持 `null`。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**：内置/可更新的 A股名称↔代码词典（来源在「待确认」记录），对 LLM 给出的 a-share 实体做精确名匹配回填；LLM 已给代码但与词典不符时以词典为准。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): A-share ticker dictionary calibration`

### Task 11: 打分（PIPELINE_SCORE）

**Files:** Create: `PIPELINE_SCORE`; Test: 管线测试位置
- [ ] **Step 1: 写失败测试** —— 同内容下，`heat="爆"` 比无 heat 分高；财联社（主干）比 OSINT 分高；越新越高（时效衰减）。断言相对排序，不断言绝对值。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现** `score = w_heat(heat) + w_source(source) + w_recency(now-published_at)`；权重：主干 > 精选X > OSINT。精选阈值可配。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): selection scoring`

### Task 12: 串起入库管线

**Files:** Modify: 抓取调度入口
- [ ] **Step 1: 写失败测试**（集成）—— 喂 4 个 adapter 的混合样本，跑完整管线，断言库里条目带齐 channels/categories/entities/verified/dedup_group/score，且 OSINT 条目 verified=unverified。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 接线** normalize→dedup→tagger→ticker→score→入库。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(pipeline): wire finance ingestion end-to-end`

---

## Phase 4 — 公开 API（复用现有端点 + channel/金融参数）

> 契约测试：用 HTTP 层测试（本仓库的 API 测试方式）发请求、断言响应 JSON。所有 `/api/public/*` 请求带浏览器 User-Agent。

### Task 13: items 支持 channel + 金融分类 + entity/market

**Files:** Modify: `API_ITEMS`; Test: API 测试位置
- [ ] **Step 1: 写失败契约测试**：
```
GET /api/public/items?channel=finance&mode=selected&take=5
  → 200; 每条 item.channels 含 "finance"; 不含纯 ai-only 条目
GET /api/public/items?channel=finance&category=commodity
  → 200; 每条 categories 含 "commodity"
GET /api/public/items?channel=finance&entity=新易盛
  → 200; 每条 entities 有 name=="新易盛" 或 code=="300502"
GET /api/public/items?channel=finance&market=oil
  → 200; 每条 markets 含 "oil"
GET /api/public/items   (无 channel)
  → 200; 行为与改动前一致（默认 ai，回归不破）
```
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现** `channel`（缺省 ai）、finance `category` slug 过滤、`entity`（按 name/code 检索 entities）、`market` 过滤；保留 `since≤7d`、`take≤100`、`cursor`、`q`。
- [ ] **Step 4: 跑测试确认通过（含无参回归）。**
- [ ] **Step 5: Commit** `feat(api): items channel + finance filters`

### Task 14: 突发流 + 未证实控制

**Files:** Modify: `API_ITEMS`; Test: API 测试位置
- [ ] **Step 1: 写失败契约测试**：
```
GET /api/public/items?channel=finance&mode=breaking&take=10
  → 200; 按 published_at 倒序; 每条含 heat 字段
GET /api/public/items?channel=finance&include_unverified=false
  → 200; 无 verified=="unverified" 的条目
GET /api/public/items?channel=finance&category=geo
  → 200; OSINT 条目出现且带 verified=="unverified"
```
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现** `mode=breaking`（纯时间倒序，附 heat）与 `include_unverified`（缺省 true）。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(api): breaking stream + unverified control`

### Task 15: daily 支持 channel

**Files:** Modify: `API_DAILY`; Test: API 测试位置
- [ ] **Step 1: 写失败契约测试**：
```
GET /api/public/daily?channel=finance
  → 200; 返回当日金融日报; sections 的 slug ⊆ {macro,commodity,equity,sector,geo,ashare}
GET /api/public/daily/2026-05-06?channel=finance → 200 该日金融日报
GET /api/public/dailies?channel=finance&take=7  → 200 仅金融日报存档
GET /api/public/daily   (无 channel) → 200 行为不变（AI 日报）
```
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现** daily/dailies 的 `channel` 过滤（缺省 ai）。
- [ ] **Step 4: 跑测试确认通过（含无参回归）。**
- [ ] **Step 5: Commit** `feat(api): daily channel support`

---

## Phase 5 — 日报生成

### Task 16: 金融日报定时生成（DAILY_GEN）

**Files:** Modify/Create: `DAILY_GEN`; Test: 生成器测试位置
- [ ] **Step 1: 写失败测试** —— 给定一批昨日金融条目，生成器产出 `channel="finance"` 日报：6 版块（缺数据的版块可空）、每条含 title_zh/summary_zh/source/source_url、**不含** verified=="unverified" 的条目（OSINT 不进日报正文）、每版块有条数上限（可配）。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**：复用 AI 日报生成器，参数化 `channel`；按 6 slug 分组；过滤未证实；写入 `(date, channel=finance)`。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: 挂定时任务**：北京时间每日 08:00 生成（与 AI 日报一致或错峰，见「待确认」）。
- [ ] **Step 6: Commit** `feat(daily): generate finance daily report at 08:00 CST`

---

## Phase 6 — 接入层（Skill / RSS / OpenAPI / 前端）

### Task 17: 扩展 aihot Skill（SKILL_AIHOT）

**Files:** Modify: `aihot/SKILL.md`（skills 仓库 github.com/KKKKhazix/khazix-skills）
- [ ] **Step 1:** 在现有 `aihot/SKILL.md` 末尾**追加**以下「金融频道」整段（照搬）：

````markdown
---

## Finance Channel (channel=finance)

The same public API also serves a **finance** channel. Add `channel=finance` to
`daily` and `items` endpoints. All other rules (browser User-Agent, Beijing-time
output, Chinese-markdown only, no API internals, default = selected) are identical.

### Finance categories (slug → 版块)
| slug | 版块 |
|---|---|
| `macro` | 宏观·政策 |
| `commodity` | 大宗·能源 |
| `equity` | 股指·汇率 |
| `sector` | 行业·公司 |
| `geo` | 地缘·风险 |
| `ashare` | A股·公告 |

### Routing (finance)
| User query | Endpoint |
|---|---|
| 今天/最近 金融·财经·行情 | `items?channel=finance&mode=selected&since=<window>` |
| 金融日报 | `daily?channel=finance` |
| 某天的金融日报 | `daily/{YYYY-MM-DD}?channel=finance` |
| 某只股/某公司的消息 | `items?channel=finance&entity=<名称或代码>` |
| A股/原油/黄金/美股 动态 | `items?channel=finance&market=<a-share\|us\|hk\|oil\|gold\|fx\|bond>` |
| 盯盘/突发/快讯 | `items?channel=finance&mode=breaking` |
| 全部/全量 | add `mode=all` |
| 关键词 | add `q=<term>` |

### Finance-only params
- `channel=finance` (required for finance)
- `entity=<name|code>`  e.g. `entity=新易盛` or `entity=300502`
- `market=<a-share|us|hk|oil|gold|fx|bond>`
- `mode=breaking`  (time-desc stream with `heat`)
- `include_unverified=false`  (exclude OSINT-sourced unverified items)

### Verified flag
Geopolitical OSINT items carry `verified="unverified"`. When such items appear,
**label them 「未证实」 in the output** and never present them as confirmed fact.
They never appear in the finance daily report body.

### Finance examples
```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
# 今天财经精选
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=selected&take=50"
# 某公司
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&entity=英伟达&take=30"
# 原油
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&market=oil&take=30"
# 突发盯盘
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=breaking&take=20"
# 金融日报
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/daily?channel=finance"
```
````
- [ ] **Step 2:** 把 `description` frontmatter 从「AI news」扩成「AI 与金融/财经 news」，让 Agent 能在财经提问时触发。
- [ ] **Step 3: Commit**（在 skills 仓库）`feat(aihot-skill): add finance channel`

### Task 18: RSS 三个金融 Feed（RSS_FEEDS）

**Files:** Modify/Create: `RSS_FEEDS`; Test: RSS 测试位置
- [ ] **Step 1: 写失败测试** —— 三个 feed URL 各返回合法 RSS：金融精选(`channel=finance&mode=selected`)、金融全量(`mode=all`)、金融日报(`daily?channel=finance`)；item 含中文标题/链接/时间。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现三个 feed**（复用 AI feed 生成器，加 channel）。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(rss): finance feeds`

### Task 19: OpenAPI 增补（OPENAPI）

**Files:** Modify: `openapi.yaml`
- [ ] **Step 1: 写失败测试** —— OpenAPI schema 校验通过，且 `items`/`daily` 参数含 `channel`、`entity`、`market`、`include_unverified`，`mode` 枚举含 `breaking`，并文档化 finance 分类 slug。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 补全 `openapi.yaml`。**
- [ ] **Step 4: 跑校验确认通过。**
- [ ] **Step 5: Commit** `docs(openapi): finance params`

### Task 20: 主站「金融」入口（WEB_NAV）

**Files:** Modify: `WEB_NAV`
- [ ] **Step 1:** 主站左侧（与「AI」并列）加「金融」入口，复用现有列表/日报/精选/全量/搜索 UI，数据源切到 `channel=finance`；Agent 接入页加金融 Skill 安装句、三个 RSS、API 说明。
- [ ] **Step 2:** 冒烟：金融入口能看到精选 Feed、能切 6 分类、能看日报、能搜索；未证实条目带「未证实」角标。
- [ ] **Step 3: Commit** `feat(web): finance section entry`

---

## Self-Review（作者已过一遍）

- **Spec 覆盖**：6 大分类(Task 9/16)、一库多标签(Task 1)、分层信源(Task 3–6)、去重(Task 8)、LLM 打标(Task 9)、A股代码(Task 10)、打分(Task 11)、日报(Task 16)、精选/全量/搜索/按标的/按市场/突发(Task 13–14)、Skill/RSS/API/前端(Task 17–20)、未证实风控(Task 6/14/16)——均有对应任务。
- **回归保护**：Task 13/15 含「无 channel 行为不变」断言，确保 AI 板块不被破坏。
- **类型一致**：字段名、slug、参数名全计划用同一份「数据契约」，前后一致。
- **未证实**：Q5 未选「完整辟谣链路」，故只做 `verified` 标记 + `include_unverified` + 不进日报，无证实/辟谣关联任务。

## 待确认（执行中回填）

- 后端是否已有 `channel` 概念（Task 0 输出）。
- A股名称↔代码词典来源与更新频率（Task 10）。
- 金融日报时间：与 AI 日报同为 08:00 还是错峰（Task 16）。
- X / OSINT 抓取的具体接入方式（受现有抓取基础设施约束，Task 5/6）。
