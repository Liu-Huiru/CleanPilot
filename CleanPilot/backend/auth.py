#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证：PBKDF2 密码散列与令牌会话查询。"""
import hashlib
import hmac
import os


def hash_pw(pw, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 120000)
    return salt.hex() + "$" + digest.hex()


def check_pw(pw, stored):
    try:
        salt_hex, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), 120000)
        return hmac.compare_digest(calc.hex(), digest)
    except Exception:
        return False


def user_by_token(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)
    ).fetchone()
    return dict(row) if row else None
