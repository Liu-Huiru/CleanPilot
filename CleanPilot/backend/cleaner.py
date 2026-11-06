#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理执行：移入隔离区（回收站）、恢复与过期清理。"""
import json
import os
import secrets
import shutil
import time
import traceback

from .database import audit, db
from shared.constants import QUARANTINE_DIRNAME
from shared.utils import fmt_bytes, inside


def run_execute(task_id, device, items, user, retention_days):
    conn = db()
    root = device["root_path"]
    qroot = os.path.join(root, QUARANTINE_DIRNAME, "quarantine")
    detail = []
    moved = failed = released = 0
    try:
        for idx, it in enumerate(items):
            ok, reason = False, ""
            try:
                if not os.path.exists(it["path"]):
                    reason = "文件已不存在"
                elif not inside(root, it["path"]):
                    reason = "路径不在设备范围内，已拒绝"
                else:
                    st = os.stat(it["path"])
                    if st.st_size != it["size"]:
                        reason = "文件已变化，跳过"
                    else:
                        eid = secrets.token_hex(8)
                        qdir = os.path.join(qroot, eid)
                        os.makedirs(qdir, exist_ok=True)
                        qpath = os.path.join(qdir, it["name"] or "file")
                        shutil.move(it["path"], qpath)
                        now = time.time()
                        conn.execute(
                            """INSERT INTO recycle(device_id,task_id,name,orig_path,q_path,size,deleted_at,expires_at,state)
                               VALUES(?,?,?,?,?,?,?,?, 'held')""",
                            (device["id"], task_id, it["name"], it["path"], qpath, it["size"], now,
                             now + retention_days * 86400))
                        conn.execute("UPDATE items SET status='moved' WHERE id=?", (it["id"],))
                        moved += 1
                        released += it["size"]
                        ok = True
            except Exception as e:
                reason = "移动失败：%s" % e
            if not ok:
                failed += 1
                conn.execute("UPDATE items SET status='failed' WHERE id=?", (it["id"],))
            detail.append({"name": it["name"], "size": it["size"], "ok": ok, "reason": reason})
            conn.execute("UPDATE tasks SET progress=?, moved_items=?, failed_items=?, released_bytes=?, detail_json=? WHERE id=?",
                         (int((idx + 1) * 100 / max(1, len(items))), moved, failed, released,
                          json.dumps(detail, ensure_ascii=False), task_id))
            conn.commit()

        state = "completed" if failed == 0 else ("partial" if moved else "failed")
        conn.execute("UPDATE tasks SET state=?, finished_at=? WHERE id=?", (state, time.time(), task_id))
        audit(conn, user, "execute", device["id"],
              "%d 项 / %s" % (moved, fmt_bytes(released)), state)
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE tasks SET state='failed', finished_at=? WHERE id=?", (time.time(), task_id))
        conn.commit()
        traceback.print_exc()
    finally:
        conn.close()


def purge_expired(conn, device_id=None):
    now = time.time()
    q = "SELECT * FROM recycle WHERE state='held' AND expires_at<=?"
    args = [now]
    if device_id:
        q += " AND device_id=?"
        args.append(device_id)
    rows = conn.execute(q, args).fetchall()
    ids = []
    for r in rows:
        try:
            if os.path.exists(r["q_path"]):
                os.remove(r["q_path"])
            d = os.path.dirname(r["q_path"])
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
        ids.append(r["id"])
    if ids:
        conn.executemany("UPDATE recycle SET state='expired' WHERE id=?", [(i,) for i in ids])
        conn.commit()


def recycle_occupied(conn, device_id):
    row = conn.execute("SELECT COALESCE(SUM(size),0) s FROM recycle WHERE device_id=? AND state='held'",
                       (device_id,)).fetchone()
    return int(row["s"] or 0)
