#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 智能引擎：设备健康分析、清理建议与对话助手。"""
import shutil

from shared.utils import fmt_bytes


def ai_analyze_device(conn, device, scan=None):
    """AI 分析设备状态，生成清理建议摘要"""
    try:
        usage = shutil.disk_usage(device["root_path"])
        total, free = usage.total, usage.free
    except Exception:
        total, free = 0, 0
    used = max(0, total - free)
    pct = round(used * 100 / total, 1) if total else 0

    health = 92 if pct < 80 else (76 if pct < 90 else 58)
    health_label = "优秀" if health >= 85 else ("良好" if health >= 70 else "需关注")

    # 获取最近一次扫描结果
    if scan is None:
        scan = conn.execute(
            "SELECT * FROM scans WHERE device_id=? ORDER BY id DESC LIMIT 1", (device["id"],)
        ).fetchone()

    categories = {}
    cleanable = {"count": 0, "bytes": 0}
    if scan and scan["status"] == "done":
        rows = conn.execute(
            "SELECT category, category_label, risk, COUNT(*) c, SUM(size) s "
            "FROM items WHERE scan_id=? GROUP BY category, risk ORDER BY s DESC",
            (scan["id"],)
        ).fetchall()
        for r in rows:
            key = r["category_label"]
            if key not in categories:
                categories[key] = {"count": 0, "bytes": 0, "risks": {}}
            categories[key]["count"] += r["c"]
            categories[key]["bytes"] += r["s"]
            categories[key]["risks"][r["risk"]] = categories[key]["risks"].get(r["risk"], 0) + r["c"]
            if r["risk"] in ("R0", "R1"):
                cleanable["count"] += r["c"]
                cleanable["bytes"] += r["s"]

    recycle_count = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM recycle WHERE device_id=? AND state='held'",
        (device["id"],)
    ).fetchone()

    # AI 建议生成
    suggestions = []
    if pct >= 90:
        suggestions.append("存储空间已使用 %.1f%%，设备可能出现卡顿，建议立即清理" % pct)
    elif pct >= 80:
        suggestions.append("存储空间使用 %.1f%%，建议尽快清理以保持流畅" % pct)

    for label, info in sorted(categories.items(), key=lambda x: -x[1]["bytes"]):
        if info["bytes"] >= 100 * 1024 * 1024:
            suggestions.append("%s：%d 项，共 %s，建议优先清理" % (
                label, info["count"], fmt_bytes(info["bytes"])))

    if recycle_count["s"] >= 500 * 1024 * 1024:
        suggestions.append("回收站占用 %s，确认无需恢复后可清空释放空间" % fmt_bytes(recycle_count["s"]))

    if not suggestions:
        suggestions.append("设备状态良好，暂无需紧急清理的内容")

    return {
        "device": {"name": device["name"], "id": device["id"]},
        "storage": {"total": total, "used": used, "free": free, "percent": pct},
        "health": {"score": health, "label": health_label},
        "cleanable": cleanable,
        "categories": categories,
        "recycle": {"count": recycle_count["c"], "bytes": recycle_count["s"]},
        "suggestions": suggestions,
        "has_scan": scan is not None and scan["status"] == "done",
    }


def ai_chat(conn, user, device, message):
    """AI 对话助手：根据用户消息返回清理建议"""
    msg = (message or "").strip().lower()
    analysis = ai_analyze_device(conn, device)

    # 识别用户意图
    if any(k in msg for k in ("卡", "慢", "顿", "空间不足", "满了", "不够")):
        pct = analysis["storage"]["percent"]
        resp = "检测到您的设备存储空间已使用 <b>%.1f%%</b>（健康分：%d，%s）。" % (
            pct, analysis["health"]["score"], analysis["health"]["label"])
        if analysis["cleanable"]["bytes"] > 0:
            resp += "AI 发现可安全清理 <b>%s</b>（%d 项），主要包括：" % (
                fmt_bytes(analysis["cleanable"]["bytes"]), analysis["cleanable"]["count"])
            top = sorted(analysis["categories"].items(), key=lambda x: -x[1]["bytes"])[:4]
            for label, info in top:
                if info["bytes"] > 0:
                    resp += "<br>• %s：%s（%d 项）" % (label, fmt_bytes(info["bytes"]), info["count"])
            resp += "<br><br>建议优先清理缓存和安装包。是否开始扫描并清理？"
        else:
            resp += "当前暂未发现可清理内容，建议先运行一次扫描。"
        return {"reply": resp, "action": "scan" if analysis["cleanable"]["bytes"] == 0 else "plan"}

    if any(k in msg for k in ("删", "清理", "清", "释放")):
        if analysis["cleanable"]["bytes"] > 0:
            resp = "好的！AI 已为您筛选出可清理内容：<br>"
            top = sorted(analysis["categories"].items(), key=lambda x: -x[1]["bytes"])[:5]
            for label, info in top:
                if info["bytes"] > 0:
                    risks = info.get("risks", {})
                    safe = risks.get("R0", 0) + risks.get("R1", 0)
                    resp += "<br>• <b>%s</b>：%s（%d 项，其中 %d 项可直接清理）" % (
                        label, fmt_bytes(info["bytes"]), info["count"], safe)
            resp += "<br><br>共可释放 <b>%s</b>。所有清理都会移到回收站，可随时恢复。是否生成清理方案？" % (
                fmt_bytes(analysis["cleanable"]["bytes"]))
            return {"reply": resp, "action": "plan"}
        resp = "当前没有扫描结果。请先点击「开始扫描」，AI 会为您分析可清理内容。"
        return {"reply": resp, "action": "scan"}

    if any(k in msg for k in ("安全", "误删", "恢复", "回收站", "找回")):
        resp = ("请放心，CleanPilot 采用 <b>分级 + 回收站</b> 双重保护机制：<br><br>"
                "• <b>R0 安全清理</b>：缓存、临时文件，删除后可由程序重新生成<br>"
                "• <b>R1 建议清理</b>：过期安装包、重复文件，默认选中<br>"
                "• <b>R2 需谨慎</b>：旧文档、压缩包，需您逐项确认<br>"
                "• <b>R3 受保护</b>：相册照片等，系统永不清理<br><br>"
                "所有清理的文件都会移到回收站，保留 %d 天内可一键恢复。" % device["retention_days"])
        return {"reply": resp, "action": "none"}

    if any(k in msg for k in ("缓存", "cache", "临时")):
        cats = analysis["categories"]
        cache_info = cats.get("缓存与临时文件", {})
        if cache_info.get("bytes", 0) > 0:
            resp = "检测到缓存与临时文件 <b>%s</b>（%d 项）。这些是应用运行产生的临时数据，删除后不会影响使用，应用会自动重新生成。建议清理！" % (
                fmt_bytes(cache_info["bytes"]), cache_info["count"])
            return {"reply": resp, "action": "plan", "filter": "cache"}
        resp = "当前未检测到明显的缓存文件。可以先运行一次扫描看看。"
        return {"reply": resp, "action": "scan"}

    if any(k in msg for k in ("安装包", "apk", "安装")):
        cats = analysis["categories"]
        info = cats.get("安装包", {})
        if info.get("bytes", 0) > 0:
            resp = "检测到安装包 <b>%s</b>（%d 个）。安装完成后安装包就不再需要了，可以安全删除。" % (
                fmt_bytes(info["bytes"]), info["count"])
            return {"reply": resp, "action": "plan", "filter": "installer"}
        resp = "当前未检测到安装包。"
        return {"reply": resp, "action": "none"}

    if any(k in msg for k in ("重复", "一样", "相同")):
        cats = analysis["categories"]
        info = cats.get("重复文件", {})
        if info.get("bytes", 0) > 0:
            resp = "检测到重复文件 <b>%s</b>（%d 项）。这些是内容完全相同的文件（通过 SHA-256 校验），系统会保留最新的一份，清理其余副本。" % (
                fmt_bytes(info["bytes"]), info["count"])
            return {"reply": resp, "action": "plan", "filter": "duplicate"}
        resp = "当前未检测到重复文件。"
        return {"reply": resp, "action": "none"}

    if any(k in msg for k in ("截图", "截屏")):
        cats = analysis["categories"]
        info = cats.get("截图", {})
        if info.get("bytes", 0) > 0:
            resp = "检测到截图 <b>%s</b>（%d 张）。截图通常用于临时分享，长期不需要的可以清理。" % (
                fmt_bytes(info["bytes"]), info["count"])
            return {"reply": resp, "action": "plan", "filter": "screenshot"}
        resp = "当前未检测到截图。"
        return {"reply": resp, "action": "none"}

    if any(k in msg for k in ("大", "大文件")):
        cats = analysis["categories"]
        info = cats.get("大体积文件", {})
        if info.get("bytes", 0) > 0:
            resp = "检测到大体积文件（>500MB）<b>%s</b>（%d 个）。这些文件占用空间大，如果长期不用建议清理。" % (
                fmt_bytes(info["bytes"]), info["count"])
            return {"reply": resp, "action": "plan", "filter": "large"}
        resp = "当前未检测到超大文件。"
        return {"reply": resp, "action": "none"}

    if any(k in msg for k in ("你好", "hi", "hello", "在吗", "帮助", "怎么用")):
        resp = ("您好！我是 CleanPilot AI 助手 🤖<br><br>"
                "我可以帮您：<br>"
                "• 分析设备存储状态，找出可清理内容<br>"
                "• 按类别（缓存、安装包、重复文件等）给出清理建议<br>"
                "• 解释清理安全性，让您放心操作<br><br>"
                "您可以问我：「我能删什么？」「清理缓存」「太卡了怎么办」")
        return {"reply": resp, "action": "none"}

    # 默认回复
    pct = analysis["storage"]["percent"]
    resp = "我是 CleanPilot AI 助手。当前设备存储使用 <b>%.1f%%</b>，" % pct
    if analysis["cleanable"]["bytes"] > 0:
        resp += "可清理 <b>%s</b>。" % fmt_bytes(analysis["cleanable"]["bytes"])
        resp += "您可以问我「能删什么」「清理缓存」「安全吗」等问题。"
    else:
        resp += "暂无扫描数据。点击「开始扫描」后我可以为您分析。"
    return {"reply": resp, "action": "scan" if analysis["cleanable"]["bytes"] == 0 else "none"}
