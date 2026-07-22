---
name: aihot-finance
description: >-
  中文金融/财经新闻查询 skill。从 aihot.virxact.com 拉取经过抓取、去重、打分、分类的
  财经资讯：金融日报、精选、全量、按分类、按标的/代码、按市场/资产、突发快讯、关键词搜索。
  当用户问"今天财经/金融发生了什么""某只股/某公司最近消息""A股/原油/黄金动态""盯盘/突发"
  时使用。直接调用公开 REST API，无需 API key。
---

# AIHOT 金融 Skill — 完整参考

## 用途

让 Agent 不开浏览器、不配密钥，直接拿到中文财经/金融资讯。数据来自 AIHOT 的公开
REST API（`aihot.virxact.com`），已做抓取 → 去重 → 打分 → 分类。

**核心原则：** 用户问"今天财经/金融怎么样""某公司/某股最近消息"等，一律查 API，不要用
训练数据作答——API 的内容更新、更全、带原文链接。

---

## 必备：User-Agent 请求头

所有 `/api/public/*` 调用必须带浏览器 User-Agent，否则返回 403。

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=selected"
```

> 本 skill 的所有请求都必须带 `channel=finance`。

---

## 路由优先级（finance）

**默认行为：** 一般性"今天财经/金融有啥"用 `items?channel=finance&mode=selected`——这是
AIHOT 的财经精选，带灵活时间窗。只有用户明确说"日报""全部/全量"时才换端点。

| 用户问法 | 端点 |
|---|---|
| 今天/最近 财经·金融·行情 | `GET /api/public/items?channel=finance&mode=selected&since=<时间窗>` |
| 明确说"金融日报" | `GET /api/public/daily?channel=finance` |
| 某一天的金融日报 | `GET /api/public/daily/{YYYY-MM-DD}?channel=finance` |
| 日报存档列表 | `GET /api/public/dailies?channel=finance&take=N` |
| 明确说"全部/全量" | `GET /api/public/items?channel=finance&mode=all` |
| 某只股 / 某公司的消息 | `GET /api/public/items?channel=finance&entity=<名称或代码>` |
| A股/美股/港股/原油/黄金 动态 | `GET /api/public/items?channel=finance&market=<市场>` |
| 盯盘 / 突发 / 快讯 | `GET /api/public/items?channel=finance&mode=breaking` |
| 关键词搜索 | `GET /api/public/items?channel=finance&q=<词>` |
| 某个方向的全部动态 | `GET /api/public/items?channel=finance&category=<slug>` |

---

## 端点一览

| 端点 | 用途 | 参数 |
|---|---|---|
| `/api/public/daily` | 最新金融日报 | `channel=finance` |
| `/api/public/daily/{YYYY-MM-DD}` | 指定日期日报 | `channel=finance` |
| `/api/public/dailies` | 日报存档列表 | `channel=finance`、`take`(1–180, 默认30) |
| `/api/public/items` | 全部财经条目 | `channel=finance` + `mode/category/since/take/cursor/q/entity/market/include_unverified` |

**约束：**
- `since` 最多回看 7 天（不传则默认窗口）
- `take` 最大 100 条/次
- 限流 600 次/分钟/IP
- 无需鉴权

---

## 六大分类（category slug → 版块）

| slug | 版块 | 覆盖 |
|---|---|---|
| `macro` | 宏观·政策 | 就业(ADP/初请/裁员)、美元指数、美联储、各国央行、货币政策、关税 |
| `commodity` | 大宗·能源 | 原油(OPEC+/油价/产量)、黄金(金价/ETF)、锂/储能、天然气、电力 |
| `equity` | 股指·汇率 | 全球股指(KOSPI/日经/A股/港股)、汇率(USD/JPY)、债市 |
| `sector` | 行业·公司 | 半导体、AI基建(算力/光纤/PCB/数据中心)、新能源车/智驾/机器人、银行、个股财报/融资 |
| `geo` | 地缘·风险 | 中东冲突、霍尔木兹、航运、军工、影响行情的地缘事件 |
| `ashare` | A股·公告 | A股公告速递、涨停/异动、概念板块联动、IPO |

---

## 金融特有参数

- `channel=finance`（**必带**）
- `entity=<名称或代码>` —— 按标的/公司查，如 `entity=新易盛` 或 `entity=300502`、`entity=英伟达`
- `market=<a-share|us|hk|oil|gold|fx|bond>` —— 按市场/资产查
- `mode=breaking` —— 突发快讯流（按时间倒序，条目带 `heat` 热度：沸/爆/火/热）
- `include_unverified=false` —— 排除未证实条目（缺省 `true`，即包含）

---

## 真实性标记（重要）

地缘 OSINT 来源的条目带 `verified="unverified"`。遇到这类条目：

- **必须在输出里标注「未证实」**，绝不能当作已证实的事实陈述。
- 这类条目**不会**出现在金融日报正文里，只会出现在精选/全量/突发/`category=geo` 中。
- 如果用户要"干净、可信"的信息，加 `include_unverified=false`。

---

## 输出格式要求

**铁律：** 输出是给人看的**中文 markdown 新闻简报**——不要出现任何 API 细节（端点、参数、
cursor、限流、HTTP 状态码、缓存）。

**可以出现的元信息：** 时间窗（"5/1–5/7"）、条数、未证实标记、热度、原文链接。
**禁止出现的：** 端点路径、`mode=selected` 之类参数、"600 次/分钟"、cursor 机制、状态码。

### 金融日报样式
```markdown
**AIHOT 金融日报 · 2026-05-07**

## 宏观·政策
1. **<标题>** — <信源>
   <一句话摘要>
   <原文链接>

## 大宗·能源
...
```

### 条目列表样式（多分类）
按分类分组，跨组连续编号。单分类结果用扁平编号列表。

### 时间显示
把 ISO 8601 UTC 转成北京时间，用相对/绝对格式："2 小时前"或"5/7 10:30"——**绝不**直接
甩出原始 UTC 时间戳。

### 突发流样式
带热度标记，适合盯盘：
```markdown
**突发快讯**
- 🔴[爆] 19:15 伊朗向一艘美国军舰警告射击，尚不清楚是否造成损伤 — 金十
- [火] 20:17 美中央司令部：导弹驱逐舰穿越霍尔木兹后正在阿拉伯湾执行任务 — 金十
- [未证实] 21:33 〔OSINT〕称美方接近与伊朗达成协议 — Clash Report
```

---

## 常用工作流

**最近 24 小时财经精选：**
```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
# macOS(BSD date)：
since=$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ)
# Linux(GNU date)：since=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=selected&since=$since&take=50"
```

**某公司 / 某只股：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&entity=英伟达&take=30"
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&entity=300502&take=30"
```

**按市场（原油 / A股 / 黄金）：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&market=oil&take=30"
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&market=a-share&take=30"
```

**按分类（如地缘·风险）：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=selected&category=geo&take=50"
```

**突发盯盘：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&mode=breaking&take=20"
```

**金融日报（最新 / 某天）：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/daily?channel=finance"
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/daily/2026-05-06?channel=finance"
```

**关键词搜索：**
```bash
curl -H "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?channel=finance&q=霍尔木兹&take=30"
```

---

## 反模式（不要做）

- 不要把"今天财经怎么样"路由到 `daily`（时间窗不对，应走 `mode=selected`）
- 没有用户说"全部/全量"时不要默认 `mode=all`
- 不要漏掉 `channel=finance`（漏了会查到 AI 频道或报错）
- 不要把未证实(OSINT)条目当事实——必须标「未证实」
- 不要向用户暴露 cursor / hasNext / 缓存 / 限流等内部细节
- 不要用客户端过滤代替服务端 `q=` / `category=` / `entity=` / `market=`
- 压缩结果时也不要丢掉原文链接
- 绝不编造内容——一律以 API 返回为准

---

**完整 API 文档：** `https://aihot.virxact.com/openapi.yaml`
