#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 接口层：路由分发、鉴权、静态文件服务。"""
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .ai_engine import ai_analyze_device, ai_chat
from .auth import check_pw, hash_pw, user_by_token
from .cleaner import purge_expired, recycle_occupied, run_execute
from .config import STATIC_DIR
from .database import audit, db
from .scanner import run_scan
from shared.constants import RISK_ORDER
from shared.utils import fmt_bytes, inside, mem_info, safe_int

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}


class Handler(BaseHTTPRequestHandler):
    server_version = "CleanPilot/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- 基础 ----------
    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def error(self, code, msg, extra=None):
        body = {"error": msg}
        if extra:
            body.update(extra)
        self.send_json(body, code)

    def body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2 * 1024 * 1024:
                return {}
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def current_user(self, conn):
        auth = self.headers.get("Authorization") or ""
        token = auth[7:] if auth.startswith("Bearer ") else ""
        return user_by_token(conn, token)

    def require(self, conn, admin=False):
        u = self.current_user(conn)
        if not u:
            self.error(401, "请先登录")
            return None
        if admin and u["role"] != "admin":
            self.error(403, "需要管理员权限")
            return None
        return u

    def device_of(self, conn, user, device_id):
        d = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not d:
            return None
        d = dict(d)
        if user["role"] != "admin" and d["owner_id"] != user["id"]:
            return None
        return d

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path.startswith("/api/"):
            conn = db()
            try:
                return self.api_get(conn, path, qs)
            except Exception as e:
                traceback.print_exc()
                return self.error(500, "服务异常：%s" % e)
            finally:
                conn.close()
        return self.serve_static(path)

    def api_get(self, conn, path, qs):
        if path == "/api/health":
            return self.send_json({"ok": True, "time": time.time()})

        if path == "/api/me":
            u = self.require(conn)
            if not u:
                return
            return self.send_json({"user": {"id": u["id"], "username": u["username"], "role": u["role"]}})

        if path == "/api/devices":
            u = self.require(conn)
            if not u:
                return
            rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if u["role"] != "admin" and d["owner_id"] != u["id"]:
                    continue
                try:
                    usage = shutil.disk_usage(d["root_path"])
                    total, free = usage.total, usage.free
                except Exception:
                    total, free = 0, 0
                out.append({
                    "id": d["id"], "name": d["name"], "root_path": d["root_path"],
                    "retention_days": d["retention_days"], "max_risk": d["max_risk"],
                    "protected_paths": json.loads(d["protected_paths"] or "[]"),
                    "last_scan_at": d["last_scan_at"],
                    "exists": os.path.isdir(d["root_path"]),
                    "total": total, "free": free, "used": max(0, total - free),
                    "recycle_bytes": recycle_occupied(conn, d["id"]),
                    "owner_id": d["owner_id"],
                })
            return self.send_json({"devices": out})

        if path == "/api/overview":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(qs.get("device_id", ["0"])[0])
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            try:
                usage = shutil.disk_usage(d["root_path"])
                total, free = usage.total, usage.free
            except Exception:
                total, free = 0, 0
            pct = round((total - free) * 100 / total, 1) if total else 0
            health = 92 if pct < 80 else (76 if pct < 90 else 58)
            last = conn.execute("SELECT * FROM scans WHERE device_id=? ORDER BY id DESC LIMIT 1",
                                (did,)).fetchone()
            items = {"count": 0, "bytes": 0}
            if last and last["status"] == "done":
                st = json.loads(last["stats_json"] or "{}")
                for k, v in st.items():
                    if k in ("R0", "R1"):
                        items["count"] += v.get("count", 0)
                        items["bytes"] += v.get("bytes", 0)
            recycle_count = conn.execute(
                "SELECT COUNT(*) c FROM recycle WHERE device_id=? AND state='held'", (did,)).fetchone()["c"]
            tasks = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(released_bytes),0) s FROM tasks WHERE device_id=? AND state IN ('completed','partial')",
                (did,)).fetchone()
            return self.send_json({
                "device": {"id": d["id"], "name": d["name"], "root_path": d["root_path"],
                           "retention_days": d["retention_days"], "max_risk": d["max_risk"],
                           "protected_paths": json.loads(d["protected_paths"] or "[]")},
                "storage": {"total": total, "free": free, "used": max(0, total - free), "percent": pct},
                "health": health,
                "cleanable": items,
                "recycle": {"count": recycle_count, "bytes": recycle_occupied(conn, did)},
                "last_scan": dict(last) if last else None,
                "lifetime": {"tasks": tasks["c"], "released": tasks["s"]},
                "system": mem_info(),
            })

        if path.startswith("/api/scan/"):
            u = self.require(conn)
            if not u:
                return
            parts = path.strip("/").split("/")
            sid = safe_int(parts[2])
            s = conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
            if not s:
                return self.error(404, "扫描任务不存在")
            s = dict(s)
            d = self.device_of(conn, u, s["device_id"])
            if not d:
                return self.error(403, "无权访问")
            if len(parts) >= 4 and parts[3] == "items":
                rows = conn.execute("SELECT * FROM items WHERE scan_id=? ORDER BY size DESC", (sid,)).fetchall()
                return self.send_json({"items": [dict(r) for r in rows]})
            stats = json.loads(s.pop("stats_json") or "{}")
            s["stats"] = stats
            s["device_name"] = d["name"]
            return self.send_json({"scan": s})

        if path.startswith("/api/task/"):
            u = self.require(conn)
            if not u:
                return
            tid = safe_int(path.strip("/").split("/")[-1])
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                return self.error(404, "任务不存在")
            t = dict(row)
            d = self.device_of(conn, u, t["device_id"])
            if not d:
                return self.error(403, "无权访问")
            t["detail"] = json.loads(t.pop("detail_json") or "[]")
            return self.send_json({"task": t})

        if path == "/api/recycle":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(qs.get("device_id", ["0"])[0])
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            purge_expired(conn, did)
            rows = conn.execute(
                "SELECT * FROM recycle WHERE device_id=? AND state='held' ORDER BY deleted_at DESC", (did,)).fetchall()
            out = []
            for r in rows:
                r = dict(r)
                r["days_left"] = max(0, int((r["expires_at"] - time.time()) / 86400) + 1)
                r["exists"] = os.path.exists(r["q_path"])
                out.append(r)
            return self.send_json({"items": out, "occupied": recycle_occupied(conn, did),
                                   "retention_days": d["retention_days"]})

        if path == "/api/reports":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(qs.get("device_id", ["0"])[0])
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            rows = conn.execute(
                "SELECT * FROM tasks WHERE device_id=? ORDER BY id DESC LIMIT 30", (did,)).fetchall()
            out = []
            for r in rows:
                r = dict(r)
                r["detail"] = json.loads(r.pop("detail_json") or "[]")
                out.append(r)
            return self.send_json({"tasks": out})

        if path == "/api/admin/summary":
            u = self.require(conn, admin=True)
            if not u:
                return
            dev = conn.execute("SELECT COUNT(*) c FROM devices").fetchone()["c"]
            tasks = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(released_bytes),0) s FROM tasks WHERE state IN ('completed','partial')").fetchone()
            scans = conn.execute("SELECT COUNT(*) c FROM scans WHERE status='done'").fetchone()["c"]
            recycle = conn.execute("SELECT COUNT(*) c FROM recycle WHERE state='held'").fetchone()["c"]
            return self.send_json({"devices": dev, "tasks": tasks["c"], "released": tasks["s"],
                                   "scans": scans, "recycle": recycle, "system": mem_info()})

        if path == "/api/admin/devices":
            u = self.require(conn, admin=True)
            if not u:
                return
            rows = conn.execute(
                """SELECT d.*, u.username owner FROM devices d LEFT JOIN users u ON u.id=d.owner_id ORDER BY d.id""").fetchall()
            out = []
            for r in rows:
                r = dict(r)
                try:
                    usage = shutil.disk_usage(r["root_path"])
                    r["total"], r["free"] = usage.total, usage.free
                except Exception:
                    r["total"] = r["free"] = 0
                r["protected_paths"] = json.loads(r["protected_paths"] or "[]")
                r["exists"] = os.path.isdir(r["root_path"])
                r["recycle_bytes"] = recycle_occupied(conn, r["id"])
                r["task_count"] = conn.execute("SELECT COUNT(*) c FROM tasks WHERE device_id=?", (r["id"],)).fetchone()["c"]
                out.append(r)
            return self.send_json({"devices": out})

        if path == "/api/admin/audit":
            u = self.require(conn, admin=True)
            if not u:
                return
            rows = conn.execute("SELECT * FROM audits ORDER BY id DESC LIMIT 100").fetchall()
            return self.send_json({"audits": [dict(r) for r in rows]})

        if path == "/api/ai/analyze":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(qs.get("device_id", ["0"])[0])
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            return self.send_json(ai_analyze_device(conn, d))

        return self.error(404, "接口不存在")

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self.error(404, "not found")
        conn = db()
        try:
            return self.api_post(conn, path)
        except Exception as e:
            traceback.print_exc()
            return self.error(500, "服务异常：%s" % e)
        finally:
            conn.close()

    def api_post(self, conn, path):
        body = self.body_json()

        if path == "/api/register":
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            if len(username) < 2 or len(password) < 6:
                return self.error(400, "用户名至少 2 位，密码至少 6 位")
            if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                return self.error(409, "用户名已存在")
            conn.execute("INSERT INTO users(username,password,role,created_at) VALUES(?,?,'user',?)",
                         (username, hash_pw(password), time.time()))
            conn.commit()
            uid = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, uid, time.time()))
            conn.commit()
            audit(conn, {"id": uid, "username": username}, "register")
            conn.commit()
            return self.send_json({"token": token, "user": {"id": uid, "username": username, "role": "user"}})

        if path == "/api/login":
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not check_pw(password, row["password"]):
                return self.error(401, "用户名或密码不正确")
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                         (token, row["id"], time.time()))
            conn.commit()
            return self.send_json({"token": token, "user": {"id": row["id"], "username": row["username"], "role": row["role"]}})

        if path == "/api/logout":
            auth = self.headers.get("Authorization") or ""
            token = auth[7:] if auth.startswith("Bearer ") else ""
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
            return self.send_json({"ok": True})

        if path == "/api/devices":
            u = self.require(conn)
            if not u:
                return
            name = (body.get("name") or "").strip()
            root = (body.get("root_path") or "").strip()
            if not name or not root:
                return self.error(400, "请填写设备名称与存储路径")
            root = os.path.abspath(os.path.expanduser(root))
            if not os.path.isdir(root):
                return self.error(400, "路径不存在或不是文件夹：%s" % root)
            conn.execute(
                "INSERT INTO devices(name,root_path,owner_id,created_at) VALUES(?,?,?,?)",
                (name, root, u["id"], time.time()))
            conn.commit()
            did = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            audit(conn, u, "device.add", did, root)
            conn.commit()
            return self.send_json({"id": did})

        m = re.match(r"^/api/devices/(\d+)/policy$", path)
        if m:
            u = self.require(conn)
            if not u:
                return
            did = int(m.group(1))
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            paths = body.get("protected_paths")
            if paths is None:
                return self.error(400, "缺少 protected_paths")
            if not isinstance(paths, list):
                return self.error(400, "保护路径必须是数组")
            clean = [str(p).strip() for p in paths if str(p).strip()][:50]
            retention = max(7, min(90, safe_int(body.get("retention_days"), d["retention_days"])))
            max_risk = body.get("max_risk") or d["max_risk"]
            if max_risk not in ("R1", "R2"):
                max_risk = "R2"
            if u["role"] != "admin":
                retention, max_risk = d["retention_days"], d["max_risk"]
            conn.execute("UPDATE devices SET protected_paths=?, retention_days=?, max_risk=? WHERE id=?",
                         (json.dumps(clean, ensure_ascii=False), retention, max_risk, did))
            audit(conn, u, "policy.update", did, "%d 条保护路径 / 保留 %d 天 / 上限 %s" % (len(clean), retention, max_risk))
            conn.commit()
            return self.send_json({"ok": True})

        if path == "/api/scan":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(body.get("device_id"))
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            if not os.path.isdir(d["root_path"]):
                return self.error(400, "设备路径不可访问，请检查磁盘是否已连接")
            running = conn.execute("SELECT id FROM scans WHERE device_id=? AND status='running'", (did,)).fetchone()
            if running:
                return self.send_json({"scan_id": running["id"], "reused": True})
            conn.execute("INSERT INTO scans(device_id,status,started_at) VALUES(?,'running',?)", (did, time.time()))
            conn.commit()
            sid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            audit(conn, u, "scan.start", did, "全盘扫描")
            conn.commit()
            threading.Thread(target=run_scan, args=(sid, d), daemon=True).start()
            return self.send_json({"scan_id": sid})

        if path == "/api/plan":
            u = self.require(conn)
            if not u:
                return
            sid = safe_int(body.get("scan_id"))
            ids = body.get("item_ids") or []
            if not isinstance(ids, list) or not ids:
                return self.error(400, "请至少选择一项")
            s = conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
            if not s:
                return self.error(404, "扫描任务不存在")
            d = self.device_of(conn, u, s["device_id"])
            if not d:
                return self.error(403, "无权访问")
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute("SELECT * FROM items WHERE scan_id=? AND id IN (%s)" % placeholders,
                                [sid] + [safe_int(i) for i in ids]).fetchall()
            if not rows:
                return self.error(400, "所选条目已失效，请重新扫描")
            if any(r["risk"] == "R3" for r in rows):
                return self.error(400, "包含受保护内容，系统不允许清理")
            max_rank = RISK_ORDER.get(d["max_risk"], 2)
            blocked = [r for r in rows if RISK_ORDER[r["risk"]] > max_rank]
            if blocked:
                return self.error(400, "策略限制：当前设备最多允许清理到 %s" % d["max_risk"],
                                  {"blocked": len(blocked)})
            total = sum(r["size"] for r in rows)
            now = time.time()
            conn.execute(
                "INSERT INTO plans(device_id,scan_id,item_ids,total_items,total_bytes,state,created_at,expires_at) VALUES(?,?,?,?,?, 'draft',?,?)",
                (d["id"], sid, json.dumps([r["id"] for r in rows]), len(rows), total, now, now + 86400))
            pid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            by_risk = {}
            for r in rows:
                b = by_risk.setdefault(r["risk"], {"count": 0, "bytes": 0})
                b["count"] += 1
                b["bytes"] += r["size"]
            audit(conn, u, "plan.create", d["id"], "%d 项 / %s" % (len(rows), fmt_bytes(total)))
            conn.commit()
            return self.send_json({"plan_id": pid, "total_items": len(rows), "total_bytes": total,
                                   "by_risk": by_risk, "expires_at": now + 86400,
                                   "max_risk": d["max_risk"], "retention_days": d["retention_days"]})

        if path == "/api/execute":
            u = self.require(conn)
            if not u:
                return
            pid = safe_int(body.get("plan_id"))
            ack = bool(body.get("acknowledge_r2"))
            p = conn.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
            if not p:
                return self.error(404, "清理方案不存在")
            p = dict(p)
            d = self.device_of(conn, u, p["device_id"])
            if not d:
                return self.error(403, "无权访问")
            if p["state"] != "draft":
                return self.error(409, "该方案已执行或已失效")
            if time.time() > p["expires_at"]:
                return self.error(409, "方案已过期，请重新扫描")
            ids = json.loads(p["item_ids"])
            rows = conn.execute("SELECT * FROM items WHERE id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()
            rows = [dict(r) for r in rows]
            if any(r["risk"] == "R2" for r in rows) and not ack:
                return self.error(400, "包含需谨慎内容，需确认后才能执行", {"code": "R2_UNCONFIRMED"})
            conn.execute("INSERT INTO tasks(plan_id,device_id,user_id,state,started_at) VALUES(?,?,?, 'running', ?)",
                         (pid, d["id"], u["id"], time.time()))
            tid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            conn.execute("UPDATE plans SET state='executing' WHERE id=?", (pid,))
            conn.commit()
            threading.Thread(target=run_execute,
                             args=(tid, d, rows, u, d["retention_days"]), daemon=True).start()
            return self.send_json({"task_id": tid})

        m = re.match(r"^/api/recycle/(\d+)/restore$", path)
        if m:
            u = self.require(conn)
            if not u:
                return
            rid = int(m.group(1))
            r = conn.execute("SELECT * FROM recycle WHERE id=?", (rid,)).fetchone()
            if not r:
                return self.error(404, "回收站条目不存在")
            r = dict(r)
            d = self.device_of(conn, u, r["device_id"])
            if not d:
                return self.error(403, "无权访问")
            if r["state"] != "held":
                return self.error(409, "该条目已恢复或已清除")
            if not os.path.exists(r["q_path"]):
                return self.error(409, "备份文件已丢失，无法恢复")
            target = r["orig_path"]
            if not inside(d["root_path"], target):
                return self.error(400, "原路径不在设备范围内")
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    base, ext = os.path.splitext(target)
                    n = 1
                    while os.path.exists("%s (%d)%s" % (base, n, ext)):
                        n += 1
                    target = "%s (%d)%s" % (base, n, ext)
                shutil.move(r["q_path"], target)
                qdir = os.path.dirname(r["q_path"])
                if os.path.isdir(qdir) and not os.listdir(qdir):
                    os.rmdir(qdir)
            except Exception as e:
                return self.error(500, "恢复失败：%s" % e)
            conn.execute("UPDATE recycle SET state='restored' WHERE id=?", (rid,))
            audit(conn, u, "recycle.restore", d["id"], os.path.basename(target))
            conn.commit()
            return self.send_json({"ok": True, "path": target})

        m = re.match(r"^/api/recycle/(\d+)/delete$", path)
        if m:
            u = self.require(conn)
            if not u:
                return
            rid = int(m.group(1))
            r = conn.execute("SELECT * FROM recycle WHERE id=?", (rid,)).fetchone()
            if not r:
                return self.error(404, "回收站条目不存在")
            r = dict(r)
            d = self.device_of(conn, u, r["device_id"])
            if not d:
                return self.error(403, "无权访问")
            try:
                if os.path.exists(r["q_path"]):
                    os.remove(r["q_path"])
                qdir = os.path.dirname(r["q_path"])
                if os.path.isdir(qdir) and not os.listdir(qdir):
                    os.rmdir(qdir)
            except OSError:
                pass
            conn.execute("UPDATE recycle SET state='deleted' WHERE id=?", (rid,))
            audit(conn, u, "recycle.delete", d["id"], r["name"])
            conn.commit()
            return self.send_json({"ok": True})

        if path == "/api/recycle/empty":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(body.get("device_id"))
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            rows = conn.execute("SELECT * FROM recycle WHERE device_id=? AND state='held'", (did,)).fetchall()
            freed = 0
            for r in rows:
                try:
                    if os.path.exists(r["q_path"]):
                        os.remove(r["q_path"])
                    qdir = os.path.dirname(r["q_path"])
                    if os.path.isdir(qdir) and not os.listdir(qdir):
                        os.rmdir(qdir)
                    freed += r["size"]
                except OSError:
                    pass
            conn.execute("UPDATE recycle SET state='deleted' WHERE device_id=? AND state='held'", (did,))
            audit(conn, u, "recycle.empty", did, "%d 项 / %s" % (len(rows), fmt_bytes(freed)))
            conn.commit()
            return self.send_json({"ok": True, "freed": freed})

        if path == "/api/devices/remove":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(body.get("device_id"))
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            conn.execute("DELETE FROM devices WHERE id=?", (did,))
            audit(conn, u, "device.remove", did, d["name"])
            conn.commit()
            return self.send_json({"ok": True})

        if path == "/api/ai/chat":
            u = self.require(conn)
            if not u:
                return
            did = safe_int(body.get("device_id"))
            d = self.device_of(conn, u, did)
            if not d:
                return self.error(404, "设备不存在")
            message = body.get("message") or ""
            if not message.strip():
                return self.error(400, "请输入消息")
            result = ai_chat(conn, u, d, message)
            audit(conn, u, "ai.chat", did, message[:80])
            conn.commit()
            return self.send_json(result)

        return self.error(404, "接口不存在")

    # ---------- DELETE ----------
    def do_DELETE(self):
        conn = db()
        try:
            u = self.require(conn)
            if not u:
                return
            m = re.match(r"^/api/recycle/(\d+)$", self.path)
            if m:
                return self.api_post(conn, "/api/recycle/%s/delete" % m.group(1))
            return self.error(404, "接口不存在")
        finally:
            conn.close()

    # ---------- 静态文件 ----------
    def serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not inside(STATIC_DIR, full) or not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "index.html")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)
