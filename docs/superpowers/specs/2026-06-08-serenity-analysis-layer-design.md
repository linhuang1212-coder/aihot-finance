# AIHOT · Serenity 深度分析层 · 设计文档

- 日期：2026-06-08
- 状态：已通过 brainstorming 评审，待写实施计划
- 适用：AIHOT 金融板块 · 本地版（c:\Users\Administrator\Desktop\新闻）

## 0. 背景与目标

现有项目是**数据层**：抓取 → 去重 → 关键词/LLM 打标 → 精选/全量/突发/日报，回答"发生了什么"。
本设计在其之上加一个**分析层**：对单个标的产出 Serenity 式的产业链深度分析（卡脖子/上游、机构信号 vs 噪音、长线价值、风险），回答"该怎么解读"。

灵感来自第三方 Claude Code 技能 `yijiashu/serenity-skill`（白毛股神，蒸馏自 @stockgodserenity 推文）。
**只取其分析框架思路，提示词由本项目自行重写**——该仓库无 License，不照搬其文本/数据。

**核心原则：留框架、淡化人格。** 不做"扮演某人、自信喊票"，只做"用上游卡脖子 + 机构视角 + 长线 + 风险"的分析镜片。

非目标（本期不做）：自动管线分析（每条精选都跑）；真·联网深度研究（抓正文/机构持仓）；前端展示；投资建议或点位预测。

## 1. 范围决策（已与用户确认）

| 决策点 | 选择 |
|---|---|
| 分析数据源 | **现有新闻 feed + Google News RSS 补充**（复用 newsfetch 抓取，零依赖）。不做真·联网深研（留后续升级）。 |
| 触发方式 | **按需 + 服务端端点**（非自动管线）。深度分析贵且慢，只在用户问时跑。 |
| 对外入口 | **Telegram `/serenity <标的>` + `/api/analysis` 端点**。v1 不做前端展示（端点化以后前端可复用）。 |
| 人格强度 | **留框架、淡化人格**；`analysis_pack.md` 由本项目重写，不照搬无 License 仓库。 |
| 依赖 | **零新增第三方依赖**，纯标准库 + 现有 DeepSeek key。 |

## 2. 架构与组件（4 个，各自单一职责、可独立测试）

| 组件 | 职责 | 新建/修改 |
|---|---|---|
| `analysis_pack.md` | Serenity 框架系统提示词（领域规范，改它即调教，无需动代码） | 新建 |
| `serenity.py` | 分析逻辑：`build_context` / `fetch_supplement` / `analyze` | 新建 |
| `server.py` `/api/analysis` | HTTP 端点：取条目 → 调 serenity → 返回 JSON | 修改 |
| `telegram_bot.py` `/serenity` | bot 命令：调端点 → 渲染 | 修改 |

设计原则：`serenity.py` 通过参数接收条目（依赖注入），不直接依赖 server 全局状态，便于单测；
DeepSeek 调用、缓存、预算复用 `llm.py` 的既有模式。

### `serenity.py` 函数契约

```
build_context(entity: str, items: list) -> dict
    # 从传入条目中挑该标的最相关的 N 条（实体命中 + 时效 + score 排序），
    # 返回 {"lines": "格式化文本", "based_on": [item_id...], "sources": [{title,url,source}...]}

fetch_supplement(entity: str) -> list
    # 用 Google News RSS（中/英各一条 query）拉该标的最新头条，零依赖；
    # 失败（无 VPN 等）返回 []，不报错。返回 [{title, source, url}...]

analyze(entity: str, items: list) -> dict
    # 组装 analysis_pack + build_context + fetch_supplement → DeepSeek →
    # 返回 {"status","analysis_md","sources","based_on","generated_at","cached"}
    # 命中 (entity,当天) 缓存则直接返回缓存，cached=true
```

## 3. 数据流

```
用户: /serenity 英伟达
  → bot 先回 "🔍 正在做深度分析…"
  → bot 调 server: GET /api/analysis?channel=finance&entity=英伟达
      → server 从内存 ITEMS 取该标的条目，调 serenity.analyze(entity, items)
          → build_context(entity, items)          取本地相关条目
          → fetch_supplement(entity)              Google News RSS 补最新头条
          → 查 (entity, 当天) 缓存；命中即返回
          → 未命中：DeepSeek(analysis_pack + 上下文 + 补充)，写缓存，预算封顶
      → 返回 JSON
  → bot 渲染 analysis_md 给用户
```

## 4. `analysis_pack.md` 内容大纲（本项目重写）

- **视角定位**：产业链分析视角；非短线、非技术分析；上游供应链 + 机构资金 + 长线。
- **4 心智模型**：① 卡脖子/上游优先（价值在瓶颈环节非终端品牌）② 机构资金行为（信号 vs 噪音）③ 长线价值（3–5 年趋势 > 3–5 天波动）④ 估值重置（re-rating 机会）。
- **决策纪律**：上游优先、瓶颈识别、机构验证、稀缺性定价、技术颠覆风险、多源（multi-source）风险、估值下行风险、范式转移时机。
- **信号 vs 噪音规则**：真信号 = 下游龙头锁上游 / 产能售罄 / 产业链验证；噪音 = 零散小基金"增持 N 股"标题、蹭概念涨停。
- **输出结构**：核心判断 → 卡脖子/上游 → 信号 vs 噪音 → 风险 → 底线。
- **表达纪律**：中文为主、专业英文术语点缀；地缘标「未证实」；不预测点位；拿不准少说别编；末尾固定免责。

## 5. 输出结构与纪律

固定五段结构（对齐演示）：**核心判断 / 卡脖子·上游 / 信号 vs 噪音 / 风险 / 底线**。
强制纪律：
- 标注信号与噪音；地缘类标「未证实」。
- **每条分析末尾带**："本分析为框架推理，非投资建议；不预测点位。"
- 诚实边界：声明手上无实时机构持仓/订单数据（market.py 仅有报价）。

## 6. 缓存 / 预算 / 失败兜底

- **缓存**：按 `(entity, 当天日期)` 存 `data/analysis_cache.json`，重复问不重复花钱（同 `llm.py` 缓存思路）。
- **预算**：单次分析一次 DeepSeek 调用；`max_tokens` 走环境变量（默认约 1200）。
- **失败兜底（均不崩）**：
  - 无 DeepSeek key → `status=no_llm`，友好提示。
  - 该标的无新闻 → `status=no_data`。
  - DeepSeek 超时/异常 → `status=error` + 友好提示。
  - RSS 补充失败 → 跳过补充，仅用本地条目继续。
- bot 调用端点超时上调到 ~90s（深度分析较慢）。

## 7. API 契约

```
GET /api/analysis?channel=finance&entity=<名称|代码>

200 响应：
{
  "status":   "ok" | "no_llm" | "no_data" | "error",
  "entity":   "英伟达",
  "analysis_md": "…markdown…",        // status=ok 时有
  "message":  "…友好提示…",            // status!=ok 时有
  "sources":  [{ "title","url","source" }...],
  "based_on": ["itemid", ...],
  "generated_at": "ISO8601 +08:00",
  "cached":   true | false
}
```
- 缺 `entity` → `status=no_data` + 用法提示（或 400，实现时择一，本期取前者更友好）。
- 复用现有：`channel`（缺省 finance 语境）；不引入鉴权。

## 8. Telegram 命令

- `/serenity <标的>`（别名 `/deep <标的>`）：先回"🔍 正在做深度分析…"，再调端点、渲染 `analysis_md`（或 `message`）。
- 无参 → 提示用法（如"用法：/serenity 英伟达"）。
- 加进 `HELP` 文本；超时上调到 ~90s。

## 9. 测试策略（契约 + 单元；DeepSeek 用录制响应，不真调）

- `build_context`：给样本条目 + 实体 → 选对条目、`based_on`/`sources` 正确、格式化文本含标题。
- `fetch_supplement`：mock RSS → 返回头条列表；网络失败 → 返回 `[]` 不抛。
- `analyze`：喂 mock 的 DeepSeek 响应 → 返回结构化 `analysis_md`、含免责串、`status=ok`、写入缓存；二次调用 `cached=true` 不再"调用"。
- 端点契约：`GET /api/analysis?entity=英伟达` → 200、`status=ok`、`analysis_md` 非空且含免责。
- 兜底：无 key → `status=no_llm`；无该标的新闻 → `status=no_data`；各自 `message` 友好。
- 回归：现有路由（items/daily/categories/静态）行为不变。

## 10. 明确不做（YAGNI / 留待以后）

- 前端展示分析（先只 bot + API）。
- 真·联网深度研究（抓正文 / 机构持仓 / HBM 份额这类 feed 外硬数据）—— 后续升级。
- 自动管线分析（每条精选都跑）。
- 机构持仓数据源接入（market.py 暂只报价）。

## 11. 验收标准

1. `/serenity 英伟达` 在 Telegram 返回结构化中文分析，含五段结构、信号 vs 噪音、风险、末尾免责。
2. `GET /api/analysis?entity=英伟达` → 200、`status=ok`、`analysis_md` 非空且含免责。
3. 无 DeepSeek key → `status=no_llm` 友好提示，不崩。
4. 该标的无新闻 → `status=no_data`。
5. 同一标的当天重复请求命中缓存（`cached=true`），不重复调 DeepSeek。
6. 现有端点与功能不回归。
7. 零新增第三方依赖（纯标准库 + 现有 key）。

## 12. 待确认（实现中回填）

- 缓存 TTL：当天有效 vs N 小时（默认"当天"，实现时定）。
- `analysis_pack.md` 是否同时产出结构化字段（如 importance）供打分用——本期只产 markdown，不改现有打分。
- `/serenity` 无参是否默认分析自选股（英伟达）——本期取"提示用法"，更明确。
