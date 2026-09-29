# -*- coding: utf-8 -*-
"""付费外部服务(twitterapi.io / DeepSeek)的调用健康记录 + 断供告警判定。进程内、零依赖。

为什么:日志里 twitterapi 和 DeepSeek 的 402(余额用完)累计断了一周多,一条告警都没有——
run.py 的健康检查只看 items.json 的修改时间,而 refresh 不管抓没抓到都会重写它。

判定按「上次成功以来的连续失败」:
- 402/401/403(欠费、key 失效)连续 >= PAY_FAIL_MIN 次 -> 告警(402 明确提示去充值);
- 其它错误(超时、429、5xx、400)要连续 >= OTHER_FAIL_MIN 次才告警——网络抖动、单条内容
  被拒这类中间夹着成功的零星失败不算「断供」,不来回刷屏;
- 告警后出现一次成功 -> 发恢复通知。没调用就没有新事件,不会凭空判恢复。
evaluate() 只给出待发消息;调用方确认送达后再 mark(),发不出去(断网/VPN 没起)下一轮会重发。

只有 server.main() 调了 enable() 才记录,测试/脚本里导入不会产生任何副作用。
"""
import threading
import time

PAY_CODES = {401, 402, 403}
PAY_FAIL_MIN = 3
OTHER_FAIL_MIN = 6
LABELS = {"twitterapi": "twitterapi.io(X 信源)", "deepseek": "DeepSeek(摘要/情绪/翻译)"}

_enabled = False
_lock = threading.Lock()
_streak = {}           # service -> [(ts, code)] 上次成功以来的连续失败
_seen_ok = {}          # service -> 是否成功过(恢复通知的前提)
_alerted = {}          # service -> bool(已送达的告警状态)


def enable():
    global _enabled
    _enabled = True


def _code(exc):
    c = getattr(exc, "code", None)
    return c if isinstance(c, int) else None


def note_ok(service, now=None):
    if not _enabled:
        return
    with _lock:
        _streak[service] = []
        _seen_ok[service] = True


def note_error(service, exc, now=None):
    if not _enabled:
        return
    now = now or time.time()
    with _lock:
        s = _streak.setdefault(service, [])
        s.append((now, _code(exc)))
        if len(s) > 200:
            del s[:-200]


def _message(service, streak):
    label = LABELS.get(service, service)
    codes = [c for _, c in streak if c]
    mins = max(1, int((streak[-1][0] - streak[0][0]) / 60))
    if 402 in codes:
        return ("%s 已连续失败 %d 次(约 %d 分钟,HTTP 402 Payment Required),多半是余额用完了,"
                "请去充值。充值后会自动恢复。" % (label, len(streak), mins))
    auth = [c for c in codes if c in (401, 403)]
    if auth:
        return "%s 已连续失败 %d 次(HTTP %s),可能是 key 失效或被拒,请检查。" % (
            label, len(streak), auth[-1])
    last = ("HTTP %s" % codes[-1]) if codes else "网络/超时"
    return "%s 已连续失败 %d 次(约 %d 分钟,最近一次:%s),数据可能停更。" % (
        label, len(streak), mins, last)


def evaluate(now=None):
    """返回待发送的 [(service, 新告警状态, 消息)],只在状态需要翻转时给出(不刷屏)。
    调用方送达后调 mark(service, 新状态)。"""
    if not _enabled:
        return []
    out = []
    with _lock:
        for service in set(_streak) | set(_alerted):
            streak = _streak.get(service) or []
            codes = {c for _, c in streak}
            need = PAY_FAIL_MIN if codes & PAY_CODES else OTHER_FAIL_MIN
            if len(streak) >= need and not _alerted.get(service):
                out.append((service, True, _message(service, streak)))
            elif _alerted.get(service) and not streak and _seen_ok.get(service):
                out.append((service, False, "%s 已恢复正常。" % LABELS.get(service, service)))
    return out


def mark(service, alerted):
    with _lock:
        _alerted[service] = alerted
