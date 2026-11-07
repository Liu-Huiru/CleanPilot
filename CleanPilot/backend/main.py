#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CleanPilot 清源 —— 可运行的 AI 存储治理系统

启动:
  python app.py            # 或 python -m backend.main
  python app.py --demo     # 附带演示存储与演示设备
"""
import argparse
import os
from http.server import ThreadingHTTPServer

from .api import Handler
from .config import DATA_DIR, ROOT_DIR
from .database import db, init_db, seed_demo_device


def main():
    ap = argparse.ArgumentParser(description="CleanPilot 清源")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--demo", action="store_true", help="创建演示存储与演示设备")
    args = ap.parse_args()

    init_db()
    if args.demo:
        conn = db()
        seed_demo_device(conn)
        conn.close()
        print("演示数据已准备：%s" % os.path.join(ROOT_DIR, "demo_storage"))

    os.makedirs(DATA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 58)
    print("  CleanPilot 清源 已启动")
    print("  顾客端:   http://127.0.0.1:%d/" % args.port)
    print("  管理后台: http://127.0.0.1:%d/admin.html" % args.port)
    print("  演示账号: demo / user123    管理员: admin / admin123")
    print("=" * 58)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
