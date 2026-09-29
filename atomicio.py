# -*- coding: utf-8 -*-
"""JSON 状态文件的原子读写(零依赖)。

为什么:items.json(48MB)、llm_cache、translations、x_state、bot_state 原来都是
open("w") 直接覆盖——写到一半断电/被杀(这台机器 60 天内 4 次非正常关机)就留下半截文件;
下次读失败又被当成空表,再保存就把全部内容覆盖掉。bot_state 读坏 = 订阅者清空、推送悄悄停。

写:先写同目录临时文件 -> fsync -> os.replace 原子替换。Windows 上目标文件正被别的进程
打开时 replace 会 PermissionError,重试一会儿;最终失败也只丢这一次保存,旧文件保持完整。
读:文件坏了就改名成 .corrupt-时间戳 留底,返回 default,绝不让下一次保存覆盖掉证据。
"""
import json
import os
import shutil
import time
from datetime import datetime

REPLACE_RETRIES = 10
REPLACE_WAIT = 0.3


def write_json(path, obj, keep_prev=False, **dump_kw):
    """原子写 JSON。keep_prev=True 时先把现有文件复制成 path.prev(小文件用,读坏时可回退)。
    成功返回 True;失败打印原因、返回 False,原文件不受影响。"""
    dump_kw.setdefault("ensure_ascii", False)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, **dump_kw)
            f.flush()
            os.fsync(f.fileno())
        if keep_prev and os.path.exists(path):
            try:
                shutil.copyfile(path, path + ".prev")
            except OSError:
                pass
        for i in range(REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if i == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT)
    except Exception as e:
        print("[atomicio] write %s failed: %s" % (os.path.basename(path), e))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _quarantine(path):
    dst = "%s.corrupt-%s" % (path, datetime.now().strftime("%Y%m%d%H%M%S"))
    try:
        os.replace(path, dst)
        print("[atomicio] %s 读取失败,已改名留底: %s" % (os.path.basename(path), os.path.basename(dst)))
    except OSError:
        pass


def read_json(path, default=None, try_prev=False):
    """读 JSON。不存在 -> default;内容坏了 -> 改名留底,再试 path.prev(try_prev),都不行 -> default。
    其它 IO 错误(如被占用)-> default,但不动文件。"""
    for i in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            break
        except ValueError:
            _quarantine(path)
            break
        except OSError as e:                  # 被占用等暂时性错误:稍等重试,仍不行再看 .prev(不动文件)
            if i == 2:
                print("[atomicio] read %s failed: %s" % (os.path.basename(path), e))
                break
            time.sleep(REPLACE_WAIT)
    if try_prev:
        try:
            with open(path + ".prev", encoding="utf-8") as f:
                obj = json.load(f)
            print("[atomicio] %s 用上一份备份 .prev 恢复" % os.path.basename(path))
            return obj
        except (OSError, ValueError):
            pass
    return default
