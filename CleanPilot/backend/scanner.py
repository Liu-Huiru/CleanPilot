#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描引擎：文件遍历、风险分类与重复文件检测。"""
import hashlib
import json
import os
import stat as statmod
import time
import traceback

from .database import db
from shared.constants import HASH_BUDGET, MAX_FILES, QUARANTINE_DIRNAME, RISK_ORDER
from shared.utils import fmt_bytes, protected_prefixes

CACHE_EXT = {".tmp", ".temp", ".log", ".bak", ".cache", ".dmp", ".old", ".swp"}
INSTALLER_EXT = {".apk", ".xapk", ".ipa", ".msi", ".dmg", ".pkg", ".exe"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".m4v"}
CACHE_HINTS = ("/cache/", "/tmp/", "/temp/", ".thumbnails", "/log/", "/logs/", "/$recycle.bin/")
DOWNLOAD_HINTS = ("/download", "/下载")
SCREEN_HINTS = ("screenshot", "screen_shot", "截屏", "屏幕截图", "screencap")
# 已安装程序 / 游戏 / 系统目录：其中的文件一律视为程序本体，绝不清理
INSTALL_HINTS = ("/steamapps/", "/steamlibrary/", "/epic games/", "/gog games/", "/origin games/",
                 "/program files/", "/programdata/", "/windows/", "/system32/",
                 "/applications/", "/library/application support/", "/android/data/", "/android/obb/")


def classify(path, st, protected, dup_of=None):
    """返回 (risk, category, category_label, confidence, reason, group_key) 或 None"""
    low = path.lower().replace("\\", "/")
    ext = os.path.splitext(path)[1].lower()
    size = st.st_size
    age_days = max(0, (time.time() - st.st_mtime) / 86400)

    for pre in protected:
        if low.startswith(os.path.realpath(pre).lower().replace("\\", "/")):
            return ("R3", "protected", "受保护内容", 1.0,
                    "命中保护清单，系统不会清理该路径", None)

    in_download = any(h in low for h in DOWNLOAD_HINTS)
    is_media = ext in IMG_EXT or ext in VID_EXT

    # 已安装程序与游戏目录：属于程序本体，删除会导致无法运行
    if any(h in low for h in INSTALL_HINTS):
        return ("R3", "installed", "已安装程序", 0.98,
                "位于已安装程序或游戏目录，删除会导致程序无法运行，系统默认保护", None)

    # 用户媒体目录默认受保护（相册）
    if is_media and ("\\dcim\\" in low or "/dcim/" in low or "\\pictures\\" in low):
        return ("R3", "media", "个人媒体", 0.99,
                "位于相册目录，属于个人照片/视频，系统默认保护", None)

    if any(h in low for h in CACHE_HINTS) or ext in CACHE_EXT:
        return ("R0", "cache", "缓存与临时文件", 0.97,
                "应用缓存、日志或临时文件，删除后可由程序重新生成", None)

    if ext in INSTALLER_EXT:
        if age_days >= 30:
            return ("R0", "installer", "安装包", 0.95,
                    "安装包已下载 %.0f 天，安装完成后不再需要保留" % age_days, None)
        return ("R1", "installer", "安装包", 0.86,
                "近期下载的安装包，若已安装可删除", None)

    if size == 0:
        return ("R0", "empty", "空文件", 0.99, "文件大小为 0，无可用内容", None)

    if dup_of:
        return ("R1", "duplicate", "重复文件", 0.99,
                "与 %s 内容完全相同（SHA-256 一致），保留修改时间较新的一份即可" % dup_of, dup_of)

    if is_media and any(h in low for h in SCREEN_HINTS):
        return ("R1", "screenshot", "截图", 0.94,
                "屏幕截图，通常用于临时分享，%.0f 天未修改" % age_days, None)

    if in_download and age_days >= 180:
        return ("R1", "old_download", "长期未用下载", 0.92,
                "位于下载目录且已 %.0f 天未修改，可视为不再需要" % age_days, None)

    if size >= 500 * 1024 * 1024:
        return ("R2", "large", "大体积文件", 0.85,
                "单个文件超过 500 MB（%s），可能仍有用途，需你逐项确认" % fmt_bytes(size), None)

    if ext in ARCHIVE_EXT and age_days >= 180:
        return ("R2", "archive", "压缩包", 0.8,
                "压缩包可能包含个人资料，已 %.0f 天未修改，需你自行确认" % age_days, None)

    if ext in DOC_EXT and in_download:
        return ("R2", "doc", "文档", 0.79,
                "下载目录中的文档，可能仍有用途，需你自行确认", None)

    return None


def sha256_file(path, limit=HASH_BUDGET):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if read > limit:
                break
    return h.hexdigest()


def update_scan(conn, scan_id, **kw):
    keys = ", ".join("%s=?" % k for k in kw)
    conn.execute("UPDATE scans SET %s WHERE id=?" % keys, list(kw.values()) + [scan_id])
    conn.commit()


def run_scan(scan_id, device):
    conn = db()
    root = device["root_path"]
    protected = protected_prefixes(device)
    try:
        files = []
        walked = 0
        total_bytes = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in (QUARANTINE_DIRNAME, "$recycle.bin", "system volume information",
                                                ".git", "node_modules", "__pycache__")
                           and not d.startswith(".")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p, follow_symlinks=False)
                except OSError:
                    continue
                if not statmod.S_ISREG(st.st_mode):
                    continue
                if st.st_size > 8 * 1024 ** 3:
                    continue
                files.append((p, st))
                walked += 1
                total_bytes += st.st_size
                if walked % 40 == 0:
                    update_scan(conn, scan_id, file_count=walked, total_bytes=total_bytes,
                                progress=min(55, 6 + walked // 180))
                if walked >= MAX_FILES:
                    break
            if walked >= MAX_FILES:
                break

        update_scan(conn, scan_id, file_count=walked, total_bytes=total_bytes, phase="analyzing", progress=60)

        # 重复文件检测：按大小分组后计算哈希
        by_size = {}
        for p, st in files:
            if st.st_size >= 1024 * 1024:
                by_size.setdefault(st.st_size, []).append((p, st))
        hashed = {}
        budget = HASH_BUDGET
        for size, group in by_size.items():
            if len(group) < 2:
                continue
            for p, st in group:
                if budget <= 0:
                    break
                try:
                    hashed[p] = (sha256_file(p, min(budget, 64 * 1024 * 1024)), size)
                    budget -= min(size, 64 * 1024 * 1024)
                except OSError:
                    pass
        groups = {}
        for p, (h, size) in hashed.items():
            groups.setdefault(h, []).append(p)
        dup_map = {}
        for h, paths in groups.items():
            if len(paths) < 2:
                continue
            keeper = max(paths, key=lambda x: os.stat(x).st_mtime if os.path.exists(x) else 0)
            for p in paths:
                if p != keeper:
                    dup_map[p] = os.path.basename(keeper)

        # 分类
        rows = []
        stats = {k: {"count": 0, "bytes": 0} for k in RISK_ORDER}
        seen_dup = set()
        for i, (p, st) in enumerate(files):
            try:
                res = classify(p, st, protected, dup_map.get(p))
            except Exception:
                res = None
            if not res:
                continue
            risk, cat, label, conf, reason, gk = res
            if cat == "duplicate" and p in seen_dup:
                continue
            seen_dup.add(p)
            rows.append((scan_id, p, os.path.basename(p), st.st_size, st.st_mtime, cat, label,
                         risk, conf, reason, 1 if risk in ("R0", "R1") else 0, gk))
            stats[risk]["count"] += 1
            stats[risk]["bytes"] += st.st_size
            if i % 120 == 0:
                update_scan(conn, scan_id, progress=min(95, 60 + (i * 35 // max(1, len(files))))) 

        conn.executemany(
            """INSERT INTO items(scan_id,path,name,size,mtime,category,category_label,risk,
               confidence,reason,default_selected,group_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.execute("UPDATE devices SET last_scan_at=? WHERE id=?", (time.time(), device["id"]))
        update_scan(conn, scan_id, status="done", phase="done", progress=100, item_count=len(rows),
                    stats_json=json.dumps(stats, ensure_ascii=False), finished_at=time.time())
        conn.commit()
        conn.close()
    except Exception as e:
        try:
            update_scan(conn, scan_id, status="failed", error=str(e), finished_at=time.time())
            conn.close()
        except Exception:
            pass
        traceback.print_exc()
