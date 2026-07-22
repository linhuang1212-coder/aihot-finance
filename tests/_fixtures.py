# -*- coding: utf-8 -*-
"""测试共享样本条目。"""

SAMPLE_ITEMS = [
    {
        "id": "a1", "title_zh": "重磅！英伟达联手SK海力士",
        "summary_zh": "英伟达联手SK海力士，利好HBM与AI芯片产业链",
        "body_excerpt": "英伟达与SK海力士签署下一代AI内存多年合作",
        "source": "电子工程专辑", "source_url": "http://x/1",
        "published_at": "2026-06-08T15:04:00+08:00", "score": 9.0,
        "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}],
    },
    {
        "id": "b2", "title_zh": "某基金增持英伟达3908股",
        "summary_zh": "机构小幅增持英伟达，对股价影响有限",
        "body_excerpt": "", "source": "MarketBeat", "source_url": "http://x/2",
        "published_at": "2026-06-08T15:36:00+08:00", "score": 3.0,
        "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}],
    },
    {
        "id": "c3", "title_zh": "白银上涨", "summary_zh": "贵金属走强",
        "body_excerpt": "", "source": "新浪财经", "source_url": "http://x/3",
        "published_at": "2026-06-08T14:00:00+08:00", "score": 5.0,
        "entities": [],
    },
]

SAMPLE_RSS = (
    b"<?xml version='1.0'?><rss><channel>"
    b"<item><title>\xe8\x8b\xb1\xe4\xbc\x9f\xe8\xbe\xbe\xe8\x81\x94\xe6\x89\x8bSK - "
    b"\xe7\x94\xb5\xe5\xad\x90\xe5\xb7\xa5\xe7\xa8\x8b\xe4\xb8\x93\xe8\xbe\x91</title>"
    b"<link>http://g/1</link></item>"
    b"<item><title>NV memory deal - Reuters</title><link>http://g/2</link></item>"
    b"</channel></rss>"
)
