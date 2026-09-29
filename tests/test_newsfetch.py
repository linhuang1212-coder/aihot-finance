# -*- coding: utf-8 -*-
import os
import unittest
from unittest import mock

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class TestHeatTier(unittest.TestCase):
    def test_dup_count_maps_to_tiers(self):
        import newsfetch as nf
        self.assertEqual(nf._heat_tier(6, ""), "沸")
        self.assertEqual(nf._heat_tier(4, ""), "爆")
        self.assertEqual(nf._heat_tier(3, ""), "火")
        self.assertEqual(nf._heat_tier(2, ""), "热")

    def test_falls_back_to_source_flag_when_single_source(self):
        import newsfetch as nf
        self.assertEqual(nf._heat_tier(1, "热"), "热")  # 单源但金十标了 important
        self.assertEqual(nf._heat_tier(1, ""), "")     # 单源无标记 -> 无热度


class TestFetchJin10(unittest.TestCase):
    def test_parses_skips_locked_maps_heat(self):
        import newsfetch
        with mock.patch.object(newsfetch, "_http_get", return_value=_fix("jin10.json")):
            items = newsfetch.fetch_jin10()
        self.assertEqual(len(items), 3)  # VIP 锁定项被跳过（4 → 3）
        self.assertTrue(all(it["source"] == "金十" for it in items))
        titles = [it["title_zh"] for it in items]
        self.assertIn("深圳华强：存储、电源管理芯片、模拟芯片和MLCC在内的众多元器件出现缺货涨价情况", titles)
        self.assertIn("诺唯赞：控股股东拟1.15亿元至2.25亿元增持公司股份", titles)
        # important=1 的那条（伊拉克领空）heat="热"
        iraq = [it for it in items if "伊拉克" in it["title_zh"]][0]
        self.assertEqual(iraq["heat"], "热")
        # important=0 的条 heat 空
        hq = [it for it in items if it["title_zh"].startswith("深圳华强")][0]
        self.assertEqual(hq["heat"], "")
        self.assertTrue(items[0]["id"].startswith("jin10"))
        self.assertEqual(items[0]["lang"], "zh")
        self.assertEqual(items[0]["verified"], "confirmed")

    def test_extracts_stock_entities_from_remark(self):
        import newsfetch
        with mock.patch.object(newsfetch, "_http_get", return_value=_fix("jin10.json")):
            items = newsfetch.fetch_jin10()
        ents = [e for it in items for e in it["entities"]]
        hq = [e for e in ents if e["name"] == "深圳华强"]
        self.assertTrue(hq)                       # 从 remark 的 symbol 抽出个股
        self.assertEqual(hq[0]["code"], "000062")
        self.assertEqual(hq[0]["market"], "a-share")


class TestFetchEmKuaixun(unittest.TestCase):
    def test_strips_jsonp_and_parses(self):
        import newsfetch
        with mock.patch.object(newsfetch, "_http_get", return_value=_fix("em_kuaixun.txt")):
            items = newsfetch.fetch_em_kuaixun()
        self.assertEqual(len(items), 3)
        self.assertTrue(all(it["source"] == "东方财富" for it in items))
        titles = [it["title_zh"] for it in items]
        self.assertIn("黄仁勋：英伟达与SK海力士、SK电讯的合作未来可为韩国带来数千亿美元的业务规模", titles)
        first = [it for it in items if it["title_zh"].startswith("日内原油")][0]
        self.assertEqual(first["source_url"], "http://finance.eastmoney.com/a/202606083763800428.html")
        self.assertTrue(first["published_at"].startswith("2026-06-08T19:42:00"))
        self.assertTrue(items[0]["id"].startswith("emkx"))


class TestFetchAllSources(unittest.TestCase):
    """信源策略(2026-06-26 pivot)：主流 = DIGITIMES + X(KOL)；金十仍抓但只喂个股栏。"""

    def test_digitimes_x_jin10_wired_rest_dropped(self):
        import newsfetch as nf
        import xfetch
        called = []
        patches = [
            mock.patch.object(nf, "fetch_wscn", side_effect=lambda *a, **k: called.append("wscn") or []),
            mock.patch.object(nf, "fetch_jin10", side_effect=lambda *a, **k: called.append("jin10") or []),
            mock.patch.object(nf, "fetch_digitimes", side_effect=lambda *a, **k: called.append("digitimes") or []),
            mock.patch.object(nf, "fetch_em_kuaixun", side_effect=lambda *a, **k: called.append("em") or []),
            mock.patch.object(nf, "fetch_rss", side_effect=lambda *a, **k: called.append("rss") or []),
            mock.patch.object(nf, "_save_trans"),                       # 别动真翻译缓存
            mock.patch.object(xfetch, "fetch_x", side_effect=lambda *a, **k: called.append("x") or []),
        ]
        for p in patches:
            p.start()
        try:
            nf.fetch_all()
        finally:
            for p in patches:
                p.stop()
        self.assertIn("digitimes", called)   # DIGITIMES 涨价大追踪：新主源
        self.assertIn("x", called)           # X(KOL)：保留
        self.assertIn("jin10", called)       # 金十：仍抓(只喂个股栏)
        self.assertNotIn("wscn", called)     # 华尔街见闻：移除
        self.assertNotIn("em", called)       # 东方财富：移除
        self.assertNotIn("rss", called)      # RSS：移除


class TestFetchDigitimes(unittest.TestCase):
    FIXTURE = """
<div class="thumbnail"><a href="/tech/dt/n/shwnws.asp?CnlID=1&cat=&id=759909&packageid=77820"><img src="a.jpg"></a></div>
<div class="col-md-8"><div class="info_desc" id="info_desc8"><div class="">
<p style="padding:0;font-weight: 600;"><a href="/tech/dt/n/shwnws.asp?CnlID=1&cat=&id=759909&packageid=77820">三星DRAM單價年增逾400%　通用型反彈成最大贏家</a></p>
<div style="color:#808080;margin-top:-10px;">2026/6/26</div>
<h7 class="hidden-xs" title="三星電子在南韓的主要半導體生產地，記憶體出口金額大幅成長。">x</h7>
</div></div></div>
<div class="thumbnail"><a href="/tech/dt/n/shwnws.asp?CnlID=1&id=760042&packageid=77820"><img src="b.jpg"></a></div>
<div class="col-md-8"><div class="info_desc"><div class="">
<p style="padding:0;font-weight: 600;"><a href="/tech/dt/n/shwnws.asp?CnlID=1&id=760042&packageid=77820">記憶體成本飆漲　蘋果選擇直接轉嫁消費者</a></p>
<div style="color:#808080;margin-top:-10px;">2026/6/25</div>
<h7 class="hidden-xs" title="蘋果無預警大幅調整Mac與iPad多款產品售價。">x</h7>
</div></div></div>
"""

    def test_parses_list(self):
        import newsfetch as nf
        items = nf._parse_digitimes(self.FIXTURE)
        self.assertEqual(len(items), 2)                      # 缩图锚不重复计数
        a = items[0]
        self.assertEqual(a["id"], "dt759909")
        self.assertIn("三星DRAM單價", a["title_zh"])
        self.assertTrue(a["published_at"].startswith("2026-06-26"))
        self.assertIn("記憶體", a["summary_zh"])
        self.assertEqual(a["source"], "DIGITIMES·涨价追踪")
        self.assertEqual(a["lang"], "zh")
        self.assertIn("759909", a["source_url"])
        self.assertEqual(items[1]["id"], "dt760042")

    def test_empty_html(self):
        import newsfetch as nf
        self.assertEqual(nf._parse_digitimes("<html>nothing</html>"), [])


class TestSourceTierDigitimes(unittest.TestCase):
    def test_digitimes_is_tier1_and_kept(self):
        import newsfetch as nf
        self.assertIn("DIGITIMES·涨价追踪", nf.SOURCE_TIER1)
        it = {"source": "DIGITIMES·涨价追踪", "title_zh": "記憶體漲價", "title_orig": "",
              "lang": "zh", "entities": [], "categories": ["sector"]}
        self.assertTrue(nf._keep(it))


class TestFetchThs(unittest.TestCase):
    def test_parses_ctime_and_heat(self):
        import newsfetch
        with mock.patch.object(newsfetch, "_http_get", return_value=_fix("ths.json")):
            items = newsfetch.fetch_ths()
        self.assertEqual(len(items), 3)
        self.assertTrue(all(it["source"] == "同花顺" for it in items))
        nlb = [it for it in items if it["title_zh"].startswith("6月8日")][0]
        self.assertEqual(nlb["source_url"], "https://news.10jqka.com.cn/20260608/c677301042.shtml")
        self.assertTrue(nlb["published_at"].startswith("2026-"))
        self.assertEqual(nlb["heat"], "")  # import=0
        hot = [it for it in items if "2026年第一季度" in it["title_zh"]][0]
        self.assertEqual(hot["heat"], "热")  # import=3（源自带标记，仍在 fetch_ths 设置）
        self.assertTrue(items[0]["id"].startswith("ths"))

    def test_extracts_stock_entities(self):
        import newsfetch
        with mock.patch.object(newsfetch, "_http_get", return_value=_fix("ths.json")):
            items = newsfetch.fetch_ths()
        ents = [e for it in items for e in it["entities"]]
        hq = [e for e in ents if e["name"] == "深圳华强"]
        self.assertTrue(hq)                       # 从 stock 字段抽出个股
        self.assertEqual(hq[0]["code"], "000062")
        self.assertEqual(hq[0]["market"], "a-share")


class TestClusterEntityMerge(unittest.TestCase):
    def test_entity_survives_dedup(self):
        import newsfetch as nf
        # 同一事件两源：新浪(摘要长,无实体) vs 同花顺(有个股实体)。去重后代表条目应保留实体。
        a = {"title_zh": "深圳华强存储芯片缺货涨价", "summary_zh": "详" * 50, "source": "新浪财经",
             "published_at": "2026-06-09T10:00:00+08:00", "entities": []}
        b = {"title_zh": "深圳华强存储芯片缺货涨价", "summary_zh": "短", "source": "同花顺",
             "published_at": "2026-06-09T10:01:00+08:00",
             "entities": [{"name": "深圳华强", "code": "000062", "market": "a-share"}]}
        reps = nf._cluster([a, b])
        self.assertEqual(len(reps), 1)                       # 同事件合并为一
        names = {e["name"] for e in reps[0].get("entities", [])}
        self.assertIn("深圳华强", names)                      # 个股实体并到代表条目，没丢


class TestKeepNoiseReduction(unittest.TestCase):
    def _en(self, **kw):
        base = {"source": "SomeObscureBlog", "lang": "en", "title_zh": "x", "title_orig": "x",
                "summary_zh": "", "body_excerpt": "", "categories": ["equity"],
                "markets": [], "entities": []}
        base.update(kw)
        return base

    def test_english_tail_without_entity_dropped(self):
        import newsfetch as nf
        self.assertFalse(nf._keep(self._en(entities=[])))          # 碎片噪音 -> 丢

    def test_english_tail_with_entity_kept(self):
        import newsfetch as nf
        self.assertTrue(nf._keep(self._en(entities=[{"name": "英伟达", "code": "NVDA"}])))

    def test_tier1_english_always_kept(self):
        import newsfetch as nf
        self.assertTrue(nf._keep(self._en(source="Reuters", entities=[])))

    def test_chinese_tier2_finance_relevant_kept(self):
        import newsfetch as nf
        it = {"source": "某中文财经站", "lang": "zh", "title_zh": "央行降息", "title_orig": "",
              "summary_zh": "货币政策", "body_excerpt": "", "categories": ["macro"],
              "markets": [], "entities": []}
        self.assertTrue(nf._keep(it))

    def test_blocked_source_always_dropped(self):
        import newsfetch as nf
        # 即便 tier1 + 有重点实体，被屏蔽源也丢（覆盖 Google News 转手的新浪）
        it = {"source": "新浪财经", "lang": "en", "title_zh": "英伟达", "title_orig": "Nvidia",
              "summary_zh": "", "body_excerpt": "", "categories": ["sector"],
              "markets": [], "entities": [{"name": "英伟达", "code": "NVDA"}]}
        self.assertFalse(nf._keep(it))

    def test_ths_source_blocked(self):
        import newsfetch as nf
        # 用户已下掉同花顺：即便有个股/分类，任何渠道一律丢
        it = {"source": "同花顺", "lang": "zh", "title_zh": "某A股涨停", "title_orig": "",
              "summary_zh": "", "body_excerpt": "", "categories": ["ashare"],
              "markets": ["a-share"], "entities": [{"name": "深圳华强", "code": "000062"}]}
        self.assertFalse(nf._keep(it))


class TestPriorityThemes(unittest.TestCase):
    """用户优先主线（涨价/半导体材料/新能源/数据中心/境外对华政策）的打分加权。"""

    def _item(self, source, title):
        return {"title_zh": title, "summary_zh": "", "body_excerpt": "",
                "title_orig": "", "source": source, "entities": [],
                "dup_count": 1, "heat": ""}

    def test_priority_keyword_boosts_score(self):
        import newsfetch as nf
        base = nf._score(self._item("某财经站", "公司发布季度业绩"))[0]
        hot = nf._score(self._item("某财经站", "存储芯片大幅涨价 供不应求"))[0]
        self.assertGreater(hot, base)            # 命中「涨价/存储芯片」主线 -> 分更高

    def test_weak_spot_becomes_selected(self):
        import newsfetch as nf
        # tier1 源 + 主线词 -> 越过精选门槛（补强弱项的核心目的）
        _, selected = nf._score(self._item("金十", "光棒价格涨价近550%"))
        self.assertTrue(selected)
        _, plain = nf._score(self._item("金十", "某公司召开股东大会"))
        self.assertFalse(plain)                  # 同源无主线词 -> base 4.0 < 5.0 不精选

    def test_theme_boost_capped(self):
        import newsfetch as nf
        stuffed = "涨价 缺货 紧缺 提价 HBM 光模块 储能 数据中心 出口管制 液冷 石英砂"
        score, _ = nf._score(self._item("某财经站", stuffed))
        self.assertLessEqual(score, 2.0 + nf.THEME_BOOST_CAP)  # 堆砌也不超封顶


class TestSentimentBoost(unittest.TestCase):
    """强方向(str=3 利多/利空)且命中自选标的 -> +1.0；其余不动分。"""

    def _item(self, sentiment=None, entities=None):
        return {"title_zh": "x", "summary_zh": "", "body_excerpt": "", "title_orig": "",
                "source": "某财经站", "dup_count": 1, "heat": "",
                "entities": entities or [], "sentiment": sentiment}

    def test_strong_directional_on_watchlist_boosts(self):
        import newsfetch as nf
        wl = nf.ENTITY_MAP[0][1]["name"]                    # 英伟达（自选）
        ents = [{"name": wl, "code": "NVDA"}]
        without = nf._score(self._item(entities=ents))[0]   # 有自选实体、无强方向
        withd = nf._score(self._item(
            sentiment={"dir": "利多", "tgt": "存储", "str": 3}, entities=ents))[0]
        self.assertEqual(round(withd - without, 2), 1.0)    # 纯情绪加分 = +1.0

    def test_weak_or_neutral_no_boost(self):
        import newsfetch as nf
        wl = nf.ENTITY_MAP[0][1]["name"]
        ents = [{"name": wl}]
        base = nf._score(self._item(entities=ents))[0]
        s2 = nf._score(self._item(sentiment={"dir": "利多", "str": 2, "tgt": "x"},
                                  entities=ents))[0]
        sn = nf._score(self._item(sentiment={"dir": "中性", "str": 0, "tgt": ""},
                                  entities=ents))[0]
        self.assertEqual(s2, base)                          # str=2 不够强
        self.assertEqual(sn, base)                          # 中性不加分

    def test_strong_directional_off_watchlist_no_boost(self):
        import newsfetch as nf
        off = [{"name": "某不在自选的股", "code": "000001"}]
        base = nf._score(self._item(entities=off))[0]
        s = nf._score(self._item(sentiment={"dir": "利空", "str": 3, "tgt": "x"},
                                 entities=off))[0]
        self.assertEqual(s, base)                           # 实体不在自选 -> 不加


class TestPriorityFeeds(unittest.TestCase):
    def test_gnews_cn_encodes_query(self):
        import newsfetch as nf
        url = nf._gnews_cn("涨价 OR 缺货")
        self.assertIn("news.google.com/rss/search", url)
        self.assertIn("hl=zh-CN", url)
        self.assertIn("when%3A2d", url)          # 时间窗已 URL 编码
        self.assertNotIn(" ", url)               # 空格已编码

    def test_priority_feeds_appended(self):
        import newsfetch as nf
        cn_search = [u for u, s in nf.RSS_FEEDS if "rss/search" in u and "zh-CN" in u]
        self.assertGreaterEqual(len(cn_search), 5)  # 5 条定向中文源已并入


class TestKeepXWhitelist(unittest.TestCase):
    """X 白名单源直通降噪;普通英文无实体条目仍被丢(回归)。"""

    def _en_item(self, source):
        return {"source": source, "lang": "en", "entities": [],
                "title_zh": "random text", "summary_zh": "", "body_excerpt": "",
                "title_orig": "some random non-finance text", "categories": [],
                "markets": [], "tags": []}

    def test_x_source_passes_keep(self):
        import newsfetch as nf
        self.assertTrue(nf._keep(self._en_item("X·Walter Bloomberg")))

    def test_plain_english_no_entity_still_dropped(self):
        import newsfetch as nf
        self.assertFalse(nf._keep(self._en_item("Some Random Blog")))


class TestChinaRelated(unittest.TestCase):
    def _it(self, **kw):
        it = {"title_zh": "", "summary_zh": "", "body_excerpt": "", "title_orig": "",
              "entities": [], "categories": [], "markets": []}
        it.update(kw)
        return it

    def test_keyword_hits(self):
        import newsfetch as nf
        for t in ["中国央行下调存款准备金率", "比亚迪二季度交付创新高", "人民币汇率走低",
                  "A股三大指数收涨", "华为发布新旗舰", "PBOC cuts RRR"]:
            self.assertTrue(nf._is_china_related(self._it(title_zh=t)), t)

    def test_entity_market_and_cat(self):
        import newsfetch as nf
        self.assertTrue(nf._is_china_related(
            self._it(entities=[{"name": "深圳华强", "market": "a-share"}])))
        self.assertTrue(nf._is_china_related(self._it(categories=["ashare"])))
        self.assertTrue(nf._is_china_related(self._it(markets=["a-share"])))
        self.assertTrue(nf._is_china_related(self._it(markets=["hk"])))

    def test_non_china_false(self):
        import newsfetch as nf
        for t in ["美联储宣布降息25个基点", "伊朗发射导弹", "OPEC决定增产"]:
            self.assertFalse(nf._is_china_related(self._it(title_zh=t)), t)


class TestTranslateFallback(unittest.TestCase):
    """云端 Google 翻译被墙时,TRANSLATE_BACKEND=deepseek 回退用 DeepSeek。"""

    def test_deepseek_primary_when_enabled(self):
        import newsfetch as nf
        import llm
        import os
        cache = {}
        with mock.patch.object(nf, "_load_trans", return_value=cache), \
             mock.patch.object(nf, "_http_get") as httpget, \
             mock.patch.object(llm, "translate", return_value="英伟达大涨"), \
             mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}):
            nf._trans_budget = 0
            out = nf.translate("Nvidia soars")
        self.assertEqual(out, "英伟达大涨")
        self.assertEqual(cache.get("Nvidia soars"), "英伟达大涨")   # 已缓存
        httpget.assert_not_called()   # 直接走 DeepSeek,不碰 Google

    def test_no_fallback_without_env(self):
        import newsfetch as nf
        import llm
        import os
        cache = {}
        with mock.patch.object(nf, "_load_trans", return_value=cache), \
             mock.patch.object(nf, "_http_get", side_effect=Exception("blocked")), \
             mock.patch.object(llm, "translate",
                               side_effect=AssertionError("不该调用")), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRANSLATE_BACKEND", None)
            nf._trans_budget = 0
            out = nf.translate("Nvidia soars")
        self.assertEqual(out, "Nvidia soars")   # 无 env -> 原样返回,不调 DeepSeek


class TestMainline(unittest.TestCase):
    def _it(self, **kw):
        it = {"title_zh": "", "summary_zh": "", "body_excerpt": "", "title_orig": "",
              "entities": [], "categories": [], "markets": []}
        it.update(kw)
        return it

    def test_theme_keywords_expanded(self):
        import newsfetch as nf
        for k in ["特高压", "西电东送", "国产替代", "可控核聚变",
                  "cowos", "产能", "扩产", "算力枢纽"]:
            self.assertIn(k, nf.THEME_KW, k)
        # 裸"核电"/"十五五" 太宽(战争核电站/全行业规划), 不应进表
        for k in ["核电", "十五五"]:
            self.assertNotIn(k, nf.THEME_KW, k)

    def test_chain_news_tagged_mainline(self):
        import newsfetch as nf
        for t in ["台积电N2先进工艺产能复合增速70%", "微软主机内存成本涨价超2.5倍",
                  "长鑫存储推进国产替代", "西电东送新增8000万千瓦",
                  "可控核聚变技术攻关", "HBM供不应求"]:
            it = self._it(title_zh=t)
            nf._tag_mainline(it)
            self.assertTrue(it["mainline"], t)

    def test_offchain_news_not_tagged(self):
        import newsfetch as nf
        # 体育/天气/纯油(风险罩, 非 AI 算力主线) 都不应进主线
        for t in ["某足球球星转会皇马", "某地天气晴转多云",
                  "中国成品油7月增加出口配额", "美联储维持利率不变",
                  "国际原子能机构:扎波罗热核电站关键电力线路修复工作仍在进行"]:
            it = self._it(title_zh=t)
            nf._tag_mainline(it)
            self.assertFalse(it["mainline"], t)


class TestJin10StockOnly(unittest.TestCase):
    """2026-06-27:金十回归但仅喂「个股(A股)栏」-> 一律 selected=False(不进主流/不推送)。"""

    def test_jin10_forced_unselected(self):
        import newsfetch as nf
        import llm
        import xfetch
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

        def mk(iid, source, title, ents=None):
            return {"id": iid, "source": source, "title_zh": title, "summary_zh": "",
                    "body_excerpt": "", "title_orig": "", "lang": "zh",
                    "channels": ["finance"], "categories": [], "markets": [], "tags": [],
                    "entities": ents or [], "heat": "", "read_count": None, "verified": "confirmed",
                    "published_at": now, "dedup_group": "g_" + iid, "score": 0.0}

        # 金十即便是中国/A股个股,也强制不选中(只在个股栏靠 mode=all 露出)
        jin10 = [mk("cn", "金十", "比亚迪二季度交付创新高",
                    [{"name": "比亚迪", "code": "002594", "market": "a-share"}]),
                 mk("us", "金十", "某国际商品价格小幅波动")]
        xitems = [mk("x", "X·Clash Report", "中国出口数据公布")]

        with mock.patch.object(nf, "fetch_digitimes", return_value=[]), \
             mock.patch.object(nf, "fetch_jin10", return_value=jin10), \
             mock.patch.object(nf, "fetch_wscn", return_value=[]), \
             mock.patch.object(nf, "fetch_em_kuaixun", return_value=[]), \
             mock.patch.object(nf, "fetch_rss", return_value=[]), \
             mock.patch.object(nf, "_save_trans"), \
             mock.patch.object(xfetch, "fetch_x", return_value=xitems), \
             mock.patch.object(llm, "enrich", return_value=0):
            out = nf.fetch_all()

        by = {it["id"]: it for it in out}
        self.assertFalse(by["cn"]["selected"])     # 金十(含A股个股) -> 强制不选中
        self.assertFalse(by["us"]["selected"])     # 金十 -> 强制不选中
        self.assertIn("x", by)                     # X 条目不受金十规则影响,正常进流

    def test_jin10_excluded_from_enrich(self):
        import newsfetch as nf
        import llm
        import xfetch
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

        def mk(iid, source, title):
            return {"id": iid, "source": source, "title_zh": title, "summary_zh": "",
                    "body_excerpt": "", "title_orig": "", "lang": "zh",
                    "channels": ["finance"], "categories": [], "markets": [], "tags": [],
                    "entities": [], "heat": "", "read_count": None, "verified": "confirmed",
                    "published_at": now, "dedup_group": "g_" + iid, "score": 0.0}

        seen = {}

        def fake_enrich(items, *a, **k):
            seen["sources"] = [it.get("source") for it in items]
            return 0

        with mock.patch.object(nf, "fetch_digitimes",
                               return_value=[mk("d", "DIGITIMES·涨价追踪", "記憶體漲價")]), \
             mock.patch.object(nf, "fetch_jin10", return_value=[mk("j", "金十", "比亚迪交付创新高")]), \
             mock.patch.object(nf, "fetch_wscn", return_value=[]), \
             mock.patch.object(nf, "fetch_em_kuaixun", return_value=[]), \
             mock.patch.object(nf, "fetch_rss", return_value=[]), \
             mock.patch.object(nf, "_save_trans"), \
             mock.patch.object(xfetch, "fetch_x", return_value=[]), \
             mock.patch.object(llm, "enrich", side_effect=fake_enrich):
            nf.fetch_all()

        self.assertNotIn("金十", seen["sources"])            # 金十不进增强
        self.assertIn("DIGITIMES·涨价追踪", seen["sources"])  # 其他源照常增强


class TestXSubject(unittest.TestCase):
    def test_colon_terminated(self):
        import newsfetch as nf
        self.assertEqual(
            nf._x_subject("Iran's Foreign Minister Abbas Araghchi:\nThe United States..."),
            "iran's foreign minister abbas araghchi")

    def test_strips_breaking_prefix(self):
        import newsfetch as nf
        self.assertEqual(nf._x_subject("BREAKING: Jordan Bardella:\nWe will not yield"),
                         "jordan bardella")

    def test_non_subject_format_none(self):
        import newsfetch as nf
        self.assertIsNone(nf._x_subject("*FED CUTS RATES BY 25 BPS"))
        self.assertIsNone(nf._x_subject("Trump: tariffs coming today"))   # 同行非主语行
        self.assertIsNone(nf._x_subject("Breaking news: markets fall"))    # 防误判
        self.assertIsNone(nf._x_subject(""))
        self.assertIsNone(nf._x_subject(None))


class TestMergeXBursts(unittest.TestCase):
    def _x(self, iid, label, orig, mins_ago=0):
        from datetime import datetime, timezone, timedelta
        dt = datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=mins_ago)
        return {"id": iid, "source": "X·" + label, "title_orig": orig,
                "title_zh": "译:" + orig[:40], "summary_zh": "",
                "published_at": dt.isoformat(timespec="seconds"),
                "source_url": "https://x.com/x/" + iid, "lang": "en",
                "verified": "confirmed", "channels": ["finance"]}

    def test_same_subject_merged(self):
        import newsfetch as nf
        grp = [self._x("a", "Clash Report", "Iran FM Araghchi:\nstmt one", 4),
               self._x("b", "Clash Report", "Iran FM Araghchi:\nstmt two", 2),
               self._x("c", "Clash Report", "BREAKING: Iran FM Araghchi:\nstmt three", 0)]
        out = nf._merge_x_bursts(grp)
        self.assertEqual(len(out), 1)
        m = out[0]
        self.assertEqual(len(m["segments"]), 2)             # 首条作 title,其余 2 段
        self.assertTrue(m["title_zh"])
        self.assertEqual(m["source"], "X·Clash Report")
        self.assertEqual(m["published_at"], grp[2]["published_at"])  # 最晚

    def test_different_subject_not_merged(self):
        import newsfetch as nf
        out = nf._merge_x_bursts([
            self._x("a", "Clash Report", "Iran FM Araghchi:\nx"),
            self._x("b", "Clash Report", "Jordan Bardella:\ny")])
        self.assertEqual(len(out), 2)

    def test_non_x_and_single_untouched(self):
        import newsfetch as nf
        jin = {"id": "j", "source": "金十", "title_orig": "央行:\n降准",
               "title_zh": "央行降准", "published_at": "2026-06-15T10:00:00+08:00"}
        out = nf._merge_x_bursts([jin, self._x("s", "Walter Bloomberg", "FM Smith:\nstmt")])
        self.assertEqual(len(out), 2)

    def test_no_subject_format_untouched(self):
        import newsfetch as nf
        out = nf._merge_x_bursts([self._x("a", "Walter Bloomberg", "*FED CUTS RATES"),
                                  self._x("b", "Walter Bloomberg", "*ECB HOLDS")])
        self.assertEqual(len(out), 2)
