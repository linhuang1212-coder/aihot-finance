# AIHOT 金融板块 · 本地版

一个零依赖、可在本机直接跑起来的金融/财经热点板块。和 AIHOT 的 AI 板块同构：
**精选 + 全量 + 突发 + 日报 + 6 大分类 + 搜索 + 按市场筛选**。

**数据是实时抓取的真实新闻**，启动即抓、后台每 3 分钟自动刷新：

中文（中国大陆可直接访问，无需 VPN）：
- **新浪财经 7×24 全球财经快讯**
- **华尔街见闻 lives**（global / A股 / 美股 / 港股 / 外汇 / 商品 六个频道——A 股个股动态主要来自这里）

英文国际源（**需 VPN**，抓不到会自动跳过）：
- **Google News 商业/财经**（聚合 Reuters / Bloomberg / WSJ / FT / CNN 等）
- **CNBC**（Markets + Top News）、**Yahoo Finance**、**MarketWatch**

> 还没有域名也没关系——这一版完全跑在本机 `localhost`，自己去抓真实信源。
> 抓不到境外源（没开 VPN）时会自动跳过，只保留新浪财经的中文数据，不会报错。

## 运行（只需 Python，无需 pip 安装）

**推荐：一条命令拉起全部（数据服务 + Telegram 机器人 + 守护）**

```bash
python run.py
```

`run.py` 会同时启动 server 和 bot，并提供：子进程**崩溃自动重启**、日志落盘到 `logs/`、
**健康告警**（服务不通 / 数据停更 → 自动发 Telegram 提醒）。

然后浏览器打开： **http://localhost:8910**

> 也可手动单独跑：`python server.py`（数据服务）、`python telegram_bot.py`（机器人）。
> 换端口：PowerShell `$env:PORT=9000; python run.py`。

### 常驻 / 开机自启（Windows 任务计划程序）

1. 打开「任务计划程序」→ 创建基本任务
2. 触发器：选「计算机启动时」（或「登录时」）
3. 操作：启动程序 →
   - 程序：`python`（或 python 绝对路径 `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`）
   - 参数：`run.py`
   - 起始于：`c:\Users\Administrator\Desktop\新闻`
4. 勾选「不管用户是否登录都运行」+「使用最高权限」

这样开机即自动常驻；进程崩了 `run.py` 自动拉起，`run.py` 本身由任务计划程序在重启后拉起。

> ⚠️ VPN 断开时，"VPN异常"这类告警本身也发不出 Telegram（但会记进 `logs/run.log`）；
> 服务崩溃/信源异常等本地故障在 VPN 正常时可正常告警。

## 你会看到什么

- **精选**：跨分类的高质量条目（时间流），默认视图
- **全量**：全部条目
- **突发快讯**：按时间倒序的快讯流，带 沸/爆/火/热 热度
- **金融日报**：按 6 版块打包，可切换历史日期（未证实消息不进日报正文）
- **6 大分类**：宏观·政策 / 大宗·能源 / 股指·汇率 / 行业·公司 / 地缘·风险 / A股·公告
- **搜索**：标题/摘要/标签/标的
- **按市场筛选**：A股 / 美股 / 港股 / 原油 / 黄金 / 汇率
- **未证实角标**：地缘 OSINT 来源默认标「未证实」，可一键隐藏
- **深色 / 浅色** 一键切换

## 目录结构

```
server.py                 本地 API + 静态服务（Python 标准库）
data/items.json           种子数据（48 条，可自行增删/改标签）
public/                   网页前端
  index.html / style.css / app.js
aihot-finance/SKILL.md    金融 Skill（给 Agent 用；见下方说明）
docs/superpowers/         设计文档(specs) + 实施计划(plans)
```

## API（本地）

所有接口在 `http://localhost:8910` 下，金融数据带 `channel=finance`：

| 接口 | 说明 |
|---|---|
| `GET /api/public/items?channel=finance&mode=selected` | 精选 |
| `GET /api/public/items?channel=finance&mode=all` | 全量 |
| `GET /api/public/items?channel=finance&mode=breaking` | 突发流 |
| `GET /api/public/items?channel=finance&category=commodity` | 按分类（macro/commodity/equity/sector/geo/ashare） |
| `GET /api/public/items?channel=finance&entity=英伟达` | 按标的/公司（真实数据里个股代码较少，见数据说明） |
| `GET /api/public/items?channel=finance&market=oil` | 按市场（a-share/us/hk/oil/gold/fx） |
| `GET /api/public/items?channel=finance&q=伊朗` | 关键词 |
| `GET /api/public/items?channel=finance&include_unverified=false` | 排除未证实 |
| `GET /api/public/daily?channel=finance` | 最新金融日报 |
| `GET /api/public/daily/{YYYY-MM-DD}?channel=finance` | 指定日期日报 |
| `GET /api/public/dailies?channel=finance` | 日报日期列表 |

通用参数：`take`（≤100）、`since`（ISO 时间）。

## Telegram 机器人

不需要域名/公网，用长轮询（getUpdates）即可。**需要 VPN**（api.telegram.org 在大陆被墙）。

1. token 放在 `bot_config.json`（已 gitignore，切勿提交；泄露请去 BotFather `/token` 重置）。
2. 先开 server，再开机器人（两个终端）：
   ```bash
   python server.py            # 终端1：数据服务
   python telegram_bot.py      # 终端2：机器人
   ```
3. 在 Telegram 里给 bot 发 `/start` —— 即**自动订阅推送**。之后有新消息会主动发来，**英伟达消息优先置顶、即使没进精选也推**。

   | 命令 | 作用 |
   |---|---|
   | `/start` | 订阅自动推送 + 帮助 |
   | `/stop` / `/subscribe` | 关闭 / 重新开启推送 |
   | `/nvidia` | 英伟达专题 |
   | `/hot` | 精选 |
   | `/breaking` | 突发快讯 |
   | `/daily` | 金融日报 |
   | `/macro /commodity /equity /sector /geo /ashare` | 按分类 |
   | `/oil /gold /astock /us /hk /fx` | 按市场 |
   | `/search 关键词` 或直接发关键词 | 搜索 |

   每条都带真实原文链接；地缘未证实消息标 ⚠️。
   推送状态存在 `bot_state.json`（已 gitignore）。默认推「精选 + 英伟达」的新消息，避免被新浪每分钟的快讯刷屏；英伟达必推。
   推送频率/数量可调：环境变量 `PUSH_INTERVAL`（秒，默认 60）、`PUSH_MAX`（每轮条数，默认 8）。

## 关于 Skill

`aihot-finance/SKILL.md` 是给 Agent（Claude Code / Codex 等）用的金融技能包，里面的接口指向
`aihot.virxact.com`——**那是你将来部署到域名后的形态**。在本地测试时，把里面的
`https://aihot.virxact.com` 换成 `http://localhost:8910`、并去掉 User-Agent 要求即可（本地不校验 UA）。

## 数据说明（哪些是真、哪些是启发式）

抓取逻辑在 `newsfetch.py`，每次刷新写入 `data/items.json`（当缓存用）。

**真实的部分：**
- 标题、正文、发布时间、来源、原文链接 —— 全部来自真实信源，未改动（新浪条目链回 7×24 直播页，Google/CNBC 链回原文）。

**启发式（程序自动打的，可能不准）：**
- `categories / markets / channels` 是**关键词匹配**得出的，不是 LLM 判断。匹配不到就留空（这类条目只在「全量」里出现）。
- 这一版**没有** LLM 打标，也**没有**抓 A 股个股源，所以 `entities`（个股/代码）基本为空、`ashare` 分类很少。要做精准分类、个股代码、按标的查，需要接 A 股专源（如东方财富/同花顺）并加 LLM 打标——见 `docs/superpowers/plans`。

**关于真实性：** 来源真实 ≠ 内容已证实。地缘类消息尤其可能是传闻。本版抓的都是正规媒体（confirmed），暂未接入 OSINT/X 类未证实源。

> 免责声明：本内容基于客观公开资料整理，仅供参考，不代表平台立场，不构成任何投资建议。
