#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库：连接、建表、审计日志与演示数据。"""
import os
import sqlite3
import time

from .auth import hash_pw
from .config import DATA_DIR, DB_PATH, ROOT_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  root_path TEXT NOT NULL,
  owner_id INTEGER NOT NULL,
  retention_days INTEGER NOT NULL DEFAULT 30,
  max_risk TEXT NOT NULL DEFAULT 'R2',
  protected_paths TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  last_scan_at REAL
);
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL DEFAULT 'walking',
  progress INTEGER NOT NULL DEFAULT 0,
  file_count INTEGER NOT NULL DEFAULT 0,
  total_bytes INTEGER NOT NULL DEFAULT 0,
  item_count INTEGER NOT NULL DEFAULT 0,
  stats_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  started_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  category TEXT NOT NULL,
  category_label TEXT NOT NULL,
  risk TEXT NOT NULL,
  confidence REAL NOT NULL,
  reason TEXT NOT NULL,
  default_selected INTEGER NOT NULL DEFAULT 0,
  group_key TEXT,
  status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE INDEX IF NOT EXISTS idx_items_scan ON items(scan_id);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  scan_id INTEGER NOT NULL,
  item_ids TEXT NOT NULL,
  total_items INTEGER NOT NULL,
  total_bytes INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'draft',
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL,
  device_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  moved_items INTEGER NOT NULL DEFAULT 0,
  failed_items INTEGER NOT NULL DEFAULT 0,
  released_bytes INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL DEFAULT '[]',
  started_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS recycle(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  task_id INTEGER,
  name TEXT NOT NULL,
  orig_path TEXT NOT NULL,
  q_path TEXT NOT NULL,
  size INTEGER NOT NULL,
  deleted_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'held'
);
CREATE INDEX IF NOT EXISTS idx_recycle_device ON recycle(device_id);
CREATE TABLE IF NOT EXISTS audits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor_id INTEGER,
  actor TEXT,
  action TEXT NOT NULL,
  device_id INTEGER,
  scope TEXT,
  result TEXT,
  policy_version TEXT
);
"""


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    conn.commit()
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        now = time.time()
        conn.execute("INSERT INTO users(username,password,role,created_at) VALUES(?,?,?,?)",
                     ("admin", hash_pw("admin123"), "admin", now))
        conn.execute("INSERT INTO users(username,password,role,created_at) VALUES(?,?,?,?)",
                     ("demo", hash_pw("user123"), "user", now))
        conn.commit()
    conn.close()


def audit(conn, user, action, device_id=None, scope="", result="ok", policy=""):
    conn.execute(
        "INSERT INTO audits(ts,actor_id,actor,action,device_id,scope,result,policy_version) VALUES(?,?,?,?,?,?,?,?)",
        (time.time(), (user or {}).get("id"), (user or {}).get("username", "system"),
         action, device_id, scope, result, policy),
    )


# ------------------------------------------------------------------ 演示数据
def make_demo(root):
    os.makedirs(root, exist_ok=True)
    dirs = ["DCIM/Camera", "Download", "Screenshots", "Android/data/com.demo/cache", "logs", "Work"]
    for d in dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)

    def write(rel, size, age_days, data=None):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            if data is not None:
                f.write(data)
            else:
                f.write(os.urandom(1024) * (size // 1024 + 1))
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
        return p

    photo = os.urandom(1024) * 512
    write("DCIM/Camera/IMG_2024_001.jpg", 0, 40, photo)
    write("DCIM/Camera/IMG_2024_001 (1).jpg", 0, 80, photo)
    write("Download/课程录屏_2023.mp4", 1024 * 1024 * 14, 420)
    write("Download/报销单.pdf", 1024 * 260, 12)
    write("Download/app-release.apk", 1024 * 1024 * 6, 95)
    write("Download/资料压缩包.zip", 1024 * 1024 * 9, 300)
    write("Screenshots/Screenshot_2025_10.png", 1024 * 900, 60)
    write("Android/data/com.demo/cache/cache_0001.bin", 1024 * 1024 * 8, 3)
    write("Android/data/com.demo/cache/cache_0002.bin", 1024 * 1024 * 5, 3)
    write("logs/app.log", 1024 * 1024 * 3, 2)
    write("logs/trace.log", 1024 * 700, 5)
    write("tmp/empty.tmp", 0, 0)
    write("Work/项目说明.docx", 1024 * 120, 8)
    return root


def seed_demo_device(conn):
    root = os.path.join(ROOT_DIR, "demo_storage")
    make_demo(root)
    row = conn.execute("SELECT id FROM users WHERE username='demo'").fetchone()
    if not row:
        return
    exists = conn.execute("SELECT id FROM devices WHERE root_path=?", (root,)).fetchone()
    if exists:
        return
    conn.execute("INSERT INTO devices(name,root_path,owner_id,created_at) VALUES(?,?,?,?)",
                 ("演示平板 · 128GB", root, row["id"], time.time()))
    conn.commit()
