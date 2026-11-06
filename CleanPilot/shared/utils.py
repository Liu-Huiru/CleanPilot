#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用工具函数：格式化、路径校验、系统内存信息。"""
import ctypes
import json
import os
import re


def fmt_bytes(n):
    n = float(n or 0)
    if n >= 1024 ** 3:
        return "%.1f GB" % (n / 1024 ** 3)
    if n >= 1024 ** 2:
        return "%.0f MB" % (n / 1024 ** 2)
    if n >= 1024:
        return "%.0f KB" % (n / 1024)
    return "%d B" % n


def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def inside(root, path):
    """判断 path 是否位于 root 之内（防止越界操作）。"""
    try:
        root = os.path.realpath(root)
        path = os.path.realpath(path)
        return os.path.commonpath([root, path]) == root
    except Exception:
        return False


def protected_prefixes(device):
    try:
        raw = json.loads(device.get("protected_paths") or "[]")
    except Exception:
        raw = []
    root = device["root_path"]
    out = []
    for p in raw:
        p = (p or "").strip()
        if not p:
            continue
        out.append(os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p)))
    return out


def mem_info():
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total = int(st.ullTotalPhys)
            avail = int(st.ullAvailPhys)
            return {"total": total, "used": max(0, total - avail), "percent": round((total - avail) * 100 / total)}
        with open("/proc/meminfo") as f:
            data = f.read()
        total = int(re.search(r"MemTotal:\s+(\d+)", data).group(1)) * 1024
        avail = int(re.search(r"MemAvailable:\s+(\d+)", data).group(1)) * 1024
        return {"total": total, "used": total - avail, "percent": round((total - avail) * 100 / total)}
    except Exception:
        return None
