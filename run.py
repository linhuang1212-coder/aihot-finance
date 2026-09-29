#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIHOT 守护进程（一键常驻）。
- 同时拉起 server.py 和 telegram_bot.py
- 子进程崩溃 -> 自动重启（带退避，防崩溃风暴）
- 日志落盘到 logs/
- 健康检查：服务不通 / 数据停更（VPN或信源异常）-> 自动发 Telegram 告警
运行：python run.py
开机自启：见 README「常驻」一节（Windows 任务计划程序）。

环境变量：
  PORT            服务端口，默认 8910（锁端口用 PORT+1）
  AIHOT_ALERTS    on/off，默认 on（关掉则只记日志不发TG，便于本地测试）
  REFRESH_SECONDS 与 server 一致，用于判断数据是否停更（默认 180）
"""

import os
import sys
import json
import time
import socket
import threading
import subprocess
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
PORT = os.environ.get("PORT", "8910")
ALERTS_ON = os.environ.get("AIHOT_ALERTS", "on").lower() != "off"
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))  # 5分钟(2026-07-20 降 twitterapi 轮询成本)
DATA_FILE = os.path.join(BASE, "data", "items.json")

CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PORT=PORT,
                 AIHOT_SERVER="http://localhost:%s" % PORT,
                 TRANSLATE_BACKEND="deepseek")   # Google 翻译老限流,改用 DeepSeek 直译
CHILDREN = {
    "server": [sys.executable, "-u", "server.py"],
    "bot": [sys.executable, "-u", "telegram_bot.py"],
}

_procs = {}
_restart_log = {k: [] for k in CHILDREN}   # 最近重启时间戳
_alert_state = {}                          # 告警去抖：name -> 上次是否异常
_stop = threading.Event()
_lock_sock = None                          # 单实例锁（持有到进程结束）


def acquire_singleton_lock():
    """单实例保护：绑定本地锁端口 PORT+1。已有守护在跑 -> 二次绑定失败 -> 返回 False。
    进程退出时由操作系统自动释放，不留陈旧锁文件。"""
    global _lock_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 不设 SO_REUSEADDR，确保二次绑定失败
    try:
        s.bind(("127.0.0.1", int(PORT) + 1))
    except OSError:
        s.close()
        return False
    s.listen(1)
    _lock_sock = s
    return True


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line, flush=True)
    try:
        with open(os.path.join(LOG_DIR, "run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tg_alert(text):
    """给 bot 订阅者发告警。VPN 断时本身发不出去（已在日志留痕）。"""
    log("ALERT: " + text)
    if not ALERTS_ON:
        return
    try:
        with open(os.path.join(BASE, "bot_config.json"), encoding="utf-8") as f:
            token = json.load(f).get("token")
        with open(os.path.join(BASE, "bot_state.json"), encoding="utf-8") as f:
            subs = json.load(f).get("subscribers", [])
    except Exception:
        return
    if not token or not subs:
        return
    import urllib.parse
    for chat in subs:
        try:
            data = urllib.parse.urlencode({
                "chat_id": chat, "text": "⚠️ AIHOT 系统告警\n" + text,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % token, data=data)
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log("alert send failed: %s" % e)


def start_child(name):
    logf = open(os.path.join(LOG_DIR, name + ".log"), "a", encoding="utf-8")
    logf.write("\n==== start %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    logf.flush()
    p = subprocess.Popen(CHILDREN[name], cwd=BASE, env=CHILD_ENV,
                         stdout=logf, stderr=subprocess.STDOUT)
    _procs[name] = (p, logf)
    log("started %s (pid=%d)" % (name, p.pid))


def supervise():
    while not _stop.is_set():
        for name in CHILDREN:
            p, logf = _procs.get(name, (None, None))
            if p is None:
                continue
            code = p.poll()
            if code is None:
                continue
            # 子进程退出
            now = time.time()
            _restart_log[name] = [t for t in _restart_log[name] if now - t < 120] + [now]
            recent = len(_restart_log[name])
            log("%s exited (code=%s), restarting (%d times/2min)" % (name, code, recent))
            try:
                logf.close()
            except Exception:
                pass
            if recent >= 5:                       # 崩溃风暴 -> 退避 + 告警
                tg_alert("%s 反复崩溃（2分钟内%d次），暂停30秒后重试。请查 logs/%s.log" %
                         (name, recent, name))
                time.sleep(30)
            else:
                tg_alert("%s 异常退出(code=%s)，已自动重启。" % (name, code))
                time.sleep(2)
            start_child(name)
        _stop.wait(3)


def health():
    """服务可达性 + 数据新鲜度。仅在状态翻转时告警，避免刷屏。"""
    grace = time.time() + 90       # 启动宽限
    while not _stop.is_set():
        _stop.wait(60)
        if time.time() < grace:
            continue
        # 1) server 是否可达
        up = False
        try:
            r = urllib.request.urlopen(
                "http://localhost:%s/api/public/categories" % PORT, timeout=5)
            up = (r.status == 200)
        except Exception:
            up = False
        was_bad = _alert_state.get("server_down", False)
        if not up and not was_bad:
            _alert_state["server_down"] = True
            tg_alert("数据服务无法访问（localhost:%s）。" % PORT)
        elif up and was_bad:
            _alert_state["server_down"] = False
            tg_alert("数据服务已恢复。")
        # 2) 数据是否停更（VPN/信源异常的早期信号）
        if up:
            try:
                age = time.time() - os.path.getmtime(DATA_FILE)
            except Exception:
                age = 0
            stale_limit = REFRESH_SECONDS * 3 + 120
            was_stale = _alert_state.get("stale", False)
            if age > stale_limit and not was_stale:
                _alert_state["stale"] = True
                tg_alert("数据已 %d 分钟未更新，可能是 VPN 断开或信源异常。" % int(age / 60))
            elif age <= stale_limit and was_stale:
                _alert_state["stale"] = False
                tg_alert("数据更新已恢复正常。")


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    if not acquire_singleton_lock():
        log("已有 run.py 守护进程在运行（锁端口 %d 被占用）。本次退出，避免端口冲突。" % (int(PORT) + 1))
        return
    log("==== AIHOT 守护进程启动 (alerts=%s, port=%s) ====" % (ALERTS_ON, PORT))
    for name in CHILDREN:
        start_child(name)
    threading.Thread(target=supervise, daemon=True).start()
    threading.Thread(target=health, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在关闭子进程…")
        _stop.set()
        for name, (p, logf) in _procs.items():
            try:
                p.terminate()
            except Exception:
                pass
        log("已退出。")


if __name__ == "__main__":
    main()
