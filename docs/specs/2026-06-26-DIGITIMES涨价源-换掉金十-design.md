# DIGITIMES「涨价大追踪」接入 + 取消金十 + X补4账号 设计文档

> 舆情系统 · 信源向「半导体/涨价/算力主线」收敛。2026-06-26。

## 背景
用户连发 3 条需求,本质是一次**信源 pivot**:把宽口径的金十快讯换成更聚焦算力主线的源。
1. X 加 4 个半导体/苹果供应链分析师。
2. 接入 DIGITIMES「涨价大追踪」专题(纯涨价/缺货/产能 feed,正合主线)。
3. 取消金十数据源。

## ① X 补 4 账号(已落)
`x_watch.json` kol 组新增(handle 已对 twitterapi 实测确认):
- `mingchikuo` 郭明錤、`TrendForce`、`dnystedt` Dan Nystedt、`DIGITIMESAsia` DIGITIMES Asia。
- 全 confirmed(不用 osint)。改完重启 server 生效(xfetch 模块级缓存 `_watch`)。

## ② DIGITIMES 涨价大追踪源

**URL**:`https://www.digitimes.com.tw/tech/dt/most.asp?pack=77820&cnlid=1`(pack=77820=涨价大追踪)。
**可行性实测**:200 OK / 237KB / utf-8;**列表页公开无付费墙**(正文付费,不取);单页约 19 篇,全部涨价/记忆体/封测/产能主题。

**列表 DOM(实测)**:
```html
<p style="padding:0;font-weight: 600;"><a href="/tech/dt/n/shwnws.asp?...&id=759909&packageid=77820">三星DRAM單價年增逾400%　…</a></p>
<div style="color:#808080;margin-top:-10px;">2026/6/26</div>
<h7 class="hidden-xs" title="三星電子…(摘要)">…</h7>
```

**实现**:`newsfetch.fetch_digitimes()` = `_http_get(url)` -> `_parse_digitimes(html)`(纯函数,可测)。
- 标题锚正则(font-weight:600 唯一锁定标题,避开同 id 的缩图锚):
  `font-weight:\s*600;?"><a href="(/tech/dt/n/shwnws\.asp\?[^"]*?id=(\d+)[^"]*)">([^<]+)</a>`
- 锚后 ~700 字窗口取日期 `color:#808080[^>]*>\s*([\d/]+)` 和摘要 `<h7[^>]*title="([^"]*)"`。
- 日期 `2026/6/26` -> `datetime(y,m,d,8,0, tzinfo=CST)`(列表无具体时分,统一 08:00;同日多篇并列可接受);解析失败跳过。
- 条目字段同 fetch_jin10:`id="dt"+aid`(稳定,id 去重防重复入库)、`source="DIGITIMES·涨价追踪"`、`lang="zh"`(繁体,**暂不繁转简**,可读;零依赖)、`source_url=完整 shwnws 链接`、`title_zh/summary_zh/body_excerpt`、`_classify(title+summary)` 出 cats/markets/channels、`selected=bool(cats)`、`entities=[]`(列表无股票代码)、`dedup_group=_hash_id(_norm_title(title))`、`verified="confirmed"`。
- `SOURCE_TIER1` 加 `"DIGITIMES"`:保证 `_keep` 直通(line 709 tier==1)+ 给一线权重。注意 `_source_tier` 看的是 `source` 字段;带 `·涨价追踪` 后缀须前缀匹配 -> 用 `_source_tier` 现成逻辑确认(若它精确匹配则改用 startswith,或 TIER1 收 "DIGITIMES·涨价追踪")。**实现时核对**。

**接管线**:`fetch_all` 把 `items = fetch_jin10()` 改为 `items = fetch_digitimes()`。其余(_save_trans/_merge_x_bursts/_tag_entities/_keep/_cluster/llm/打分/mainline)不变 -> DIGITIMES 涨价文天然命中 THEME_KW -> mainline+精选。

## ③ 取消金十
`fetch_all` 不再调 `fetch_jin10()`(函数保留)。连带:
- `if it.get("source")=="金十" and _is_china_related(it)` 强制选中分支**变惰性**(无金十条目),保留无害。
- 库内旧金十数据**自然老化**(沿既有先例,不清库)。

## ④ 前端
信源组现为 金十 / X·KOL / 个股。改为:
- `金十` 项 -> 换成 `DIGITIMES`(`data-view="source" data-source="DIGITIMES"`,前缀匹配 `DIGITIMES·涨价追踪`)。
- `个股`项 **删除**(唯一数据源金十已撤,A 股实体无来源)。app.js 的 `view==="stock"` 分支可留(无害)或删;先留。
- VIEW_TITLES/KICKERS 视情更新。

## 边界 / 已知
- DIGITIMES 繁体未转简:title 繁、summary_zh 可能被 LLM 规整为简;混排可接受,后续可加繁转简。
- 列表无精确时间 -> 同日多篇时间并列;day 级排序。
- 取消金十后内容量 = X(KOL~33) + DIGITIMES(~19/页);够用。传播多源印证仍稀疏(既有问题)。
- **个股栏功能性损失**:已向用户说明,需 A 股源才能恢复。

## 测试(TDD)
1. `test_newsfetch.py::TestFetchDigitimes`:`_parse_digitimes(fixture)` -> ≥2 条,字段齐(id 带 dt、source、繁体 title、date 解析、summary);无效 HTML -> []。
2. `TestSourceTierDigitimes`:`"DIGITIMES·涨价追踪"` 源 `_keep` 直通 / tier==1。
3. `TestFetchAllSources` 更新:金十**不再**被调,digitimes + x 被调。
4. 前端人工(node --check + 浏览器看 DIGITIMES 栏、个股栏消失)。
