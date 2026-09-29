# -*- coding: utf-8 -*-
"""给 Telegram 订阅者发系统告警(server 进程用;run.py 仍用它自己的 tg_alert,两者格式一致)。
AIHOT_ALERTS=off 时只打日志不发送。返回是否送达(至少一个订阅者成功),不抛异常——
调用方据此决定要不要下一轮重发(断网/VPN 没起时告警不能就这么丢了)。"""
import json
import os
import time
import urllib.parse
import urllib.request

import atomicio

BASE = os.path.dirname(os.path.abspath(__file__))


def send_alert(text):
    print("[%s] [ALERT] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    if os.environ.get("AIHOT_ALERTS", "on").lower() == "off":
        return True                      # 主动关闭 = 当作已处理
    try:
        with open(os.path.join(BASE, "bot_config.json"), encoding="utf-8") as f:
            token = json.load(f).get("token")
    except Exception:
        return False
    state = atomicio.read_json(os.path.join(BASE, "bot_state.json"), {}, try_prev=True) or {}
    subs = state.get("subscribers", [])
    if not token or not subs:
        return False
    ok = False
    for chat in subs:
        try:
            data = urllib.parse.urlencode({
                "chat_id": chat, "text": "⚠️ AIHOT 系统告警\n" + text,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % token, data=data)
            urllib.request.urlopen(req, timeout=15)
            ok = True
        except Exception as e:
            print("[alert] send to %s failed: %s" % (chat, e))
    return ok
