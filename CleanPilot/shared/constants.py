#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局常量：风险等级、扫描上限、隔离目录等。"""

QUARANTINE_DIRNAME = ".cleanpilot"
MAX_FILES = 40000
HASH_BUDGET = 640 * 1024 * 1024

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
RISK_LABEL = {"R0": "安全清理", "R1": "建议清理", "R2": "需谨慎", "R3": "受保护"}

SCAN_PHASE_LABEL = {
    "walking": "正在遍历文件",
    "analyzing": "正在分析重复与风险",
    "done": "扫描完成",
    "failed": "扫描失败",
}

TASK_STATE_LABEL = {
    "running": "执行中",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "执行失败",
}
