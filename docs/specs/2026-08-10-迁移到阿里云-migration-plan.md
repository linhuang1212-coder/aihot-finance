# AIHOT 新闻后台 迁移到阿里云 — 方案(待评审,未动手)

> 2026-08-10。目的:把 `/news` 后台从"家里PC + SSH反向隧道"搬到云VPS直跑,消除隧道/家用PC依赖(反复 502 的根因)。全程只读调研得出,**尚未执行任何变更**。

## 结论:可行,但有硬约束(feasible-with-caveats)

网站本体能干净迁移:它**运行时真正用到的源全部可达**(DIGITIMES / 金十 / twitterapi.io(X) / DeepSeek / Finnhub),程序又是纯 stdlib+sqlite,云上 Python 3.10.11 实测能跑。**唯一被墙的两条出口**是致命/降级项 —— 见下。

## 关键发现:云是**大陆阿里云**(广东),出口被 GFW 限制

实测从云端出站可达性:

| 服务 | 用途 | 云端可达? |
|---|---|---|
| DIGITIMES / 金十 / twitterapi.io / DeepSeek / Finnhub | 主源 + 翻译替代 + 行情 | ✅ 可达 |
| **translate.googleapis.com** | X 英文推翻译 | ❌ **被墙** → 降级(可用 DeepSeek 顶替) |
| **api.telegram.org** | Telegram 推送 + 健康告警 | ❌ **被墙** → 云上彻底不能用(硬阻塞) |
| Google News RSS / DuckDuckGo | Serenity 深度分析的补充 | ⚠️ 很可能被墙 → 深度分析降级 |

> 云主机:Windows Server 2025,Python 3.10.11(stdlib 实测OK),**8910 端口空闲**,C盘**只剩 9.1GB**(共享机,吃紧)。nginx 只需改 2 行。

## 架构决策:按"能不能连通"拆,而不是图省事

- **云上只跑 `server.py`**(它自带 init_db + 300秒 refresh 循环,一个进程就是整个网站)。绑 `127.0.0.1:8910`,nginx `/news` 从隧道(18910)改指到它。
- **不搬** `run.py` / `watchdog.py` / `reverse_tunnel.py`:云上用"开机计划任务"(与 `\ERP-Backend-jd` 等同款)替代守护+看门狗;隧道正是要拆掉的东西。
- **Telegram bot 不上云**(被墙)——三选一(见"待你决定")。
- 加一个小补丁:`newsfetch.translate` 在 Google 失败时**回退用 DeepSeek 翻译**(env 开关,家里行为不变)。

## 迁移步骤(10步,全程可回滚;已并入对抗评审的安全加固)

1. **云端预检**:用 app 真实 headers 跑一遍各源,确认返 200(**金十裸请求返 502,必须用真实 x-app-id/x-version 头验证**;不行则 A股栏丢源,但不影响主站)。顺带探 Google News/DuckDuckGo,确认 Serenity 降级干净。
2. **拷贝 app** 到云上非 web 根目录(如 `C:\services\aihot-news`);**不拷** watchdog.py(含明文SSH密码)/reverse_tunnel.py。
3. **密钥安全转移**:`bot_config.json` 只走 SFTP/RDP(加密),绝不进 git;云端 ACL 锁到 Administrator。
4. **数据转移(关键安全点)**:`news.db`(46MB,35k条+mentions时间线=唯一不可重建的历史)——**先用 `sqlite3 .backup` 或 `VACUUM INTO` 做一致性快照再传**(切勿边写边拷,会拷到损坏/半截),云端 `pragma quick_check`+行数校验(~35k)**作为切换硬门槛**。附带 llm_cache/translations/x_state(省重复付费调用)。`items.json` 暂留作兜底,DB 校验过了再删。
5. **两个云端补丁**:(a) translate DeepSeek 回退;(b) `server.py` 绑定改 `HOST=127.0.0.1`(**在代码里改默认值**,不止 .bat),避免公网裸暴露 8910。
6. **前台手动验证**(流量仍在隧道上):跑一次 server.py,看 init_db 不重导、refresh 出数据、API 返回非空 + 传播曲线在。
7. **注册开机计划任务** `\AIHOT-News`(启动触发 + 失败重启,替代看门狗)。**另加一个外部存活探测**(计划任务定时 curl `/api/public/categories`,挂了重启)——因为计划任务只能救"进程退出",救不了"进程卡死"。
8. **切 nginx**:只改两处 `/news/` 的 `proxy_pass 18910→8910`(:80 default + :443 fblerp.com),保留末尾斜杠;`nginx -t` 后 `nginx -s reload`(平滑,**绝不 stop/start**,别碰其它 vhost)。
9. **端到端验证 + 确认零波及**:访问 fblerp.com/news 走云端日志;回归检查 fblerp.com/ (Mac)、/design、任一 ERP 站点正常。
10. **退隧道 + 落定 Telegram**:9 绿了才在家里停 reverse_tunnel + 从看门狗摘掉隧道分支;**切换后立即停家里的抓取**(避免和云端双跑、双花 twitterapi 额度、x_state 分叉)。家里栈冷备 ~1 周再退役。

## 风险 & 回滚

- **回滚=秒级**:只有第8步碰用户流量。回滚就是把那两行 `proxy_pass` 改回 18910 + reload,流量瞬间回到家里(隧道保持开着到第10步)。
- **共享机磁盘**(9.1GB):`server.log` 要轮转、`llm_cache/translations` 要设上限、保持 RETENTION_DAYS=30、加磁盘告警——否则撑爆C盘会拖垮同机所有 ERP 站。
- **切换后云端丢了 Telegram 告警通道**:需配一个**大陆可达的告警出口**(企业微信/飞书/Server酱),否则云端悄悄挂了没人知道。
- **云 Administrator 明文密码**(watchdog.py 里)= 这台共享机的完全控制权:退隧道后**建议轮换该密码**;顺带轮换 deepseek/finnhub/twitterapi key。

## 工作量

约**半天动手 + 1周观察**。Telegram 选项影响工期:退役=0;家里留 bot 指向云API≈30分;换飞书/企业微信≈半天新代码。

## 待你决定(切换前必须定)

1. **Telegram 怎么办(硬阻塞)**:① 退役;② 留在家里跑、指向云端API继续推;③ 换成大陆可达的推送(飞书/企业微信/Server酱)。
2. **X 英文推翻译**:加 DeepSeek 回退(小额DeepSeek成本,推荐)/ 就显示英文标题。
3. Serenity 深度分析还用吗?(用的话第1步要确认它降级干净)
4. 云端 app 目录 `C:\services\aihot-news` + 只绑 127.0.0.1 可以吗?
5. 切换成功后家里 PC 冷备一周还是直接退役?
6. 要不要顺便配 `\AIHOT-DBBackup` 计划任务 + C盘磁盘告警?
