#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C 盘安全清理工具 — GUI 增强版 (tkinter，零第三方依赖)
===============================================================================
模块划分：
  1. 磁盘清理   —— 白名单临时 / 缓存目录 + 常用软件缓存，勾选后一键清理
  2. 大文件扫描 —— 扫描用户目录中的超大文件，支持定位 / 送回收站
  3. 内存&进程  —— 实时内存 / 磁盘曲线 + 后台进程占用排行 + 安全结束进程
  4. 自动清理   —— 定时规则（Windows 计划任务）+ 静默 --autorun 模式
  5. 排除设置   —— 图形化维护保留路径 / 模式，写回 cleanup_config.ini
  6. 报告       —— 清理前后对比，导出 TXT + HTML（含柱状图）

安全模型（全模块统一，不可由配置放宽）：
  A. 扫描白名单   —— 只收集明确列出的临时 / 缓存目录内的文件；
  B. 结构性越界校验 —— 删除前对每个路径二次确认其位于某个白名单根内；
  C. 受保护目录   —— C:\\Windows / Program Files / Users / ProgramData 等仅展示；
  D. 关键进程保护 —— 系统核心进程硬编码禁止结束；
  E. 确认门       —— 任何删除 / 结束进程动作都必须二次确认。

打包：pyinstaller --onefile --windowed --name CleanCDrive ^
        --add-data "cleanup_config.ini;." CleanCDrive.py
"""
import os
import sys
import re
import json
import time
import base64
import shutil
import datetime
import fnmatch
import glob
import ctypes
import subprocess
import threading

APP_NAME = "CleanCDrive"
APP_TITLE = "C 盘安全清理工具 · 增强版"
TASK_NAME = "CleanCDrive_AutoClean"

# 计划任务频率：界面上显示中文，写回配置时翻成 Windows 认识的关键字。
# 直接在下拉里摆 DAILY / WEEKLY 这类枚举值，对普通用户等于没写说明。
SCHED_ZH = {"DAILY": "每天", "WEEKLY": "每周", "HOURLY": "每小时", "ONLOGON": "登录时"}
SCHED_RAW = {v: k for k, v in SCHED_ZH.items()}

# ---------------------------------------------------------------------------
# 设计令牌 ·「科技蓝 · 小清新」
#   参考图的语言：大留白 + 白底圆角卡片 + 柔和双层阴影 + 药丸按钮 +
#   小节强调条 + 填充式信息贴片；此处将其转译为蓝色科技调，全局唯一色源。
#   GUI（tkinter）与 HTML 报告共同消费 THEME，保证两处视觉严格一致。
# ---------------------------------------------------------------------------
THEME = {
    # ---- 面：底 / 卡 / 内嵌面板 ----
    "page":    "#F1F5FB",   # 页面底色：极浅冷蓝
    "card":    "#FFFFFF",   # 卡片白
    "panel":   "#F7FAFF",   # 内嵌面板（图表底 / 输入框底）
    "tint":    "#E9F1FE",   # 浅蓝：次级按钮 / 表头 / 行悬停
    "tint2":   "#D5E3FB",   # 浅蓝加深：描边
    "selbg":   "#E4EEFE",   # 选中行底色
    "line":    "#E2EAF5",   # 分隔线 / 卡片描边
    "grid":    "#E9EFF8",   # 图表网格线
    # ---- 主色阶：科技蓝 ----
    "blue":    "#2B6BEF",   # 主色
    "blue_d":  "#1B4FD1",   # 深蓝 · 悬停 / 强调
    "blue_l":  "#7FA8F8",   # 亮蓝 · 渐变浅端
    "blue_t":  "#EAF1FE",   # 深底之上的浅字
    "cyan":    "#12B5C9",   # 点缀青 · 图表副序列
    "ink":     "#0F2547",   # 标题 · 深海军蓝
    # ---- 文字 ----
    "text":    "#33415C",
    "sub":     "#6B7A94",
    "muted":   "#9AA7BC",
    # ---- 语义色 ----
    "danger":   "#E4574C",
    "danger_d": "#C74238",
    "warnbg":   "#FDECEA",
    "success":  "#0FA46A",
    # ---- 阴影（tkinter 用双层色块模拟柔和投影）----
    "sh1":     "#DEE7F4",
    "sh2":     "#EBF1F9",
}

# 间距 / 圆角标度：全局统一，禁止各处随手写魔数
SP = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 32}
R_CARD, R_INNER, R_FIELD = 18, 12, 10

# ---------------------------------------------------------------------------
# 白名单：系统默认安全临时 / 缓存根
# ---------------------------------------------------------------------------
DEFAULT_SAFE_ROOTS = [
    "%TEMP%",
    "C:\\Windows\\Temp",
    "C:\\Windows\\SoftwareDistribution\\Download",
    "C:\\Windows\\Prefetch",
    "%LOCALAPPDATA%\\Microsoft\\Windows\\INetCache",
    "%LOCALAPPDATA%\\Microsoft\\Windows\\Temporary Internet Files",
    "%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer",
]

# 受保护的系统关键目录（仅展示，结构上禁止删除）
PROTECTED_ROOTS = [
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\Users",
    "C:\\ProgramData",
    "C:\\$Recycle.Bin",
    "C:\\System Volume Information",
    "启动分区 / EFI / 保留分区及 C: 根目录下的直接文件",
]

# 应用缓存白名单：仅列出的明确缓存子目录；Patterns 限定可删文件类型
APP_CACHE_DEFS = [
    ("Chrome 缓存", "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\*\\Cache", ["*"]),
    ("Chrome 代码缓存", "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\*\\Code Cache", ["*"]),
    ("Chrome GPU缓存", "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\*\\GPUCache", ["*"]),
    ("Edge 缓存", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\*\\Cache", ["*"]),
    ("Edge 代码缓存", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\*\\Code Cache", ["*"]),
    ("Edge GPU缓存", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\*\\GPUCache", ["*"]),
    ("Edge 着色器缓存", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\*\\GrShaderCache\\GPUCache", ["*"]),
    ("Edge 着色器缓存2", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\*\\ShaderCache\\GPUCache", ["*"]),
    ("Firefox 缓存", "%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles\\*\\cache2", ["*"]),
    ("Brave 缓存", "%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data\\*\\Cache", ["*"]),
    ("Opera 缓存", "%LOCALAPPDATA%\\Opera Software\\Opera\\Cache", ["*"]),
    ("Discord 缓存", "%LOCALAPPDATA%\\Discord\\Cache", ["*"]),
    ("Slack 缓存", "%LOCALAPPDATA%\\Slack\\Cache", ["*"]),
    ("Slack 日志", "%LOCALAPPDATA%\\Slack\\logs", ["*.log", "*.txt", "*.old"]),
    ("Teams 缓存", "%LOCALAPPDATA%\\Microsoft\\Teams\\Cache", ["*"]),
    ("Teams 代码缓存", "%LOCALAPPDATA%\\Microsoft\\Teams\\Code Cache", ["*"]),
    ("Teams GPU缓存", "%LOCALAPPDATA%\\Microsoft\\Teams\\GPUCache", ["*"]),
    ("Teams 临时", "%LOCALAPPDATA%\\Microsoft\\Teams\\tmp", ["*"]),
    ("Teams 日志", "%LOCALAPPDATA%\\Microsoft\\Teams\\logs", ["*.log", "*.txt", "*.old"]),
    ("Telegram 缓存", "%LOCALAPPDATA%\\Telegram Desktop\\tdata\\cache", ["*"]),
    ("WhatsApp 缓存", "%LOCALAPPDATA%\\WhatsApp\\Cache", ["*"]),
    ("微信 缓存", "%USERPROFILE%\\Documents\\WeChat Files\\*\\FileStorage\\Cache", ["*"]),
    ("QQ 临时", "%LOCALAPPDATA%\\Tencent\\QQ\\Temp", ["*"]),
    ("npm 缓存", "%LOCALAPPDATA%\\npm-cache", ["*"]),
    ("pip 缓存", "%LOCALAPPDATA%\\pip\\Cache", ["*"]),
    ("Yarn 缓存", "%LOCALAPPDATA%\\Yarn\\Cache", ["*"]),
    ("JetBrains 日志", "%LOCALAPPDATA%\\JetBrains\\*\\log", ["*.log", "*.txt"]),
    ("Spotify 缓存", "%LOCALAPPDATA%\\Spotify\\Data", ["*"]),
    ("Spotify 封面缓存", "%LOCALAPPDATA%\\Spotify\\Storage", ["*"]),
    ("Steam 下载残留", "%LOCALAPPDATA%\\Steam\\steamapps\\downloading", ["*"]),
    ("Steam 应用缓存", "%LOCALAPPDATA%\\Steam\\appcache", ["*"]),
    ("NVIDIA 着色器缓存", "%LOCALAPPDATA%\\NVIDIA\\GLCache", ["*"]),
    ("Adobe 媒体缓存", "%LOCALAPPDATA%\\Adobe\\Common\\Media Cache Files", ["*"]),
    ("Adobe 媒体缓存旧", "%LOCALAPPDATA%\\Adobe\\Common\\Media Cache", ["*"]),
    ("传递优化缓存", "C:\\ProgramData\\Microsoft\\Windows\\DeliveryOptimization\\Cache", ["*"]),
]

# ---------------------------------------------------------------------------
# 大文件扫描：默认扫描根与排除项
# ---------------------------------------------------------------------------
LARGE_FILE_SCAN_ROOTS = ["%USERPROFILE%", "C:\\Users\\Public"]

LARGE_FILE_EXCLUDE_DIRS = [
    "\\appdata\\local\\temp",
    "\\appdata\\local\\microsoft\\windows\\inetcache",
    "\\appdata\\local\\microsoft\\windows\\explorer",
    "\\appdata\\local\\packages",
    "\\appdata\\local\\microsoft\\windowsapps",
    "\\onedrive",
    "\\.git\\",
    "\\$recycle.bin",
    "\\system volume information",
]

# 这些扩展名即使很大也不允许通过本工具删除（避免误删可执行/驱动/安装包依赖）
LARGE_FILE_BLOCKED_EXT = {
    ".sys", ".dll", ".exe", ".msi", ".msp", ".ocx", ".drv", ".cpl",
    ".cat", ".inf", ".efi", ".mui", ".lnk",
}

LARGE_FILE_PROTECTED_PREFIX = [
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "c:\\$recycle.bin", "c:\\system volume information",
]

# ---------------------------------------------------------------------------
# 进程保护名单：硬编码，绝不允许结束
# ---------------------------------------------------------------------------
PROTECTED_PROCESSES = {
    "system", "idle", "registry", "memory compression", "secure system",
    "smss", "csrss", "wininit", "winlogon", "services", "lsass", "lsaiso",
    "svchost", "fontdrvhost", "dwm", "spoolsv", "audiodg", "sihost",
    "ctfmon", "taskhostw", "runtimebroker", "searchindexer", "searchhost",
    "securityhealthservice", "msmpeng", "nissrv", "explorer", "conhost",
    "logonui", "wudfhost", "wmiprvse", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "applicationframehost",
    "dllhost", "sppsvc", "wslservice", "trustedinstaller", "tiworker",
}

# 「可安全结束」提示：常见可关闭的后台驻留程序（仅提示，不自动结束）
CLOSEABLE_HINTS = {
    "msedge", "chrome", "firefox", "brave", "opera", "iexplore",
    "wechat", "weixin", "qq", "tim", "dingtalk", "wemeetapp", "telegram",
    "discord", "slack", "teams", "zoom", "spotify", "steam", "steamwebhelper",
    "node", "python", "code", "devenv", "pycharm64", "idea64", "webstorm64",
    "photoshop", "illustrator", "premiere", "afterfx", "acrobat",
    "onenote", "wps", "et", "wpp", "notepad++", "sublime_text",
    "thunder", "baidunetdisk", "aliload", "360se", "sogouexplorer",
    "nvidia share", "nvidia web helper", "adobedesktop", "javaw",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def fmt_size(b):
    try:
        b = float(b)
    except Exception:
        b = 0.0
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{int(b)} B"


def run_powershell(script, timeout=120):
    try:
        prelude = "$OutputEncoding=[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;\n"
        b64 = base64.b64encode((prelude + script).encode("utf-16-le")).decode("ascii")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.stdout, p.stderr, p.returncode
    except Exception as e:  # noqa
        return "", str(e), -1


def get_disk_free(drive="C"):
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    try:
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(drive + ":"),
            ctypes.byref(free), ctypes.byref(total), None)
    except Exception:
        return 0, 0
    return free.value, total.value


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_memory_info():
    """返回物理内存占用情况（无需第三方库）。"""
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return {
                "load": int(st.dwMemoryLoad),
                "total": int(st.ullTotalPhys),
                "avail": int(st.ullAvailPhys),
                "used": int(st.ullTotalPhys - st.ullAvailPhys),
            }
    except Exception:
        pass
    return {"load": 0, "total": 0, "avail": 0, "used": 0}


# --------------------------- 配置读取 / 写回 -------------------------------
MANAGED_BEGIN = "; ======== 以下区块由界面「排除设置」自动维护，请勿手动编辑 ========"
MANAGED_END = "; ======== 界面维护区块结束 ========"


def parse_config(path):
    cfg = {
        "retain_paths": [], "retain_patterns": [], "add_roots": [],
        "exclude_roots": [], "retain_age_days": 0, "exclude_apps": [],
        "exclude_paths": [], "exclude_patterns": [], "appcache_enabled": True,
        "skip_recycle": False,
        # 大文件扫描
        "large_threshold_mb": 200, "large_max_items": 200,
        # 自动清理
        "auto_enable": False, "auto_schedule": "DAILY", "auto_time": "03:00",
        "auto_targets": "system,appcache",
        "auto_ignore_battery": False,
    }
    if not path or not os.path.exists(path):
        return cfg
    section = ""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                l = raw.strip()
                if not l or l.startswith(";") or l.startswith("#"):
                    continue
                m = re.match(r"^\[(.+)\]$", l)
                if m:
                    section = m.group(1).strip().lower()
                    continue
                if "=" in l:
                    k, v = l.split("=", 1)
                    k, v = k.strip().lower(), v.strip()
                else:
                    k, v = "", l
                if section == "retain":
                    if k.startswith("pattern"):
                        if v:
                            cfg["retain_patterns"].append(v)
                    elif v:
                        cfg["retain_paths"].append(v)
                elif section == "saferoots":
                    if k.startswith("exclude"):
                        if v:
                            cfg["exclude_roots"].append(v)
                    elif v:
                        cfg["add_roots"].append(v)
                elif section == "options":
                    if k.startswith("retainfileagedays"):
                        try:
                            cfg["retain_age_days"] = int(v)
                        except Exception:
                            pass
                    elif k == "skiprecyclebin":
                        cfg["skip_recycle"] = v.lower() in ("1", "true", "yes")
                elif section == "appcache":
                    if k == "enable":
                        cfg["appcache_enabled"] = v.lower() not in ("0", "false", "no")
                    elif k == "excludeapp" and v:
                        cfg["exclude_apps"].append(v)
                    elif k == "excludepath" and v:
                        cfg["exclude_paths"].append(v)
                    elif k == "excludepattern" and v:
                        cfg["exclude_patterns"].append(v)
                elif section == "largefiles":
                    if k.startswith("thresholdmb"):
                        try:
                            cfg["large_threshold_mb"] = max(10, int(v))
                        except Exception:
                            pass
                    elif k.startswith("maxitems"):
                        try:
                            cfg["large_max_items"] = max(20, int(v))
                        except Exception:
                            pass
                elif section == "autoclean":
                    if k == "enable":
                        cfg["auto_enable"] = v.lower() in ("1", "true", "yes")
                    elif k == "schedule":
                        cfg["auto_schedule"] = v.strip().upper() or "DAILY"
                    elif k == "time":
                        cfg["auto_time"] = v.strip() or "03:00"
                    elif k == "targets":
                        cfg["auto_targets"] = v.strip().lower() or "system,appcache"
                    elif k.startswith("ignorebattery"):
                        cfg["auto_ignore_battery"] = v.lower() in ("1", "true", "yes")
    except Exception:
        pass
    return cfg


def save_managed_rules(ini_path, retain_paths, retain_patterns, age_days):
    """把界面维护的保留规则写回 ini 的托管区块（替换旧区块）。"""
    try:
        text = ""
        if os.path.exists(ini_path):
            with open(ini_path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        i = text.find(MANAGED_BEGIN)
        j = text.find(MANAGED_END)
        if i != -1 and j != -1:
            text = text[:i] + text[j + len(MANAGED_END):]
        block = [MANAGED_BEGIN, "[Retain]"]
        for p in retain_paths:
            if p.strip():
                block.append("Path=" + p.strip())
        for p in retain_patterns:
            if p.strip():
                block.append("Pattern=" + p.strip())
        block.append("")
        block.append("[Options]")
        block.append("RetainFileAgeDays=%d" % int(age_days))
        block.append(MANAGED_END)
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n\n" + "\n".join(block) + "\n")
        return True
    except Exception:
        return False


def is_retained(fp, fn, cfg):
    for p in cfg["retain_paths"]:
        pp = os.path.expandvars(p).rstrip("\\")
        if pp and (fp == pp or fp.startswith(pp + "\\")):
            return True
    for pat in cfg["retain_patterns"]:
        if fnmatch.fnmatch(fn, pat):
            return True
    for pat in cfg["exclude_patterns"]:
        if fnmatch.fnmatch(fn, pat):
            return True
    if cfg["retain_age_days"] > 0:
        try:
            age = (datetime.datetime.now() -
                   datetime.datetime.fromtimestamp(os.path.getmtime(fp))).days
            if age < cfg["retain_age_days"]:
                return True
        except OSError:
            pass
    return False


def _walk_error(err):
    pass


def scan_root(root, patterns, cfg):
    files = []
    total = 0
    count = 0
    root_norm = root.rstrip("\\") + "\\"
    if not os.path.isdir(root):
        return files, total, count
    try:
        for dirpath, _dirnames, filenames in os.walk(root, onerror=_walk_error):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                if not (fp == root or fp.startswith(root_norm)):
                    continue
                if patterns and not any(fnmatch.fnmatch(fn, p) for p in patterns):
                    continue
                if is_retained(fp, fn, cfg):
                    continue
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                files.append((fp, sz))
                total += sz
                count += 1
    except Exception:
        pass
    return files, total, count


def build_categories(cfg):
    categories = []
    excl = [os.path.expandvars(x).rstrip("\\") for x in cfg["exclude_roots"]]
    for r in DEFAULT_SAFE_ROOTS:
        er = os.path.expandvars(r)
        if not er:
            continue
        er = er.rstrip("\\")
        if er in excl:
            continue
        if os.path.isdir(er):
            files, total, count = scan_root(er, None, cfg)
            categories.append({
                "name": "系统临时: " + r, "root": er, "files": files,
                "total": total, "count": count, "kind": "system"})
    for r in cfg["add_roots"]:
        er = os.path.expandvars(r).rstrip("\\")
        if er and os.path.isdir(er):
            files, total, count = scan_root(er, None, cfg)
            categories.append({
                "name": "自定义根: " + er, "root": er, "files": files,
                "total": total, "count": count, "kind": "system"})
    if cfg["appcache_enabled"]:
        for name, pattern, patterns in APP_CACHE_DEFS:
            if name in cfg["exclude_apps"]:
                continue
            exp = os.path.expandvars(pattern)
            try:
                dirs = glob.glob(exp)
            except Exception:
                dirs = []
            for d in dirs:
                if not os.path.isdir(d):
                    continue
                dnorm = d.rstrip("\\")
                skip = False
                for xp in cfg["exclude_paths"]:
                    xpe = os.path.expandvars(xp).rstrip("\\")
                    if xpe and (dnorm == xpe or dnorm.startswith(xpe + "\\")):
                        skip = True
                        break
                if skip:
                    continue
                files, total, count = scan_root(dnorm, patterns, cfg)
                categories.append({
                    "name": "应用缓存: " + name, "root": dnorm, "files": files,
                    "total": total, "count": count, "kind": "app"})
    return categories


def get_recycle_bin():
    script = """
$shell = New-Object -ComObject Shell.Application
$bin = $shell.NameSpace(0x0a)
function Get-Size($it){
    $s=0
    try { $s=$it.Size } catch {}
    if($s -eq 0){
        try { $f=$it.GetFolder; if($f){ foreach($x in $f.Items()){ $s += Get-Size $x } } } catch {}
    }
    return $s
}
$sz=0; $c=0
foreach($it in $bin.Items()){ $sz += (Get-Size $it); $c++ }
[PSCustomObject]@{size=$sz; count=$c} | ConvertTo-Json
"""
    out, _err, _rc = run_powershell(script)
    try:
        data = json.loads(out.strip())
        return {"size": int(data.get("size", 0)), "count": int(data.get("count", 0))}
    except Exception:
        return {"size": 0, "count": 0}


def empty_recycle_bin():
    run_powershell("Clear-RecycleBin -DriveLetter C -Force -ErrorAction SilentlyContinue")


def delete_files(paths, allowed):
    deleted = freed = failed = 0
    for p, sz in paths:
        if not any(p == r or p.startswith(r + "\\") for r in allowed):
            failed += 1
            continue
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
                deleted += 1
                freed += sz
            elif os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                deleted += 1
                freed += sz
        except Exception:
            failed += 1
    return deleted, freed, failed


def clean_empty_dirs(allowed):
    for root in allowed:
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, _dn, _fn in os.walk(root, topdown=False, onerror=_walk_error):
                if dirpath.rstrip("\\") == root.rstrip("\\"):
                    continue
                if not any(dirpath == r or dirpath.startswith(r + "\\") for r in allowed):
                    continue
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                except OSError:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 大文件扫描 / 送回收站
# ---------------------------------------------------------------------------
def is_large_file_protected(fp):
    low = fp.lower().replace("/", "\\")
    for pref in LARGE_FILE_PROTECTED_PREFIX:
        if low == pref or low.startswith(pref + "\\"):
            return True
    return False


def scan_large_files(cfg, progress=None):
    """扫描用户目录中大文件。返回 [(path, size, mtime)]，按大小降序。"""
    thr = max(10, int(cfg.get("large_threshold_mb", 200))) * 1024 * 1024
    limit = max(20, int(cfg.get("large_max_items", 200)))
    found = []
    visited = set()
    for r in LARGE_FILE_SCAN_ROOTS:
        base = os.path.expandvars(r).rstrip("\\")
        if not base or not os.path.isdir(base) or base.lower() in visited:
            continue
        visited.add(base.lower())
        for dirpath, dirnames, filenames in os.walk(base, onerror=_walk_error):
            low = dirpath.lower()
            if any(x in low for x in LARGE_FILE_EXCLUDE_DIRS):
                dirnames[:] = []
                continue
            # 跳过重解析点（避免 junction / symlink 递归）
            pruned = []
            for d in dirnames:
                dp = os.path.join(dirpath, d)
                try:
                    if os.path.islink(dp):
                        continue
                except OSError:
                    continue
                pruned.append(d)
            dirnames[:] = pruned
            if progress:
                progress(dirpath)
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.islink(fp):
                        continue
                    st = os.stat(fp)
                except OSError:
                    continue
                if st.st_size < thr:
                    continue
                if is_large_file_protected(fp):
                    continue
                found.append((fp, st.st_size, st.st_mtime))
    found.sort(key=lambda x: x[1], reverse=True)
    return found[:limit]


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def recycle_paths(paths):
    """把文件送入回收站（可还原），返回 (成功数, 失败数)。"""
    ok = fail = 0
    batch = []
    for p in paths:
        if os.path.exists(p):
            batch.append(p)
    if not batch:
        return 0, 0
    # 分批，避免命令行长度 / 缓冲区过大
    for i in range(0, len(batch), 128):
        chunk = batch[i:i + 128]
        joined = "\0".join(chunk) + "\0\0"
        buf = ctypes.create_unicode_buffer(joined)
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
        try:
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception:
            rc = -1
        if rc == 0 and not op.fAnyOperationsAborted:
            ok += len(chunk)
        else:
            fail += len(chunk)
    return ok, fail


# ---------------------------------------------------------------------------
# 进程：枚举 / 结束（含关键进程保护）
# ---------------------------------------------------------------------------
def list_processes(top=120):
    script = """
$p = @()
try {
  $p = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -gt 0 } |
        Select-Object @{n='pid';e={$_.Id}},
                      @{n='name';e={$_.ProcessName}},
                      @{n='ws';e={ try { [int64]$_.WorkingSet64 } catch { 0 } }},
                      @{n='title';e={ try { [string]$_.MainWindowTitle } catch { '' } }} |
        Sort-Object ws -Descending |
        Select-Object -First %d
} catch {}
ConvertTo-Json -InputObject @($p) -Compress
""" % int(top)
    out, _err, _rc = run_powershell(script, timeout=60)
    try:
        data = json.loads(out.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []
    procs = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            pid = int(d.get("pid", 0))
        except Exception:
            continue
        name = str(d.get("name") or "")
        try:
            ws = int(d.get("ws") or 0)
        except Exception:
            ws = 0
        title = str(d.get("title") or "")
        procs.append({
            "pid": pid, "name": name, "ws": ws, "title": title,
            "protected": name.lower() in PROTECTED_PROCESSES or pid in (0, 4),
            "hint": name.lower() in CLOSEABLE_HINTS,
        })
    return procs


def kill_process(pid):
    pid = int(pid)
    if pid in (0, 4):
        return False, "系统关键进程，已拒绝"
    try:
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x0001, False, pid)
        if not h:
            return False, "无权限（可能需管理员）或进程已退出"
        ok = k.TerminateProcess(h, 0)
        k.CloseHandle(h)
        return (bool(ok), "" if ok else "结束失败")
    except Exception as e:  # noqa
        return False, str(e)


# ---------------------------------------------------------------------------
# 自动清理：Windows 计划任务
# ---------------------------------------------------------------------------
def _exe_path():
    return os.path.abspath(sys.executable)


def scheduled_task_query():
    """查询计划任务是否存在（使用 PowerShell 计划任务模块，不依赖 schtasks.exe）。"""
    script = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue;"
              "if ($t) { $i = $t | Get-ScheduledTaskInfo;"
              "  'STATE=' + $t.State;"
              "  'NEXT=' + $i.NextRunTime;"
              "  'LAST=' + $i.LastRunTime;"
              "  'RESULT=' + $i.LastTaskResult } else { 'STATE=NONE' }" % TASK_NAME)
    out, _err, _rc = run_powershell(script, timeout=60)
    txt = (out or "").strip()
    if "STATE=" in txt and "STATE=NONE" not in txt:
        return True, txt
    return False, txt


def _trigger_ps(sched, t):
    """构造 PowerShell 触发器表达式。"""
    hh, mm = "03", "00"
    try:
        parts = (t or "03:00").split(":")
        hh = str(int(parts[0])).zfill(2)
        mm = str(int(parts[1])).zfill(2) if len(parts) > 1 else "00"
    except Exception:
        pass
    if sched == "WEEKLY":
        return ("New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '%s:%s'" % (hh, mm))
    if sched == "HOURLY":
        return ("New-ScheduledTaskTrigger -Once -At (Get-Date) "
                "-RepetitionInterval (New-TimeSpan -Hours 1)")
    if sched == "ONLOGON":
        return "New-ScheduledTaskTrigger -AtLogOn"
    return ("New-ScheduledTaskTrigger -Daily -At '%s:%s'" % (hh, mm))


def scheduled_task_register(cfg):
    if not getattr(sys, "frozen", False):
        return False, "仅打包后的 exe 支持注册计划任务（当前为源码运行模式）"
    sched = (cfg.get("auto_schedule") or "DAILY").upper()
    if sched not in ("DAILY", "WEEKLY", "HOURLY", "ONLOGON"):
        sched = "DAILY"
    exe = _exe_path().replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        "try {"
        "  $a = New-ScheduledTaskAction -Execute '%s' -Argument '--autorun';"
        "  $t = %s;"
        "  $s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        "  Register-ScheduledTask -TaskName '%s' -Action $a -Trigger $t -Settings $s "
        "-Description 'CleanCDrive 自动清理（白名单安全清理）' -Force | Out-Null;"
        "  'OK'"
        "} catch { 'ERR: ' + $_.Exception.Message }"
        % (exe, _trigger_ps(sched, cfg.get("auto_time") or "03:00"), TASK_NAME))
    out, err, _rc = run_powershell(script, timeout=90)
    txt = (out or "").strip() or (err or "").strip()
    ok = txt.startswith("OK")
    if ok:
        return True, "计划任务 %s 已注册（%s）" % (TASK_NAME, sched)
    return False, txt


def scheduled_task_delete():
    script = ("try { Unregister-ScheduledTask -TaskName '%s' "
              "-Confirm:$false -ErrorAction Stop; 'OK' } "
              "catch { 'ERR: ' + $_.Exception.Message }" % TASK_NAME)
    out, err, _rc = run_powershell(script, timeout=60)
    txt = (out or "").strip() or (err or "").strip()
    return txt.startswith("OK"), txt


# ---------------------------------------------------------------------------
# 报告：TXT + HTML（含柱状图）
# ---------------------------------------------------------------------------
def log_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def write_report(lines, tag="cleanup"):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir(), f"{tag}_report_{ts}.log")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    return path


def _c(key):
    """取设计令牌颜色（供 HTML 报告与 GUI 共用同一色源）。"""
    return THEME.get(key, "#000000")


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_html_report(rep, tag="cleanup"):
    """生成带柱状图的 HTML 对比报告。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir(), f"{tag}_report_{ts}.html")
    before = rep["before"][0]
    after = rep["after"][0]
    real = after - before
    rows = []
    for name, d, f, fl in rep.get("per_cat", []):
        if d <= 0 and fl <= 0:
            continue
        rows.append((name, d, f, fl))
    if rep.get("rb_count", 0) > 0:
        rows.append(("回收站", rep["rb_count"], rep.get("rb_size", 0), 0))
    maxsz = max([r[2] for r in rows], default=1) or 1

    bars = []
    for name, d, f, fl in rows:
        w = int(max(2, round(f / maxsz * 100)))
        color = "linear-gradient(90deg,#7FA8F8,#2B6BEF)" if d > 0 else "#D5E3FB"
        bars.append(
            f'<div class="row"><div class="nm">{_esc(name)}</div>'
            f'<div class="bar"><span style="width:{w}%;background:{color}"></span></div>'
            f'<div class="val">{fmt_size(f)} · {d} 项'
            + (f' · 跳过 {fl}' if fl else '') + '</div></div>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>C 盘清理报告 {ts}</title>
<style>
/* 与 GUI 共用同一套「科技蓝 · 小清新」设计令牌 */
body{{font-family:"Microsoft YaHei UI",Segoe UI,sans-serif;background:{_c('page')};
     color:{_c('text')};margin:0;padding:32px;-webkit-font-smoothing:antialiased}}
.card{{background:{_c('card')};border-radius:18px;padding:26px 30px;max-width:880px;
      margin:0 auto 18px;border:1px solid {_c('line')};
      box-shadow:0 4px 10px {_c('sh1')},0 12px 28px {_c('sh2')};position:relative;overflow:hidden}}
.card::before{{content:"";position:absolute;left:0;right:0;top:0;height:4px;
              background:linear-gradient(90deg,{_c('blue_l')},{_c('blue')})}}
h1{{font-size:21px;margin:0 0 6px;color:{_c('ink')};letter-spacing:.2px}}
h2{{font-size:15px;margin:0 0 16px;color:{_c('ink')}}}
h2::before{{content:"";display:inline-block;width:4px;height:14px;border-radius:2px;
           background:{_c('blue')};margin-right:9px;vertical-align:-2px}}
.sub{{color:{_c('muted')};font-size:12px}}
.kpi{{display:flex;gap:14px;flex-wrap:wrap;margin-top:18px}}
.kpi div{{flex:1;min-width:158px;background:{_c('panel')};border:1px solid {_c('line')};
         border-radius:12px;padding:14px 16px}}
.kpi b{{display:block;font-size:20px;color:{_c('ink')};margin-top:6px}}
.row{{display:flex;align-items:center;gap:12px;margin:9px 0;font-size:12.5px}}
.nm{{width:220px;color:{_c('text')};overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar{{flex:1;height:14px;background:{_c('grid')};border-radius:7px;overflow:hidden}}
.bar span{{display:block;height:100%;border-radius:7px}}
.val{{width:190px;text-align:right;color:{_c('sub')};white-space:nowrap}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
th{{background:{_c('tint')};color:{_c('blue_d')};font-weight:600}}
td,th{{border-bottom:1px solid {_c('grid')};padding:9px 10px;text-align:left}}
.note{{color:{_c('muted')};font-size:12px;margin-top:12px}}
</style></head><body>
<div class="card">
  <h1>C 盘清理报告</h1>
  <div class="sub">生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　·　{APP_NAME}</div>
  <div class="kpi">
    <div>清理前可用空间<b>{fmt_size(before)}</b></div>
    <div>清理后可用空间<b>{fmt_size(after)}</b></div>
    <div>实际可用变化<b>{('+' if real >= 0 else '')}{fmt_size(real)}</b></div>
    <div>删除字节合计<b>{fmt_size(rep.get('freed', 0))}</b></div>
  </div>
  <div class="note">成功删除 {rep.get('deleted', 0)} 项，跳过（被占用/无权限）{rep.get('failed', 0)} 项。
  跳过项不会被强制删除，关闭对应软件后重跑可清理。</div>
</div>
<div class="card">
  <h2>分类明细（实际删除）</h2>
  {''.join(bars) if bars else '<div class="note">本次没有任何文件被删除。</div>'}
</div>
<div class="card">
  <h2>明细表</h2>
  <table><tr><th>分类</th><th>删除项数</th><th>释放空间</th><th>跳过</th></tr>
  {''.join(f'<tr><td>{_esc(n)}</td><td>{d}</td><td>{fmt_size(f)}</td><td>{fl}</td></tr>' for n, d, f, fl in rows) or '<tr><td colspan="4">无</td></tr>'}
  </table>
</div>
</body></html>"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass
    return path


def _safe_write(name, text):
    try:
        with open(os.path.join(log_dir(), name), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _emit(out):
    try:
        print(out)
    except Exception:
        pass


def find_ini():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    meipass = getattr(sys, "_MEIPASS", None)
    cands = [
        os.path.join(exe_dir, "cleanup_config.ini"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cleanup_config.ini"),
    ]
    if meipass:
        cands.append(os.path.join(meipass, "cleanup_config.ini"))
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]


def writable_ini():
    """返回「可写」的配置文件路径。

    单文件 exe 下内置配置位于临时解压目录（退出即删除），直接写回会丢失。
    因此这里优先使用 exe 同目录的 ini；若不存在，则把内置配置复制一份过去。
    """
    base = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(base, "cleanup_config.ini")
    if os.path.exists(target):
        return target
    src = find_ini()
    try:
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(target):
            shutil.copyfile(src, target)
    except Exception:
        pass
    return target


# ---------------------------------------------------------------------------
# 静默自动清理（计划任务调用：exe --autorun）
# ---------------------------------------------------------------------------
def autorun():
    cfg = parse_config(writable_ini())
    if not cfg.get("auto_enable"):
        _safe_write("_autorun.txt", "自动清理未启用（[AutoClean] Enable=0），已跳过。")
        return
    targets = [t.strip() for t in (cfg.get("auto_targets") or "").split(",") if t.strip()]
    before = get_disk_free("C")
    cats = build_categories(cfg)
    if "system" not in targets:
        cats = [c for c in cats if c["kind"] != "system"]
    if "appcache" not in targets:
        cats = [c for c in cats if c["kind"] != "app"]
    allowed = [c["root"] for c in cats]
    per_cat = []
    deleted = freed = failed = 0
    for c in cats:
        d, f, fl = delete_files(c["files"], allowed)
        if d or fl:
            per_cat.append((c["name"], d, f, fl))
        deleted += d
        freed += f
        failed += fl
    clean_empty_dirs(allowed)
    rb_count = rb_size = 0
    if "recycle" in targets and not cfg["skip_recycle"]:
        info = get_recycle_bin()
        empty_recycle_bin()
        rb_count = info.get("count", 0)
        rb_size = info.get("size", 0)
        deleted += rb_count
        freed += rb_size
    after = get_disk_free("C")
    rep = {"before": before, "after": after, "deleted": deleted, "freed": freed,
           "failed": failed, "per_cat": per_cat, "rb_count": rb_count, "rb_size": rb_size}
    lines = ["====== 自动清理报告 (%s) ======" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    lines.append("清理前 C: 可用 : %s" % fmt_size(before[0]))
    lines.append("清理后 C: 可用 : %s" % fmt_size(after[0]))
    lines.append("实际释放       : %s（成功 %d 项，跳过 %d 项）" % (fmt_size(freed), deleted, failed))
    for name, d, f, fl in per_cat:
        if d:
            lines.append("  - %s: %s (%d 项)" % (name, fmt_size(f), d))
    logp = write_report(lines, tag="autorun")
    htmlp = write_html_report(rep, tag="autorun")
    _safe_write("_autorun.txt", "\n".join(lines) + "\nLOG=%s\nHTML=%s" % (logp, htmlp))
    _emit("\n".join(lines))


# ---------------------------------------------------------------------------
# 自检（用于验证打包后的 exe；结果写文件，因 --windowed 无 stdout）
# ---------------------------------------------------------------------------
def selftest():
    lines = ["=== SELFTEST ==="]
    cfg = parse_config(writable_ini())
    lines.append("配置：ini=%s  大文件阈值=%dMB  自动清理=%s  应用缓存=%s"
                 % (writable_ini(), cfg["large_threshold_mb"],
                    cfg["auto_enable"], cfg["appcache_enabled"]))
    wini = writable_ini()
    lines.append("可写配置路径：%s（已存在=%s）" % (wini, os.path.exists(wini)))
    mem = get_memory_info()
    lines.append("内存：使用 %s / %s （%d%%）" % (fmt_size(mem["used"]), fmt_size(mem["total"]), mem["load"]))
    before = get_disk_free("C")
    lines.append("磁盘 C: 可用 %s / %s" % (fmt_size(before[0]), fmt_size(before[1])))
    cats = build_categories(cfg)
    total = sum(c["total"] for c in cats)
    cnt = sum(c["count"] for c in cats)
    for c in cats:
        lines.append("  [%s] %s: %d 项, %s -> %s" % (c["kind"], c["name"], c["count"], fmt_size(c["total"]), c["root"]))
    rb = None if cfg["skip_recycle"] else get_recycle_bin()
    if rb:
        lines.append("  回收站: %d 项 %s" % (rb["count"], fmt_size(rb["size"])))
    lines.append("磁盘可清理合计: %d 项, %s" % (cnt, fmt_size(total)))
    # 大文件扫描（限时抽样，避免自检过慢）
    t0 = time.time()
    big = scan_large_files({"large_threshold_mb": max(cfg["large_threshold_mb"], 500),
                            "large_max_items": 10})
    lines.append("大文件扫描(>=%dMB): 命中 %d 个, 耗时 %.1fs"
                 % (max(cfg["large_threshold_mb"], 500), len(big), time.time() - t0))
    for p, s, _m in big[:5]:
        lines.append("   %s  %s" % (fmt_size(s), p))
    # 进程模块
    procs = list_processes(30)
    lines.append("进程枚举: %d 个；其中受保护 %d 个"
                 % (len(procs), sum(1 for p in procs if p["protected"])))
    for p in procs[:5]:
        lines.append("   pid=%d %s  %s%s" % (p["pid"], p["name"], fmt_size(p["ws"]),
                                             "  [受保护]" if p["protected"] else ""))
    # 计划任务状态
    ok_task, _info = scheduled_task_query()
    lines.append("计划任务 %s: %s" % (TASK_NAME, "已注册" if ok_task else "未注册"))
    # 环境运行库自检
    try:
        import env_probe
        _ev = env_probe.scan()
        _blk = [i for i in _ev if i["level"] == "blocker"]
        _wrn = [i for i in _ev if i["level"] == "warn"]
        for i in _ev:
            lines.append("  环境[%s] %s: %s" % (i["level"], i["title"], i.get("detail", "")))
        lines.append("环境自检: blockers=%d warns=%d" % (len(_blk), len(_wrn)))
    except Exception as e:  # noqa
        lines.append("环境自检: 异常 %r" % e)
    # HTML 报告生成能力
    demo = {"before": before, "after": (before[0] + total, before[1]), "deleted": cnt,
            "freed": total, "failed": 0,
            "per_cat": [(c["name"], c["count"], c["total"], 0) for c in cats if c["count"]],
            "rb_count": rb["count"] if rb else 0, "rb_size": rb["size"] if rb else 0}
    hp = write_html_report(demo, tag="selftest")
    lines.append("HTML 报告生成: %s" % ("OK -> " + os.path.basename(hp) if os.path.exists(hp) else "FAILED"))
    lines.append("SELFTEST_OK")
    out = "\n".join(lines)
    _safe_write("_selftest.txt", out)
    _emit(out)


def guitest():
    """界面冒烟测试：完整构建新外壳 → 驱动关键交互 → 断言 → 结果写文件。

    窗口以全透明方式离屏渲染，因此布局尺寸真实（Canvas 会真正绘制），
    但不会在用户屏幕上闪现。仅执行只读操作，不做任何删除。
    """
    lines = []
    try:
        res = start_gui(smoke=True) or []
        for st, name, extra in res:
            lines.append("[%s] %s%s" % (st, name, ("  " + extra) if extra else ""))
        bad = [r for r in res if r[0] != "PASS"]
        lines.append("GUI_OK" if not bad else "GUI_ISSUES: %d" % len(bad))
    except Exception as e:  # noqa
        import traceback
        lines.append("GUI_FAIL: " + repr(e))
        lines.append(traceback.format_exc())
    out = "\n".join(lines)
    _safe_write("_guitest.txt", out)
    _emit(out)


# ===========================================================================
# GUI
# ===========================================================================
# ===========================================================================
# 环境运行库自检（程序启动第一步）
# ===========================================================================
def _run_env_guard(root, smoke, out):
    """环境自检守护。

    返回 True=继续启动；False=用户选择退出（调用方应 destroy root 并 return）。
    smoke 模式下不弹窗，仅把结果写入 out["items"] 供 --guitest 记录。
    """
    try:
        import env_probe
    except Exception as e:  # noqa
        if smoke:
            out["items"] = [("WARN", "环境自检", "无法导入 env_probe: " + repr(e))]
        return True
    try:
        issues = env_probe.scan()
    except Exception as e:  # noqa
        if smoke:
            out["items"] = [("WARN", "环境自检", "自检异常: " + repr(e))]
        return True

    if smoke:
        items = []
        for it in issues:
            if it["level"] == "ok":
                items.append(("PASS", "环境-" + it["title"], it.get("detail", "")))
            elif it["level"] == "blocker":
                items.append(("FAIL", "环境-" + it["title"], it.get("detail", "")))
            else:
                items.append(("WARN", "环境-" + it["title"], it.get("detail", "")))
        out["items"] = items
        return True

    actionable = [i for i in issues if i["level"] in ("blocker", "warn")]
    if not actionable:
        return True

    root.withdraw()                 # 构建主界面前先藏起空白主窗，只弹检测框
    go = _env_dialog(root, issues)
    if go:
        root.deiconify()
    return go


def _env_dialog(root, issues):
    """弹出环境检测对话框：声明缺什么，用户同意后自动修复。返回 True=继续。"""
    import tkinter as tk
    import tkinter.font as tkfont   # noqa  (保持与界面一致的字号来源)
    import threading
    T = THEME

    dlg = tk.Toplevel(root)
    dlg.title("环境检测 · 运行库缺失")
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=T["page"])
    W, H = 560, 470
    dlg.update_idletasks()
    sw = dlg.winfo_screenwidth(); sh = dlg.winfo_screenheight()
    dlg.geometry("%dx%d+%d+%d" % (W, H, max(0, (sw - W) // 2), max(0, (sh - H) // 3)))

    tk.Label(dlg, text="检测到运行环境缺少必要组件",
             font=("Microsoft YaHei UI", 13, "bold"), fg=T["ink"], bg=T["page"]
             ).pack(pady=(18, 4))
    tk.Label(dlg, text="程序需要以下运行库才能正常启动；可授权后自动修复。",
             font=("Microsoft YaHei UI", 9), fg=T["sub"], bg=T["page"]
             ).pack()

    list_frame = tk.Frame(dlg, bg=T["card"], bd=1, relief="solid",
                          highlightbackground=T["line"], highlightthickness=1)
    list_frame.pack(fill="both", expand=True, padx=18, pady=(12, 6))
    canvas = tk.Canvas(list_frame, bg=T["card"], highlightthickness=0)
    sb = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=T["card"])
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    def _conf(_e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _conf)

    mark_color = {"blocker": T["danger"], "warn": "#E8A33D", "ok": T["success"]}
    mark_text = {"blocker": "✗ 阻断", "warn": "! 警告", "ok": "✓ 正常"}
    for it in issues:
        row = tk.Frame(inner, bg=T["card"])
        row.pack(fill="x", padx=10, pady=6)
        tk.Label(row, text=mark_text.get(it["level"], "?"),
                 fg=mark_color.get(it["level"], T["sub"]),
                 font=("Microsoft YaHei UI", 10, "bold"), bg=T["card"],
                 width=8, anchor="w").pack(side="left")
        col = tk.Frame(row, bg=T["card"])
        col.pack(side="left", fill="x", expand=True)
        tk.Label(col, text=it["title"], fg=T["ink"],
                 font=("Microsoft YaHei UI", 10, "bold"), bg=T["card"],
                 anchor="w").pack(anchor="w")
        if it.get("detail"):
            tk.Label(col, text=it["detail"], fg=T["sub"],
                     font=("Microsoft YaHei UI", 9), bg=T["card"], anchor="w",
                     wraplength=400, justify="left").pack(anchor="w")
        if it.get("auto"):
            tk.Label(col, text="→ 可一键修复：" + it.get("fix", ""), fg=T["blue_d"],
                     font=("Microsoft YaHei UI", 9), bg=T["card"], anchor="w",
                     wraplength=400, justify="left").pack(anchor="w")

    status = tk.Label(dlg, text="", fg=T["sub"], bg=T["page"],
                      font=("Microsoft YaHei UI", 9))
    status.pack(pady=(2, 2))

    result = {"go": False}
    auto_issues = [i for i in issues if i.get("auto")]
    has_blocker = any(i["level"] == "blocker" for i in issues)

    def _finish(ok, msg):
        status.configure(text=msg)
        if ok:
            try:
                import env_probe
                still = [i for i in env_probe.scan()
                         if i["level"] in ("blocker", "warn")]
            except Exception:
                still = []
            if not still:
                result["go"] = True
                dlg.after(500, dlg.destroy)
                return
        btn_fix.configure(state="normal")
        btn_skip.configure(state="normal")
        btn_exit.configure(state="normal")

    def _do_fix():
        btn_fix.configure(state="disabled")
        btn_skip.configure(state="disabled")
        btn_exit.configure(state="disabled")
        status.configure(text="正在准备…")

        def _work():
            import env_probe
            for it in auto_issues:
                if it["key"] == "vcredist":
                    def _prog(done, total, phase):
                        if phase == "downloading":
                            pct = (100.0 * done / max(total, 1)) if total else 0
                            status.configure(text="正在下载运行库 %.0f%%（需联网）" % pct)
                        else:
                            status.configure(
                                text="正在安装运行库（可能弹出 UAC 授权，请点「是」）…")
                    r = env_probe.install_vc_redist(_prog)
                    if r is not True:
                        dlg.after(0, lambda: _finish(
                            False, "❌ " + (r if isinstance(r, str) else "安装未完成")))
                        return
            dlg.after(0, lambda: _finish(True, "✅ 修复完成，正在启动…"))

        threading.Thread(target=_work, daemon=True).start()

    btn_frame = tk.Frame(dlg, bg=T["page"])
    btn_frame.pack(fill="x", padx=18, pady=(6, 16))
    btn_fix = tk.Button(
        btn_frame, text="修复并继续" if auto_issues else "尝试修复",
        command=_do_fix, bg=T["blue"], fg="white",
        font=("Microsoft YaHei UI", 10, "bold"), relief="flat",
        padx=14, pady=6, state="normal" if auto_issues else "disabled")
    btn_skip = tk.Button(
        btn_frame, text="仍然继续（有风险）",
        command=lambda: (result.__setitem__("go", True), dlg.destroy()),
        bg=T["tint"], fg=T["blue_d"], font=("Microsoft YaHei UI", 10),
        relief="flat", padx=12, pady=6)
    btn_exit = tk.Button(
        btn_frame, text="退出",
        command=lambda: (result.__setitem__("go", False), dlg.destroy()),
        bg=T["card"], fg=T["danger"], font=("Microsoft YaHei UI", 10),
        relief="flat", padx=12, pady=6,
        highlightbackground=T["line"], highlightthickness=1)
    btn_fix.pack(side="left")
    if not has_blocker:
        btn_skip.pack(side="right", padx=(6, 0))
    btn_exit.pack(side="right")

    dlg.wait_window()
    return result["go"]


def start_gui(smoke=False):
    import tkinter as tk
    from tkinter import ttk, messagebox, font as tkfont

    # =======================================================================
    # 设计令牌 ·「科技蓝 · 小清新」—— 全部取自模块级 THEME，单一色源
    # =======================================================================
    T = THEME
    PAGE    = T["page"]      # 页面底色：极浅冷蓝
    CARD    = T["card"]      # 卡片白
    PANEL   = T["panel"]     # 内嵌面板（图表底 / 输入框底）
    TINT    = T["tint"]      # 浅蓝：次级按钮 / 表头 / 行悬停
    TINT2   = T["tint2"]     # 浅蓝加深：描边
    SELBG   = T["selbg"]     # 选中行底色
    LINE    = T["line"]      # 分隔线 / 卡片描边
    GRID    = T["grid"]      # 图表网格线
    BLUE    = T["blue"]      # 主色 · 科技蓝
    BLUE_D  = T["blue_d"]    # 深蓝 · 悬停 / 强调
    BLUE_L  = T["blue_l"]    # 亮蓝 · 渐变浅端
    BLUE_T  = T["blue_t"]    # 深底之上的浅字
    CYAN    = T["cyan"]      # 点缀青 · 图表副序列
    INK     = T["ink"]       # 标题 · 深海军蓝
    TEXT    = T["text"]      # 正文
    SUB     = T["sub"]       # 次要文字
    MUTED   = T["muted"]     # 更淡文字
    DANGER  = T["danger"]    # 危险
    DANGER_D = T["danger_d"]
    WARNBG  = T["warnbg"]    # 警告条底
    SUCCESS = T["success"]   # 正向 / 「可安全关闭」
    SH1     = T["sh1"]       # 阴影内层
    SH2     = T["sh2"]       # 阴影外层
    BG      = PAGE           # 兼容别名
    ACCENT  = BLUE           # 兼容别名

    FONT    = ("Microsoft YaHei UI", 10)
    FONT_B  = ("Microsoft YaHei UI", 10, "bold")
    FONT_T  = ("Microsoft YaHei UI", 18, "bold")
    FONT_ST = ("Microsoft YaHei UI", 11, "bold")
    FONT_S  = ("Microsoft YaHei UI", 9)
    FONT_XS = ("Microsoft YaHei UI", 8)
    FONT_NUM = ("Microsoft YaHei UI", 15, "bold")   # 数据贴片数值
    MONO    = ("Consolas", 9)

    # 圆角 / 内边距 / 间距，统一取自全局标度
    R        = R_CARD           # 卡片圆角
    R_IN     = R_INNER          # 内层小卡圆角
    PAD      = SP["l"] + 2      # 卡片内边距
    PAD_S    = SP["m"]          # 内层小卡内边距
    GAP      = SP["m"]          # 组件间距

    # ---------------------------- 颜色工具 ----------------------------
    def _mix(c1, c2, t):
        a1 = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        a2 = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(int(a1[i] + (a2[i] - a1[i]) * t) for i in range(3))

    def _darken(c, t=0.10):
        return _mix(c, "#000000", t)

    def _lighten(c, t=0.10):
        return _mix(c, "#ffffff", t)

    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        """在 Canvas 上画平滑圆角矩形（tkinter 无原生圆角，用平滑多边形模拟）。"""
        r = max(0.0, min(r, abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    _FCACHE = {}

    def _font(spec):
        k = tuple(spec)
        if k not in _FCACHE:
            _FCACHE[k] = tkfont.Font(font=spec)
        return _FCACHE[k]

    def _measure(spec, text):
        return _font(spec).measure(text)

    def _ellipsize(spec, text, maxw):
        """按「像素宽度」截断文本并补省略号。

        不能用 len(text) 估算：中文、英文、数字的字宽差异极大，
        按字符数截断后依然会溢出，把右侧的项数 / 大小文字压住。
        这里直接量测二分，保证结果一定放得进 maxw。
        """
        if maxw <= 0:
            return ""
        f = _font(spec)
        if f.measure(text) <= maxw:
            return text
        ell = "…"
        ew = f.measure(ell)
        if ew > maxw:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(text[:mid]) + ew <= maxw:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo] + ell) if lo else ell

    def _outer_bg(w):
        try:
            return str(w.cget("bg"))
        except Exception:
            return PAGE

    def _center(win, w, h):
        """把窗口居中到屏幕（多显示器下取主屏可用区域）。"""
        try:
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            pass

    # ---------------------------- 配色派生工具 ----------------------------
    # 次级按钮（浅蓝底 + 深蓝字），全界面统一
    BTN2    = TINT
    BTN2_H  = TINT2
    BTN2_FG = BLUE_D

    def _tint(bg, t=0.90):
        """把主色向白卡方向稀释，得到柔和的「贴片 / 底纹」色。"""
        return _mix(bg, CARD, t)

    # ---------------------------- ttk 主题 ----------------------------
    def _install_styles():
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass

        # 说明：这里曾尝试用 st.layout("TNotebook", [...]) 抽掉原生标签条，
        # 但在 clam / vista 下都会留下一条空白灰色标签，还会挤掉约 30px 纵向空间。
        # 现在导航由自绘 PillTabs 承担、内容区由自绘 TabStack 承担，
        # 已完全不再使用 ttk.Notebook。

        # 表格：白底、浅蓝表头、选中行浅蓝、无边框
        st.configure("Fresh.Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, font=FONT, rowheight=32, borderwidth=0, relief="flat")
        st.map("Fresh.Treeview",
               background=[("selected", SELBG)],
               foreground=[("selected", BLUE_D)])
        st.configure("Fresh.Treeview.Heading", background=TINT, foreground=BLUE_D,
                     font=FONT_B, relief="flat", borderwidth=0, padding=(12, 9))
        st.map("Fresh.Treeview.Heading", background=[("active", TINT2)])

        # 细滚动条：浅蓝滑槽 + 科技蓝滑块（圆角感由配色弱化边框实现）
        for _orient, nm in (("Vertical", "Slim.Vertical.TScrollbar"),
                            ("Horizontal", "Slim.Horizontal.TScrollbar")):
            st.configure(nm, gripcount=0, borderwidth=0, relief="flat",
                         background=TINT2, darkcolor=TINT, lightcolor=TINT,
                         troughcolor=PANEL, bordercolor=PANEL, arrowcolor=BLUE_D,
                         arrowsize=12, width=11)
            st.map(nm, background=[("active", BLUE_L), ("pressed", BLUE)])

        # 进度条
        st.configure("Fresh.Horizontal.TProgressbar", troughcolor=TINT, background=BLUE,
                     bordercolor=TINT, lightcolor=BLUE, darkcolor=BLUE,
                     thickness=8, borderwidth=0)

        # 下拉框
        st.configure("Fresh.TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=TEXT, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                     arrowcolor=BLUE_D, relief="flat", padding=(10, 6), font=FONT)
        st.map("Fresh.TCombobox",
               fieldbackground=[("readonly", CARD)],
               bordercolor=[("focus", BLUE)],
               arrowcolor=[("active", BLUE_D)])

    # ============================ 自绘组件 ============================
    class RoundedCard(tk.Frame):
        """白色大圆角卡片：柔和双层投影 + 1px 浅蓝描边 + 可选强调顶条。

        用法：card = RoundedCard(parent, title="标题")；子控件加到 card.body。
        """

        def __init__(self, parent, title=None, note=None, outer=None, bg=CARD,
                     radius=R, pad= PAD, shadow=True, accent=BLUE, top_accent=False):
            outer_bg = outer or _outer_bg(parent)
            super().__init__(parent, bg=outer_bg)
            self._bg, self._radius, self._shadow = bg, radius, shadow
            self._accent, self._top_accent = accent, top_accent
            self._cv = tk.Canvas(self, bg=outer_bg, highlightthickness=0, bd=0)
            self._cv.place(x=0, y=0, relwidth=1, relheight=1)
            self.body = tk.Frame(self, bg=bg)
            self.body.pack(fill="both", expand=True, padx=pad + 2, pady=pad + 2)
            self.head_right = None
            self.note_label = None
            if title:
                head = tk.Frame(self.body, bg=bg)
                head.pack(fill="x", pady=(0, SP["m"] + 2))
                bar = tk.Canvas(head, width=4, height=17, bg=bg,
                                highlightthickness=0, bd=0)
                bar.pack(side="left", padx=(0, 10))
                _round_rect(bar, 0, 0, 4, 17, 2, fill=accent, outline="")
                tk.Label(head, text=title, bg=bg, fg=INK,
                         font=FONT_ST).pack(side="left")
                if note:
                    # 句柄留出来，便于把这里的副标题当「动态说明」复用
                    self.note_label = tk.Label(head, text=note, bg=bg, fg=MUTED,
                                               font=FONT_XS)
                    self.note_label.pack(side="left", padx=(10, 0))
                self.head_right = tk.Frame(head, bg=bg)
                self.head_right.pack(side="right")
            self._cv.bind("<Configure>", self._redraw)

        def _redraw(self, _e=None):
            cv = self._cv
            cv.delete("bgshape")
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 10 or h < 10:
                return
            # 双层柔和投影：越外层越淡，营造「浮起」的轻盈感
            if self._shadow:
                _round_rect(cv, 4, 6, w - 3, h - 1, self._radius,
                            fill=SH2, outline="", tags="bgshape")
                _round_rect(cv, 3, 4, w - 4, h - 3, self._radius,
                            fill=SH1, outline="", tags="bgshape")
            _round_rect(cv, 2, 2, w - 5, h - 5, self._radius,
                        fill=self._bg, outline=LINE, width=1, tags="bgshape")
            if self._top_accent:
                # 顶部强调条：科技感点缀，仅露出圆角内的一小段
                _round_rect(cv, 2, 2, w - 5, 6, 3,
                            fill=self._accent, outline="", tags="bgshape")
            cv.tag_lower("bgshape")

    class StatTile(tk.Canvas):
        """数据贴片：浅蓝底圆角块 + 标签 + 大号数值（参考图的信息砖块语言）。"""

        def __init__(self, parent, label="", value="", unit="", fg=None, bg=None,
                     outer=None, width=170, height=76, accent=BLUE):
            outer_bg = outer or _outer_bg(parent)
            self._label, self._value, self._unit = label, value, unit
            self._fg = fg or INK
            self._bg = bg or _tint(accent, 0.92)
            self._accent = accent
            self.w_px, self._h = width, height
            super().__init__(parent, width=width, height=height, bg=outer_bg,
                             highlightthickness=0, bd=0)
            self.bind("<Configure>", lambda e: self._draw())
            self._draw()

        def set_value(self, value, unit=None):
            self._value = value
            if unit is not None:
                self._unit = unit
            self._draw()

        def _draw(self):
            self.delete("all")
            w = max(self.w_px, self.winfo_width())
            h = self._h
            _round_rect(self, 0, 0, w, h, R_INNER, fill=self._bg,
                        outline=_mix(self._accent, CARD, 0.80), width=1)
            # 左侧色条：极简科技感
            _round_rect(self, 0, 0, 3, h, 3, fill=self._accent, outline="")
            self.create_text(SP["l"], SP["m"] + 2, anchor="w", text=self._label,
                             fill=SUB, font=FONT_XS)
            vy = h - SP["l"] + 1
            self.create_text(SP["l"], vy, anchor="w", text=self._value,
                             fill=self._fg, font=FONT_NUM)
            if self._unit:
                # 单位与数值共享底线：数值按「垂直居中」锚定，其底边 = vy + 行高/2，
                # 单位用 anchor="sw" 把底边对到同一根线上，避免看起来像上标乱飞。
                vw = _measure(FONT_NUM, self._value)
                half = _font(FONT_NUM).metrics("linespace") / 2.0
                self.create_text(SP["l"] + vw + 3, vy + half, anchor="sw",
                                 text=self._unit, fill=self._fg, font=FONT_S)

    class PillTabs(tk.Frame):
        """药丸分段导航：取代 ttk.Notebook 的原生标签条，视觉统一到设计令牌。"""

        def __init__(self, parent, items, command=None, bg=None, outer=None):
            outer_bg = outer or _outer_bg(parent)
            super().__init__(parent, bg=outer_bg)
            self._bg = bg or outer_bg
            self._cmd = command
            self._items = []
            self._idx = 0
            for text in items:
                cv = tk.Canvas(self, height=36, bg=self._bg,
                               highlightthickness=0, bd=0, cursor="hand2")
                cv.w_px = _measure(FONT_B, text) + SP["xl"] + 12
                cv.configure(width=cv.w_px)
                cv.pack(side="left", padx=(0, SP["s"]))
                cv.bind("<Button-1>", lambda e, i=len(self._items): self.select(i))
                cv.bind("<Enter>", lambda e, i=len(self._items): self._hover(i, True))
                cv.bind("<Leave>", lambda e, i=len(self._items): self._hover(i, False))
                self._items.append({"cv": cv, "text": text, "hover": False})

        def select(self, idx, notify=True):
            self._idx = idx
            self.redraw()
            if notify and self._cmd:
                self._cmd(idx)

        def current(self):
            return self._idx

        def _hover(self, i, on):
            self._items[i]["hover"] = on
            self.redraw()

        def redraw(self):
            for i, it in enumerate(self._items):
                cv = it["cv"]
                cv.delete("all")
                w, h = cv.w_px, 36
                if i == self._idx:
                    _round_rect(cv, 0, 0, w, h, h / 2, fill=BLUE, outline=BLUE)
                    fg, fnt = "#FFFFFF", FONT_B
                elif it["hover"]:
                    _round_rect(cv, 0, 0, w, h, h / 2, fill=TINT, outline=TINT2)
                    fg, fnt = BLUE_D, FONT_B
                else:
                    _round_rect(cv, 0, 0, w, h, h / 2, fill=self._bg,
                                outline=LINE, width=1)
                    fg, fnt = SUB, FONT_B
                cv.create_text(w / 2, h / 2 + 1, text=it["text"], fill=fg, font=fnt)

    class Chip(tk.Canvas):
        """圆角小标签（用于受保护目录、类型标注等静态说明）。"""

        def __init__(self, parent, text, fg=SUB, bg=TINT, outer=None,
                     font=None, height=22, padx=12):
            outer_bg = outer or _outer_bg(parent)
            font = font or FONT_XS
            self._fg, self._bg, self._font = fg, bg, font
            self._h, self._padx, self._text = height, padx, text
            w = _measure(font, text) + padx * 2
            super().__init__(parent, width=w, height=height, bg=outer_bg,
                             highlightthickness=0, bd=0)
            self._draw()

        def _draw(self):
            self.delete("all")
            w = _measure(self._font, self._text) + self._padx * 2
            self.configure(width=w, height=self._h)
            _round_rect(self, 0, 0, w, self._h, self._h / 2,
                        fill=self._bg, outline=self._bg)
            self.create_text(w / 2, self._h / 2, text=self._text,
                             fill=self._fg, font=self._font)

        def set_text(self, text, fg=None, bg=None):
            self._text = text
            if fg:
                self._fg = fg
            if bg:
                self._bg = bg
            self._draw()

    class StatPill(tk.Canvas):
        """顶栏数据胶囊：前置小圆点 + 文本，可动态刷新。"""

        def __init__(self, parent, text="", fg=BLUE_D, bg=TINT, dot=BLUE,
                     outer=None, font=None, height=28, padx=14):
            outer_bg = outer or _outer_bg(parent)
            self._fg, self._bg, self._dot = fg, bg, dot
            self._font = font or FONT_S
            self._h, self._padx, self._text = height, padx, text
            super().__init__(parent, height=height, bg=outer_bg,
                             highlightthickness=0, bd=0)
            self._draw()

        def _draw(self):
            self.delete("all")
            tw = _measure(self._font, self._text)
            w = self._padx * 2 + tw + 12
            self.configure(width=w, height=self._h)
            _round_rect(self, 0, 0, w, self._h, self._h / 2,
                        fill=self._bg, outline=_mix(self._dot, CARD, 0.72), width=1)
            cy = self._h / 2
            self.create_oval(self._padx - 2, cy - 3.5, self._padx + 5, cy + 3.5,
                             fill=self._dot, outline="")
            self.create_text(self._padx + 12, cy, anchor="w", text=self._text,
                             fill=self._fg, font=self._font)

        def set_text(self, text):
            self._text = text
            self._draw()

    class PillButton(tk.Canvas):
        """药丸按钮：自动宽度、悬停加深、支持禁用态。"""

        def __init__(self, parent, text="", command=None, bg=BLUE, fg="#FFFFFF",
                     hover=None, width=None, height=34, font=None, outline=None,
                     padx=18, state="normal", outer=None):
            outer_bg = outer or _outer_bg(parent)
            self._font = font or FONT_B
            self._text = text
            self._auto_w = width is None
            self._pad = padx
            self.w_px = width or (_measure(self._font, text) + padx * 2)
            self._h = height
            self._r = height // 2
            self._bg0, self._fg0 = bg, fg
            self._hover = hover or _darken(bg, 0.10)
            self._outline = outline
            self._cmd = command
            self._state = state
            self._hovering = False
            super().__init__(parent, width=self.w_px, height=self._h, bg=outer_bg,
                             highlightthickness=0, bd=0,
                             cursor="hand2" if state == "normal" else "arrow")
            self._draw()
            self.bind("<Enter>", self._on_enter)
            self.bind("<Leave>", self._on_leave)
            self.bind("<Button-1>", self._on_press)
            self.bind("<ButtonRelease-1>", self._on_release)

        def _draw(self):
            self.delete("all")
            if self._state == "disabled":
                fill, fg = TINT, MUTED
            elif self._hovering:
                fill, fg = self._hover, self._fg0
            else:
                fill, fg = self._bg0, self._fg0
            _round_rect(self, 1, 1, self.w_px - 1, self._h - 1, self._r,
                        fill=fill, outline=self._outline or fill)
            self.create_text(self.w_px / 2, self._h / 2 + 1, text=self._text,
                             fill=fg, font=self._font)

        def set_colors(self, bg, fg, hover=None):
            self._bg0, self._fg0 = bg, fg
            self._hover = hover or _darken(bg, 0.10)
            self._draw()

        def _on_enter(self, _e):
            self._hovering = True
            self._draw()

        def _on_leave(self, _e):
            self._hovering = False
            self._draw()

        def _on_press(self, _e):
            if self._state == "normal":
                self.configure(cursor="hand2")

        def _on_release(self, _e):
            if self._state == "normal" and self._cmd:
                self._cmd()

        def config(self, **kw):
            if "state" in kw:
                self._state = kw.pop("state")
                super().configure(cursor="hand2" if self._state == "normal" else "arrow")
            if "text" in kw:
                self._text = kw.pop("text")
                if self._auto_w:
                    self.w_px = _measure(self._font, self._text) + self._pad * 2
                    super().configure(width=self.w_px)
            if "bg" in kw:
                self._bg0 = kw.pop("bg")
                self._hover = _darken(self._bg0, 0.10)
            if "fg" in kw:
                self._fg0 = kw.pop("fg")
            if kw:
                super().configure(**kw)
            self._draw()

        configure = config

    class FreshCheck(tk.Canvas):
        """圆角勾选框：左侧科技蓝圆角方块 + 白勾，右侧主文本 + 右对齐灰色备注。

        inline=True 时宽度自适应内容（用于并排的选项）；否则撑满可用宽度。
        """

        def __init__(self, parent, text="", note="", variable=None, command=None,
                     outer=None, font=None, fg=TEXT, box=18, height=32, inline=False):
            outer_bg = outer or _outer_bg(parent)
            self._font = font or FONT
            self._text, self._note = text, note
            self._var = variable if variable is not None else tk.BooleanVar(value=False)
            self._cmd = command
            self._on = bool(self._var.get())
            self._box, self._h = box, height
            self._fg = fg
            self._inline = inline
            self._hovering = False
            # 内边距统一定义：绘制落笔位置由 _content_w() 反推，两者共用同一组常量，
            # 否则「宽度按 12px 间距算、文字却从 19px 处开始画」，尾部就会被裁掉。
            self._lpad, self._gap, self._rpad = 8, 11, 8
            w = self._content_w()
            super().__init__(parent, width=w, height=height, bg=outer_bg,
                             highlightthickness=0, bd=0, cursor="hand2")
            self._var.trace_add("write", lambda *a: self._sync())
            self.bind("<Button-1>", self._toggle)
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)
            self.bind("<Configure>", lambda e: self._draw())
            self._draw()

        def _content_w(self):
            """内容所需的最小宽度（也是 _draw 的取宽依据）。"""
            w = (self._lpad + self._box + self._gap
                 + _measure(self._font, self._text) + self._rpad)
            if self._note:
                w += _measure(FONT_S, self._note) + SP["l"]
            return w

        def _draw(self):
            self.delete("all")
            w = (max(self.winfo_width(), self.winfo_reqwidth(), self._content_w())
                 if not self._inline else
                 max(self.winfo_reqwidth(), self._content_w()))
            h = self._h
            # 悬停：圆角浅蓝底，形成「整行可点」的暗示
            if self._hovering:
                _round_rect(self, 0, 1, w, h - 1, R_INNER, fill=TINT, outline="")
            elif self._on:
                _round_rect(self, 0, 1, w, h - 1, R_INNER, fill=_tint(BLUE, 0.95),
                            outline="")
            bx, by, bs = self._lpad, (h - self._box) / 2, self._box
            if self._on:
                _round_rect(self, bx, by, bx + bs, by + bs, 5.5,
                            fill=BLUE, outline=BLUE)
                self.create_line(bx + bs * 0.26, by + bs * 0.53,
                                 bx + bs * 0.44, by + bs * 0.72,
                                 fill="#FFFFFF", width=2, capstyle="round")
                self.create_line(bx + bs * 0.44, by + bs * 0.72,
                                 bx + bs * 0.76, by + bs * 0.30,
                                 fill="#FFFFFF", width=2, capstyle="round")
            else:
                _round_rect(self, bx + 1, by + 1, bx + bs - 1, by + bs - 1, 5,
                            fill=CARD, outline=TINT2, width=2)
            self.create_text(bx + bs + self._gap, h / 2, anchor="w", text=self._text,
                             fill=self._fg if self._on else SUB,
                             font=self._font)
            if self._note:
                self.create_text(w - self._rpad, h / 2, anchor="e", text=self._note,
                                 fill=MUTED, font=FONT_S)

        def _toggle(self, _e=None):
            self._var.set(not bool(self._var.get()))
            if self._cmd:
                self._cmd()

        def _sync(self):
            self._on = bool(self._var.get())
            self._draw()

        def _enter(self, _e):
            self._hovering = True
            self._draw()

        def _leave(self, _e):
            self._hovering = False
            self._draw()

    class CheckRow(tk.Canvas):
        """全宽可勾选行：左侧圆角勾选框 + 分类名 + 类型胶囊，右侧项数与大小。

        用于磁盘清理列表，取代原生 Checkbutton，保证与卡片语言一致。
        """

        def __init__(self, parent, name, count=0, size="", kind="", variable=None,
                     command=None, outer=None, height=42):
            super().__init__(parent, height=height, bg=outer or _outer_bg(parent),
                             highlightthickness=0, bd=0, cursor="hand2")
            self.row_name, self._count, self._size = name, count, size
            self._kind = kind
            self._h = height
            self._var = variable
            self._cmd = command
            self._hovering = False
            self._on = bool(self._var.get())
            self.bind("<Button-1>", self._toggle)
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)
            self.bind("<Configure>", lambda e: self._draw())
            self._var.trace_add("write", lambda *a: self._sync())

        def _toggle(self, _e=None):
            self._var.set(not bool(self._var.get()))
            if self._cmd:
                self._cmd()

        def _sync(self):
            self._on = bool(self._var.get())
            self._draw()

        def _enter(self, _e):
            self._hovering = True
            self._draw()

        def _leave(self, _e):
            self._hovering = False
            self._draw()

        def _draw(self):
            self.delete("all")
            w = max(self.winfo_width(), 200)
            h = self._h
            if self._hovering:
                _round_rect(self, 0, 1, w, h - 1, R_INNER, fill=TINT, outline="")
            elif self._on:
                _round_rect(self, 0, 1, w, h - 1, R_INNER,
                            fill=_tint(BLUE, 0.95), outline="")
            bx, bs = SP["m"], 18
            by = (h - bs) / 2
            if self._on:
                _round_rect(self, bx, by, bx + bs, by + bs, 5.5,
                            fill=BLUE, outline=BLUE)
                self.create_line(bx + bs * 0.26, by + bs * 0.53,
                                 bx + bs * 0.44, by + bs * 0.72,
                                 fill="#FFFFFF", width=2, capstyle="round")
                self.create_line(bx + bs * 0.44, by + bs * 0.72,
                                 bx + bs * 0.76, by + bs * 0.30,
                                 fill="#FFFFFF", width=2, capstyle="round")
            else:
                _round_rect(self, bx + 1, by + 1, bx + bs - 1, by + bs - 1, 5,
                            fill=CARD, outline=TINT2, width=2)

            # ---------- 先给右侧固定列（项数 / 大小）算出宽度，再决定名字能用多少 ----------
            # 分类名长短差别极大（"%TEMP%" vs "%LOCALAPPDATA%\Microsoft\Windows\INetCache"），
            # 不按像素预算就会溢出，压在右侧数字上。
            cnt_txt = "%d 项" % self._count
            rw = max(_measure(FONT_XS, cnt_txt), _measure(FONT_B, self._size))
            rx = w - SP["m"]
            tx = bx + bs + SP["m"]
            gap = SP["l"]
            kw = _measure(FONT_XS, self._kind) + 14 if self._kind else 0
            avail = rx - rw - gap - tx
            if kw:
                avail -= kw + SP["s"] + 2
            name = _ellipsize(FONT, self.row_name, avail)
            self.create_text(tx, h / 2, anchor="w", text=name,
                             fill=(INK if self._on else TEXT), font=FONT)
            if self._kind:
                cx = tx + _measure(FONT, name) + SP["s"] + 2
                if cx + kw <= rx - gap:      # 极窄宽度下宁可不画胶囊，也不越界
                    _round_rect(self, cx, h / 2 - 9, cx + kw, h / 2 + 9, 9,
                                fill=(_tint(CYAN, 0.86) if self._kind == "应用缓存"
                                      else _tint(BLUE, 0.88)), outline="")
                    self.create_text(cx + kw / 2, h / 2, text=self._kind,
                                     fill=(CYAN if self._kind == "应用缓存"
                                           else BLUE_D), font=FONT_XS)
            # 右侧：项数 · 大小（大小用加粗字强调）
            self.create_text(rx, h / 2 - 7, anchor="e", text=cnt_txt, fill=MUTED,
                             font=FONT_XS)
            self.create_text(rx, h / 2 + 8, anchor="e", text=self._size,
                             fill=BLUE_D, font=FONT_B)

    class RoundedField(tk.Canvas):
        """圆角输入框：Canvas 画圆角底 + 内嵌 Entry，聚焦时描边变主色。"""

        def __init__(self, parent, textvariable=None, width=12, font=None,
                     outer=None, height=34, placeholder="", card=CARD):
            outer_bg = outer or _outer_bg(parent)
            self._h = height
            self._card = card
            self.w_px = max(60, _measure(font or FONT, "0") * width + SP["l"] * 2)
            super().__init__(parent, width=self.w_px, height=height, bg=outer_bg,
                             highlightthickness=0, bd=0)
            self.entry = tk.Entry(self, textvariable=textvariable, font=font or FONT,
                                  relief="flat", bd=0, bg=card, fg=TEXT,
                                  insertbackground=BLUE, highlightthickness=0)
            self._ph = placeholder
            # 仅当未绑定 textvariable 时才启用占位符：否则写入 Entry 会污染配置变量
            self._ph_ok = bool(placeholder) and textvariable is None
            self._ph_on = False
            self._focus = False
            self.entry.place(x=SP["m"] + 1, y=height / 2, anchor="w",
                             width=self.w_px - SP["m"] * 2 - 2, height=height - 12)
            if self._ph_ok and not self.entry.get():
                self._show_ph()
            self.entry.bind("<FocusIn>", self._on_focus)
            self.entry.bind("<FocusOut>", self._on_blur)
            self.bind("<Button-1>", lambda e: self.entry.focus_set())
            self.bind("<Configure>", self._redraw)
            self._redraw()

        def _show_ph(self):
            self._ph_on = True
            self.entry.configure(fg=MUTED)
            self.entry.insert(0, self._ph)

        def _clear_ph(self):
            if self._ph_on:
                self._ph_on = False
                self.entry.delete(0, "end")
                self.entry.configure(fg=TEXT)

        def _on_focus(self, _e=None):
            self._clear_ph()
            self._focus = True
            self._redraw()

        def _on_blur(self, _e=None):
            if self._ph_ok and not self.entry.get():
                self._show_ph()
            self._focus = False
            self._redraw()

        def get(self):
            return "" if self._ph_on else self.entry.get()

        def delete(self, a, b=None):
            self._ph_on = False
            self.entry.configure(fg=TEXT)
            self.entry.delete(a, b if b is not None else "end")

        def insert(self, i, s):
            self.entry.insert(i, s)

        def bind(self, seq, fn, add=None):
            # 画布自身的 <Configure>/<Button-1> 走 Canvas，其余（按键等）转发给内层 Entry
            if getattr(self, "entry", None) is not None and seq not in ("<Configure>", "<Button-1>"):
                return self.entry.bind(seq, fn, add)
            return super().bind(seq, fn, add)

        def _redraw(self, _e=None):
            # 注意：self.delete 已被转发给内层 Entry，画布自身必须走父类
            tk.Canvas.delete(self, "bgshape")
            w = max(self.winfo_width(), self.w_px)
            h = self._h
            outline = BLUE if self._focus else LINE
            _round_rect(self, 1, 1, w - 2, h - 2, R_FIELD, fill=self._card,
                        outline=outline, width=1, tags="bgshape")
            self.tag_lower("bgshape")

    # ---------------------------- 工厂函数 ----------------------------
    def _btn_px(font_spec, text, w, padx):
        """按钮宽度（像素）：取「文字实测宽 + 左右内边距」与「w 个字宽 + 内边距」的较大者。

        w 是「最小宽度」的**字符数**语义，只用于让同排按钮看起来对齐，
        绝不是像素值：早期直接把它当像素传给 Canvas 的 width，
        w=13 就让「一键清理」只剩 13px 宽，四个字被压成一团。
        """
        base = _measure(font_spec, text) + padx * 2
        if w is None:
            return base
        return int(max(base, _measure(font_spec, "汉") * w + padx))

    def mk_btn(parent, text, cmd, bg=BLUE, fg="#FFFFFF", w=None, height=36,
               font=None, outline=None, padx=SP["l"] + 4, hover=None):
        """主按钮工厂。次级按钮统一用 bg=BTN2, fg=BTN2_FG, hover=BTN2_H。"""
        fs = font or FONT_B
        return PillButton(parent, text=text, command=cmd, bg=bg, fg=fg,
                          hover=hover, height=height, font=fs, outline=outline,
                          width=_btn_px(fs, text, w, padx), padx=padx,
                          outer=_outer_bg(parent))

    def mk_btn2(parent, text, cmd, w=None, height=36, font=None):
        """次级按钮：浅蓝底 + 深蓝字，全界面统一。"""
        return mk_btn(parent, text, cmd, bg=BTN2, fg=BTN2_FG, hover=BTN2_H,
                      w=w, height=height, font=font, outline=TINT2)

    def mk_card(parent, title=None, **kw):
        return RoundedCard(parent, title=title, outer=_outer_bg(parent), **kw)

    def mk_field(parent, textvariable=None, width=10, font=None, placeholder=""):
        return RoundedField(parent, textvariable=textvariable, width=width,
                            font=font, outer=_outer_bg(parent),
                            placeholder=placeholder)

    def mk_check(parent, text, variable=None, command=None, note="", inline=True):
        """统一样式的勾选框（内联小尺寸）。"""
        return FreshCheck(parent, text=text, variable=variable, command=command,
                          note=note, inline=inline, outer=_outer_bg(parent))

    def mk_list(parent, height=None):
        """统一样式的列表：内嵌面板底 + 圆角描边包裹 + 细滚动条。"""
        wrap = tk.Frame(parent, bg=LINE)
        inner = tk.Frame(wrap, bg=PANEL)
        lb = tk.Listbox(inner, font=FONT, bd=0, relief="flat",
                        highlightthickness=0, activestyle="none",
                        bg=PANEL, fg=TEXT, selectbackground=SELBG,
                        selectforeground=BLUE_D)
        if height:
            lb.configure(height=height)
        sb = ttk.Scrollbar(inner, orient="vertical",
                           style="Slim.Vertical.TScrollbar", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return wrap, lb

    class TabStack(tk.Frame):
        """无标签条的页面栈：所有页 place 叠放，show(i) 用 tkraise 切换。

        取代 ttk.Notebook。原生 Notebook 的标签条在不同 ttk 主题下都无法可靠
        隐藏：即使把 TNotebook 的 layout 覆盖成只剩 client，实际渲染仍会留下
        一条空白灰标签，既破坏「科技蓝 · 小清新」的视觉，又白吃掉约 30px
        纵向空间，把底部的操作按钮行顶出窗口。
        """

        def __init__(self, parent, bg=PAGE):
            super().__init__(parent, bg=bg)
            self._pages = []
            self._cur = 0

        def add(self, page):
            page.place(x=0, y=0, relwidth=1, relheight=1)
            self._pages.append(page)
            return page

        def show(self, idx):
            if 0 <= idx < len(self._pages):
                self._cur = idx
                self._pages[idx].tkraise()

        def current(self):
            return self._cur

        def pages(self):
            return list(self._pages)

    class App:
        def __init__(self, root):
            self.root = root
            root.title(APP_TITLE)
            # 窗口尺寸自适应屏幕：设计基准 1040x840，但屏幕装不下时按可视区收窄。
            # 1366x768 / 1536x864 这类笔记本分辨率上是常态，硬编码 840 会让底部
            # 操作区（全选 / 一键清理）和状态栏被屏幕下边缘裁掉。
            _sw = root.winfo_screenwidth()
            _sh = root.winfo_screenheight()
            win_w = max(880, min(1040, _sw - 80))
            win_h = max(560, min(840, _sh - 130))
            root.geometry("%dx%d" % (win_w, win_h))
            root.minsize(min(960, win_w), min(600, win_h))
            root.configure(bg=BG)
            _center(root, win_w, win_h)
            self.ini = writable_ini()
            self.cfg = parse_config(self.ini)
            self.categories = []
            self.cat_vars = {}
            self.recycle = None
            self.recycle_var = None
            self._build()
            self.root.after(200, self.scan_disk)

        # ---------------- 顶层布局 ----------------
        def _build(self):
            # ---------- 顶栏：品牌标 + 标题 + 数据胶囊 ----------
            head = tk.Frame(self.root, bg=BG)
            head.pack(fill="x", padx=SP["xl"], pady=(SP["l"], SP["s"]))
            self._logo(head)
            left = tk.Frame(head, bg=BG)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text="C 盘安全清理工具", bg=BG, fg=INK,
                     font=FONT_T).pack(anchor="w")
            tk.Label(left, text="白名单扫描 · 删除前二次越界校验 · 受保护目录仅展示；"
                                "所有删除与结束进程动作均需二次确认。",
                     bg=BG, fg=SUB, font=FONT_S).pack(anchor="w", pady=(3, 0))
            pills = tk.Frame(head, bg=BG)
            pills.pack(side="right", anchor="e")
            self.pill_disk = StatPill(pills, text="C: 读取中…", outer=BG)
            self.pill_disk.pack(side="right")
            self.pill_mem = StatPill(pills, text="内存 读取中…", fg=CYAN,
                                     bg=_tint(CYAN, 0.90), dot=CYAN, outer=BG)
            self.pill_mem.pack(side="right", padx=(0, SP["s"]))
            self._refresh_disk()

            # ---------- 药丸分段导航 ----------
            navwrap = tk.Frame(self.root, bg=BG)
            navwrap.pack(fill="x", padx=SP["xl"] - 4, pady=(SP["s"], SP["m"]))
            self.tabs = PillTabs(navwrap,
                                 ["磁盘清理", "大文件扫描", "内存与进程",
                                  "自动清理", "排除设置"],
                                 command=self._on_tab, outer=BG)
            self.tabs.pack(side="left")
            self.tabs.redraw()

            # ---------- 状态栏 ----------
            # 放在内容区之前 pack(side="bottom")：packer 按调用顺序分配空间，
            # 先登记的控件优先拿到自己的请求高度，后登记的（弹性内容区）拿剩余。
            # 反之状态栏会被内容区挤掉。
            sbar = tk.Frame(self.root, bg=BG)
            sbar.pack(side="bottom", fill="x", padx=SP["xl"], pady=(0, SP["m"]))
            tk.Canvas(sbar, height=1, bg=LINE, highlightthickness=0).pack(fill="x",
                                                                         pady=(0, SP["s"]))
            self.status = tk.Label(sbar, text="就绪", bg=BG, fg=SUB,
                                   font=FONT_S, anchor="w")
            self.status.pack(fill="x")

            # ---------- 内容区（自绘页面栈，无原生标签条）----------
            self.stack = TabStack(self.root, bg=PAGE)
            self.stack.pack(fill="both", expand=True, padx=SP["m"], pady=(0, SP["s"]))
            self.tab_disk = tk.Frame(self.stack, bg=PAGE)
            self.tab_big = tk.Frame(self.stack, bg=PAGE)
            self.tab_mem = tk.Frame(self.stack, bg=PAGE)
            self.tab_auto = tk.Frame(self.stack, bg=PAGE)
            self.tab_excl = tk.Frame(self.stack, bg=PAGE)
            for t in (self.tab_disk, self.tab_big, self.tab_mem,
                      self.tab_auto, self.tab_excl):
                self.stack.add(t)
            self._build_disk_tab()
            self._build_big_tab()
            self._build_mem_tab()
            self._build_auto_tab()
            self._build_excl_tab()
            self.stack.show(0)

        def _logo(self, parent):
            """品牌标：圆角蓝方块 + 白色 C 标识 + 青色小点（纯 Canvas 绘制）。"""
            cv = tk.Canvas(parent, width=44, height=44, bg=BG,
                           highlightthickness=0, bd=0)
            cv.pack(side="left", padx=(0, SP["m"]))
            _round_rect(cv, 0, 0, 42, 42, 12, fill=BLUE, outline="")
            _round_rect(cv, 2, 2, 40, 20, 10, fill=BLUE_L, outline="")
            _round_rect(cv, 2, 12, 40, 40, 12, fill=BLUE, outline="")
            cv.create_text(21, 22, text="C", fill="#FFFFFF",
                           font=("Microsoft YaHei UI", 17, "bold"))
            cv.create_oval(30, 30, 38, 38, fill=CYAN, outline="#FFFFFF", width=1.5)

        def _on_tab(self, idx):
            try:
                self.stack.show(idx)
            except Exception:
                pass
            if idx == 2:
                self.refresh_procs()

        def _refresh_disk(self):
            f, t = get_disk_free("C")
            mem = get_memory_info()
            self.pill_disk.set_text("C: 可用 %s / %s" % (fmt_size(f), fmt_size(t)))
            self.pill_mem.set_text("内存 %d%% · 已用 %s"
                                   % (mem["load"], fmt_size(mem["used"])))

        def set_status(self, s):
            self.status.config(text=s)

        # ==================== Tab 1：磁盘清理 ====================
        def _build_disk_tab(self):
            # 底部操作区优先 pack（side="bottom"）：packer 先分配的空间不会被
            # 后续控件抢走。若放在最后 pack，窗口变矮时这一行会被压扁到
            # 只剩一半高度，按钮文字被裁掉。
            foot = mk_card(self.tab_disk, None)
            foot.pack(side="bottom", fill="x", padx=SP["m"],
                      pady=(SP["s"], SP["m"]))
            self.foot_body = foot.body
            frow = tk.Frame(foot.body, bg=CARD)
            frow.pack(fill="x")
            self.sel_label = tk.Label(frow, text="已选：0 项 · 预计释放 0 B",
                                      bg=CARD, fg=INK, font=FONT_B)
            self.sel_label.pack(side="left")
            btns = tk.Frame(frow, bg=CARD)
            btns.pack(side="right")
            # 统一用 side="right" 依次右贴，视觉顺序即 全选 → 全不选 → 刷新 → 一键清理
            self.btn_clean = mk_btn(btns, "一键清理", self.on_clean, bg=DANGER, w=13)
            self.btn_clean.pack(side="right")
            # 三个次级按钮统一 w=6 取等宽，避免「全选」比「全不选」短一截的参差感
            mk_btn2(btns, "刷新", self.scan_disk, w=6).pack(
                side="right", padx=(0, SP["s"]))
            mk_btn2(btns, "全不选", lambda: self._set_all(False), w=6).pack(
                side="right", padx=(0, SP["s"]))
            mk_btn2(btns, "全选", lambda: self._set_all(True), w=6).pack(
                side="right", padx=(0, SP["s"]))
            self.progress = ttk.Progressbar(foot.body, mode="indeterminate",
                                            style="Fresh.Horizontal.TProgressbar")

            # ---------- 顶部统计贴片行 ----------
            tiles = tk.Frame(self.tab_disk, bg=PAGE)
            tiles.pack(fill="x", padx=SP["m"], pady=(SP["m"], SP["s"]))
            self.tile_total = StatTile(tiles, label="可清理合计", value="扫描中",
                                       unit="", outer=PAGE, width=210)
            self.tile_total.pack(side="left")
            self.tile_recycle = StatTile(tiles, label="回收站", value="—", unit="",
                                         accent=CYAN, outer=PAGE, width=180)
            self.tile_recycle.pack(side="left", padx=(SP["m"], 0))
            self.tile_sel = StatTile(tiles, label="已选 · 预计释放", value="0 B",
                                     accent=BLUE_D, outer=PAGE, width=210)
            self.tile_sel.pack(side="left", padx=(SP["m"], 0))
            tip = tk.Frame(tiles, bg=PAGE)
            tip.pack(side="right", anchor="e")
            tk.Label(tip, text="勾选要清理的项目 → 点「一键清理」\n受保护的系统目录不会出现在左侧列表",
                     bg=PAGE, fg=MUTED, font=FONT_XS, justify="right").pack(anchor="e")

            body = tk.Frame(self.tab_disk, bg=PAGE)
            body.pack(fill="both", expand=True, padx=SP["m"], pady=SP["s"])
            left = mk_card(body, "可清理项目", top_accent=True)
            left.pack(side="left", fill="both", expand=True)
            self.canvas = tk.Canvas(left.body, bg=CARD, highlightthickness=0)
            vbar = ttk.Scrollbar(left.body, orient="vertical", style="Slim.Vertical.TScrollbar",
                                 command=self.canvas.yview)
            self.inner = tk.Frame(self.canvas, bg=CARD)
            self.inner.bind("<Configure>",
                            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
            self._win_item = self.canvas.create_window((0, 0), window=self.inner,
                                                       anchor="nw")
            # 让内层 Frame 跟随画布宽度：create_window 默认用窗口「请求宽度」
            # （即 Canvas 控件的 378px 默认宽），会让行只占画布左侧 2/3，
            # 右侧留出一大片死区、右对齐的数字也贴不到边上。
            self.canvas.bind(
                "<Configure>",
                lambda e: self.canvas.itemconfigure(self._win_item, width=e.width))
            self.canvas.configure(yscrollcommand=vbar.set)
            self.canvas.pack(side="left", fill="both", expand=True)
            vbar.pack(side="right", fill="y")

            right = mk_card(body, "占用排行 TOP 8")
            right.pack(side="right", fill="y", padx=(SP["m"], 0))
            # 注意：画布用 fill/expand 跟随卡片，而不是写死 height=400。
            # 写死高度时右侧卡片会向上「顶」出一份超过可用空间的请求，
            # 把底部的操作按钮行和状态栏整条挤出窗口。
            self.rank = tk.Canvas(right.body, bg=CARD, width=300, height=200,
                                  highlightthickness=0)
            self._rank_cats = []
            self.rank.pack(fill="both", expand=True, padx=SP["s"], pady=SP["s"])
            self.rank.bind("<Configure>", self._on_rank_resize)


        def _set_progress(self, running):
            # 进度条只在运行时占一行，平时隐藏 —— 常驻会白占 14px 纵向空间
            if running:
                self.progress.pack(fill="x", pady=(SP["s"], 0))
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()

        def scan_disk(self):
            self.set_status("正在扫描临时文件与应用缓存...")
            self.btn_clean.config(state="disabled")
            self._set_progress(True)
            threading.Thread(target=self._scan_worker, daemon=True).start()

        def _scan_worker(self):
            cats = build_categories(self.cfg)
            rb = None if self.cfg["skip_recycle"] else get_recycle_bin()
            self.root.after(0, lambda: self._on_scanned(cats, rb))

        def _on_scanned(self, cats, rb):
            self._set_progress(False)
            self.categories = cats
            self.recycle = rb
            for w in self.inner.winfo_children():
                w.destroy()
            self.cat_vars = {}
            self.recycle_var = None
            if any(c["count"] > 0 for c in cats) or (rb and rb["count"] > 0):
                for c in cats:
                    if c["count"] == 0:
                        continue
                    var = tk.BooleanVar(value=True)
                    kind = "应用缓存" if c.get("kind") == "app" else "系统临时"
                    row = CheckRow(self.inner, name=c["name"], count=c["count"],
                                   size=fmt_size(c["total"]), kind=kind,
                                   variable=var, command=self._update_sel,
                                   outer=CARD)
                    row.pack(fill="x", pady=1)
                    self.cat_vars[c["name"]] = (var, c)
                if rb and rb["count"] > 0:
                    self.recycle_var = tk.BooleanVar(value=True)
                    CheckRow(self.inner, name="回收站", count=rb["count"],
                             size=fmt_size(rb["size"]), kind="系统临时",
                             variable=self.recycle_var, command=self._update_sel,
                             outer=CARD).pack(fill="x", pady=1)
            else:
                empty = tk.Frame(self.inner, bg=CARD)
                empty.pack(fill="x", pady=SP["xl"])
                tk.Label(empty, text="✨　未发现可清理的临时 / 缓存文件，C 盘很干净",
                         bg=CARD, fg=SUCCESS, font=FONT_B).pack()
            tot = sum(c["total"] for c in cats)
            cnt = sum(c["count"] for c in cats)
            self.tile_total.set_value(fmt_size(tot), unit="/ %d 项" % cnt)
            if rb and rb["count"] > 0:
                self.tile_recycle.set_value(fmt_size(rb["size"]),
                                            unit="/ %d 项" % rb["count"])
            else:
                self.tile_recycle.set_value("空", unit="")
            self.set_status("扫描完成：%d 个文件可清理%s" % (cnt, "，回收站 %d 项" % rb["count"] if rb else ""))
            self._rank_cats = cats
            self._draw_rank(cats)
            self.btn_clean.config(state="normal")
            self._update_sel()

        def _on_rank_resize(self, _e=None):
            self._draw_rank(self._rank_cats)

        def _draw_rank(self, cats):
            """TOP 8 占用排行：浅蓝轨道 + 圆角数据条 + 数值标注。

            完全按画布「实际」宽高排布，卡片随窗口缩放时既不溢出也不留白；
            标签按像素截断，避免长路径把右侧数值压住。
            """
            self.rank.delete("all")
            cw = max(self.rank.winfo_width(), 240)
            ch = max(self.rank.winfo_height(), 160)
            items = sorted([c for c in cats if c["total"] > 0],
                           key=lambda x: -x["total"])[:8]
            if not items:
                self.rank.create_text(SP["m"], 22, anchor="w", font=FONT_S,
                                      fill=MUTED, text="暂无可视化数据")
                return
            mx = items[0]["total"] or 1
            pad = SP["m"]
            track_w = cw - pad * 2
            lh = _font(FONT_XS).metrics("linespace")
            bar_h = 8
            # 把可用高度均分给 8 行，窗口变矮时自动收紧而不是被裁掉
            step = max(lh + bar_h + 4, (ch - pad) / len(items))
            for i, c in enumerate(items):
                top = pad + step * i
                val = fmt_size(c["total"])
                vw = _measure(FONT_XS, val)
                nm = c["name"].replace("系统临时: ", "").replace("应用缓存: ", "")
                nm = _ellipsize(FONT_XS, nm, track_w - vw - SP["l"])
                ty = top + lh / 2.0
                self.rank.create_text(pad, ty, anchor="w", font=FONT_XS,
                                      fill=SUB, text=nm)
                self.rank.create_text(cw - pad, ty, anchor="e", font=FONT_XS,
                                      fill=BLUE_D, text=val)
                # 轨道 + 数据条（首名用青色点缀，形成视觉焦点）
                by0 = ty + lh / 2.0 + 3
                _round_rect(self.rank, pad, by0, pad + track_w, by0 + bar_h,
                            bar_h / 2, fill=GRID, outline="")
                bw = max(bar_h, c["total"] / mx * track_w)
                col = CYAN if i == 0 else BLUE
                _round_rect(self.rank, pad, by0, pad + bw, by0 + bar_h,
                            bar_h / 2, fill=col, outline="")


        def _set_all(self, val):
            for var, _ in self.cat_vars.values():
                var.set(val)
            if self.recycle_var:
                self.recycle_var.set(val)
            self._update_sel()

        def _selected_cats(self):
            return [c for _n, (var, c) in self.cat_vars.items() if var.get()]

        def _update_sel(self):
            cats = self._selected_cats()
            cnt = sum(c["count"] for c in cats)
            sz = sum(c["total"] for c in cats)
            if self.recycle_var and self.recycle_var.get() and self.recycle:
                cnt += self.recycle["count"]
                sz += self.recycle["size"]
            self.sel_label.config(text="已选：%d 项 · 预计释放 %s" % (cnt, fmt_size(sz)))
            self.tile_sel.set_value(fmt_size(sz), unit="/ %d 项" % cnt)

        def on_clean(self):
            selected = self._selected_cats()
            rb_on = bool(self.recycle_var and self.recycle_var.get() and self.recycle)
            if not selected and not rb_on:
                messagebox.showinfo("提示", "请先勾选要清理的项目。")
                return
            if not self._confirm_clean(selected, self.recycle if rb_on else None):
                return
            self.btn_clean.config(state="disabled")
            self._set_progress(True)
            self.set_status("正在清理...")
            allowed = [c["root"] for c in self.categories]
            threading.Thread(target=self._clean_worker,
                             args=(selected, allowed, rb_on), daemon=True).start()

        def _clean_worker(self, selected, allowed, rb_on):
            before = get_disk_free("C")
            per_cat = []
            deleted = freed = failed = 0
            total_files = sum(len(c["files"]) for c in selected)
            done = 0
            for c in selected:
                d, f, fl = delete_files(c["files"], allowed)
                per_cat.append((c["name"], d, f, fl))
                deleted += d
                freed += f
                failed += fl
                done += len(c["files"])
                self.root.after(0, lambda n=done: self.set_status(
                    "已处理 %d/%d 个文件..." % (min(n, total_files), total_files)))
            clean_empty_dirs(allowed)
            rb_count = rb_size = 0
            if rb_on:
                self.root.after(0, lambda: self.set_status("正在清空回收站..."))
                info = get_recycle_bin()
                empty_recycle_bin()
                rb_count = info.get("count", 0)
                rb_size = info.get("size", 0)
                deleted += rb_count
                freed += rb_size
            after = get_disk_free("C")
            rep = {"before": before, "after": after, "deleted": deleted, "freed": freed,
                   "failed": failed, "per_cat": per_cat, "rb_count": rb_count, "rb_size": rb_size}
            self.root.after(0, lambda: self._on_cleaned(rep))

        def _confirm_clean(self, cats, rb):
            dlg = tk.Toplevel(self.root)
            dlg.title("确认清理")
            dlg.configure(bg=PAGE)
            dlg.transient(self.root)
            dlg.grab_set()
            _center(dlg, 560, 500)

            card = mk_card(dlg, top_accent=True)
            card.pack(fill="both", expand=True, padx=SP["l"], pady=SP["l"])
            body = card.body

            # 警告条：柔和红底圆角块 + 图标化标题
            strip = tk.Canvas(body, height=54, bg=CARD, highlightthickness=0)
            strip.pack(fill="x", pady=(0, SP["m"]))
            strip.bind("<Configure>", lambda e, cv=strip: self._warn_strip(cv))
            tk.Label(body, text="仅删除白名单目录内的文件；删除前每个路径都会再做一次越界校验。",
                     bg=CARD, fg=SUB, font=FONT_S).pack(anchor="w")

            holder = tk.Frame(body, bg=LINE)
            holder.pack(fill="both", expand=True, pady=SP["m"])
            lb = tk.Listbox(holder, font=FONT, bd=0, relief="flat",
                            highlightthickness=0, activestyle="none",
                            bg=PANEL, fg=TEXT, selectbackground=SELBG,
                            selectforeground=BLUE_D)
            lb.pack(fill="both", expand=True, padx=1, pady=1)
            lsb = ttk.Scrollbar(holder, orient="vertical",
                                style="Slim.Vertical.TScrollbar", command=lb.yview)
            lb.configure(yscrollcommand=lsb.set)
            lsb.pack(side="right", fill="y", padx=(0, 1), pady=1)
            for c in cats:
                lb.insert("end", "  %s　—　%d 项 · %s"
                          % (c["name"], c["count"], fmt_size(c["total"])))
            if rb:
                lb.insert("end", "  回收站　—　%d 项 · %s"
                          % (rb["count"], fmt_size(rb["size"])))

            tot = sum(c["total"] for c in cats) + (rb["size"] if rb else 0)
            cnt = sum(c["count"] for c in cats) + (rb["count"] if rb else 0)
            tk.Label(body, text="预计释放空间：%s　共 %d 项" % (fmt_size(tot), cnt),
                     bg=CARD, fg=INK, font=FONT_ST).pack(anchor="w")

            res = {"ok": False}
            bar = tk.Frame(body, bg=CARD)
            bar.pack(fill="x", pady=(SP["m"], 0))
            mk_btn2(bar, "取消", dlg.destroy, w=10).pack(side="right", padx=(SP["s"], 0))
            mk_btn(bar, "确认清理", lambda: (res.update(ok=True), dlg.destroy()),
                   bg=DANGER, w=12).pack(side="right")
            self.root.wait_window(dlg)
            return res["ok"]

        def _warn_strip(self, cv):
            """绘制「不可逆操作」警告条：柔和红底 + 深红标题 + 说明文字。"""
            cv.delete("all")
            w = max(cv.winfo_width(), 200)
            h = 54
            _round_rect(cv, 0, 0, w, h, R_INNER, fill=WARNBG,
                        outline=_tint(DANGER, 0.72), width=1)
            cv.create_text(SP["l"] + 6, h / 2 - 8, anchor="w",
                           text="⚠  即将删除以下项目，此操作不可逆",
                           fill=DANGER_D, font=FONT_B)
            cv.create_text(SP["l"] + 6, h / 2 + 9, anchor="w",
                           text="删除阶段会对每个文件路径做二次白名单校验，越界即拒绝",
                           fill=SUB, font=FONT_XS)

        def _on_cleaned(self, rep):
            self._set_progress(False)
            self._refresh_disk()
            lines = ["==================== 清理完成报告 ====================",
                     "清理前 C: 可用空间 : %s" % fmt_size(rep["before"][0]),
                     "清理后 C: 可用空间 : %s" % fmt_size(rep["after"][0]),
                     "实际可用空间变化   : %s" % fmt_size(rep["after"][0] - rep["before"][0]),
                     "实际释放（删除字节）: %s（成功 %d 个，失败 %d 个）"
                     % (fmt_size(rep["freed"]), rep["deleted"], rep["failed"]),
                     "----------------------------------------------------",
                     "分类明细（实际删除）："]
            any_del = False
            for name, d, f, fl in rep["per_cat"]:
                if d > 0:
                    lines.append("  - %s: %s  (%d 个文件)" % (name, fmt_size(f), d))
                    any_del = True
                elif fl > 0:
                    lines.append("  - %s: 跳过 %d 个（被占用 / 无权限）" % (name, fl))
            if rep["rb_count"] > 0:
                lines.append("  - 回收站: %s  (%d 项)" % (fmt_size(rep["rb_size"]), rep["rb_count"]))
                any_del = True
            if not any_del:
                lines.append("  （本次没有任何文件被删除，详见上方跳过说明）")
            lines.append("=====================================================")
            path = write_report(lines)
            html = write_html_report(rep)
            self._show_result(lines, path, html)
            self.scan_disk()

        def _show_result(self, lines, path, html=None):
            dlg = tk.Toplevel(self.root)
            dlg.title("清理完成")
            dlg.configure(bg=PAGE)
            dlg.transient(self.root)
            _center(dlg, 700, 560)
            card = mk_card(dlg, top_accent=True)
            card.pack(fill="both", expand=True, padx=SP["l"], pady=SP["l"])
            body = card.body

            head = tk.Frame(body, bg=CARD)
            head.pack(fill="x", pady=(0, SP["m"]))
            tk.Label(head, text="✔", bg=_tint(SUCCESS, 0.86), fg=SUCCESS,
                     font=FONT_B, width=3, height=1).pack(side="left")
            ttl = tk.Frame(head, bg=CARD)
            ttl.pack(side="left", padx=(SP["m"], 0))
            tk.Label(ttl, text="清理完成", bg=CARD, fg=INK,
                     font=FONT_T).pack(anchor="w")
            tk.Label(ttl, text="已生成前后对比报告，可打开查看明细",
                     bg=CARD, fg=SUB, font=FONT_S).pack(anchor="w")

            holder = tk.Frame(body, bg=LINE)
            holder.pack(fill="both", expand=True)
            txt = tk.Text(holder, font=MONO, bd=0, relief="flat",
                          highlightthickness=0, bg=PANEL, fg=TEXT,
                          wrap="none", padx=SP["m"], pady=SP["m"])
            tsb = ttk.Scrollbar(holder, orient="vertical",
                                style="Slim.Vertical.TScrollbar", command=txt.yview)
            txt.configure(yscrollcommand=tsb.set)
            tsb.pack(side="right", fill="y", padx=(0, 1), pady=1)
            txt.pack(fill="both", expand=True, padx=(1, 0), pady=1)
            txt.insert("1.0", "\n".join(lines))
            txt.tag_configure("hl", foreground=BLUE_D)
            txt.config(state="disabled")

            tk.Label(body, text="报告：" + os.path.basename(path)
                     + (("　·　" + os.path.basename(html)) if html else ""),
                     bg=CARD, fg=MUTED, font=FONT_XS, wraplength=600,
                     justify="left").pack(anchor="w", pady=(SP["s"], 0))

            bar = tk.Frame(body, bg=CARD)
            bar.pack(fill="x", pady=(SP["m"], 0))
            mk_btn(bar, "完成", dlg.destroy, w=12).pack(side="right")
            if html:
                mk_btn2(bar, "打开图表报告", lambda: os.startfile(html),
                        w=14).pack(side="right", padx=(0, SP["s"]))
            mk_btn2(bar, "打开报告目录", lambda: os.startfile(log_dir()),
                    w=14).pack(side="right", padx=(0, SP["s"]))
            dlg.grab_set()
            self.root.wait_window(dlg)

        # ==================== Tab 2：大文件扫描 ====================
        def _build_big_tab(self):
            # 底部操作区优先 pack（同磁盘清理页：后 pack 会被弹性内容区挤扁）
            foot = mk_card(self.tab_big, None)
            foot.pack(side="bottom", fill="x", padx=SP["m"],
                      pady=(SP["s"], SP["m"]))
            self.big_sel = tk.Label(foot.body, text="已选 0 个 · 合计 0 B", bg=CARD,
                                    fg=INK, font=FONT_B)
            self.big_sel.pack(anchor="w")
            fb = tk.Frame(foot.body, bg=CARD)
            fb.pack(fill="x", pady=(SP["m"], 0))
            mk_btn2(fb, "定位文件", self.big_reveal, w=10).pack(side="left",
                                                               padx=(0, SP["s"]))
            mk_btn2(fb, "打开文件", self.big_open, w=10).pack(side="left",
                                                             padx=(0, SP["s"]))
            mk_btn2(fb, "清空列表", self.big_clear, w=10).pack(side="left")
            mk_btn(fb, "送回收站", self.big_recycle, bg=DANGER,
                   w=12).pack(side="right")

            ctl = mk_card(self.tab_big, "扫描条件", top_accent=True)
            ctl.pack(fill="x", padx=SP["m"], pady=(SP["m"], SP["s"]))
            top = ctl.body
            tk.Label(top, text="扫描用户目录中的大文件，可定位或送入回收站（可还原）。"
                               "系统目录与可执行 / 驱动类文件受保护，无法在此删除。",
                     bg=CARD, fg=SUB, font=FONT_S, wraplength=920,
                     justify="left").pack(anchor="w")
            bar = tk.Frame(top, bg=CARD)
            bar.pack(fill="x", pady=(SP["m"], 0))
            tk.Label(bar, text="阈值 (MB)", bg=CARD, fg=TEXT,
                     font=FONT_S).pack(side="left", padx=(0, SP["s"]))
            self.big_thr = tk.StringVar(value=str(self.cfg["large_threshold_mb"]))
            mk_field(bar, textvariable=self.big_thr, width=6).pack(side="left",
                                                                  padx=(0, SP["m"]))
            mk_btn(bar, "开始扫描", self.scan_big, w=10).pack(side="left")
            self.big_prog = ttk.Progressbar(bar, mode="indeterminate", length=160,
                                            style="Fresh.Horizontal.TProgressbar")
            self.big_info = tk.Label(bar, text="尚未扫描", bg=CARD, fg=SUB, font=FONT_S)
            self.big_info.pack(side="left", padx=SP["m"])

            wrap = mk_card(self.tab_big, "扫描结果")
            wrap.pack(fill="both", expand=True, padx=SP["m"], pady=SP["s"])
            holder = tk.Frame(wrap.body, bg=LINE)
            holder.pack(fill="both", expand=True)
            cols = ("size", "age", "path")
            self.big_tree = ttk.Treeview(holder, columns=cols, show="headings",
                                         selectmode="extended", height=16,
                                         style="Fresh.Treeview")
            self.big_tree.heading("size", text="大小")
            self.big_tree.heading("age", text="最近修改")
            self.big_tree.heading("path", text="文件路径")
            self.big_tree.column("size", width=110, anchor="e")
            self.big_tree.column("age", width=120, anchor="center")
            self.big_tree.column("path", width=700, anchor="w")
            vs = ttk.Scrollbar(holder, orient="vertical",
                               style="Slim.Vertical.TScrollbar",
                               command=self.big_tree.yview)
            self.big_tree.configure(yscrollcommand=vs.set)
            vs.pack(side="right", fill="y", padx=(0, 1), pady=1)
            self.big_tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
            self.big_tree.tag_configure("odd", background=PANEL)
            self.big_tree.bind("<Double-1>", lambda e: self.big_reveal())

            self.big_tree.bind("<<TreeviewSelect>>", lambda e: self._big_update_sel())
            self.big_files = {}

        def scan_big(self):
            try:
                mb = max(10, int(self.big_thr.get()))
            except Exception:
                mb = 200
            self.cfg["large_threshold_mb"] = mb
            self.big_info.config(text="正在扫描（首次可能较慢）...")
            self.big_prog.pack(side="left", padx=8)
            self.big_prog.start(12)
            threading.Thread(target=self._big_worker, args=(mb,), daemon=True).start()

        def _big_worker(self, mb):
            res = scan_large_files({"large_threshold_mb": mb,
                                    "large_max_items": self.cfg["large_max_items"]},
                                   progress=lambda d: self.root.after(
                                       0, lambda: self.set_status("扫描中：%s" % d[-70:])))
            self.root.after(0, lambda: self._big_fill(res, mb))

        def _big_fill(self, res, mb):
            self.big_prog.stop()
            self.big_prog.pack_forget()
            for i in self.big_tree.get_children():
                self.big_tree.delete(i)
            self.big_files = {}
            for n, (p, s, m) in enumerate(res):
                try:
                    age = datetime.datetime.fromtimestamp(m).strftime("%Y-%m-%d")
                except Exception:
                    age = "-"
                iid = self.big_tree.insert("", "end", values=(fmt_size(s), age, p),
                                           tags=("odd",) if n % 2 else ())
                self.big_files[iid] = (p, s)
            tot = sum(x[1] for x in res)
            self.big_info.config(text="命中 %d 个文件 · 合计 %s" % (len(res), fmt_size(tot)))
            self.set_status("大文件扫描完成：%d 个（>=%dMB），合计 %s" % (len(res), mb, fmt_size(tot)))
            self._big_update_sel()

        def _big_selected(self):
            return [self.big_files[i] for i in self.big_tree.selection() if i in self.big_files]

        def _big_update_sel(self):
            sel = self._big_selected()
            self.big_sel.config(text="已选 %d 个 · 合计 %s"
                                     % (len(sel), fmt_size(sum(s for _p, s in sel))))

        def big_reveal(self):
            sel = self._big_selected()
            if not sel:
                messagebox.showinfo("提示", "请先在列表中选择文件。")
                return
            p = sel[0][0]
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
            except Exception as e:  # noqa
                messagebox.showwarning("无法定位", str(e))

        def big_open(self):
            sel = self._big_selected()
            if not sel:
                return
            try:
                os.startfile(sel[0][0])
            except Exception as e:  # noqa
                messagebox.showwarning("无法打开", str(e))

        def big_clear(self):
            for i in self.big_tree.get_children():
                self.big_tree.delete(i)
            self.big_files = {}
            self.big_info.config(text="已清空列表（未删除任何文件）")
            self._big_update_sel()

        def big_recycle(self):
            sel = self._big_selected()
            if not sel:
                messagebox.showinfo("提示", "请先勾选（选中）要删除的文件。")
                return
            bad = [p for p, _s in sel if is_large_file_protected(p)
                   or os.path.splitext(p)[1].lower() in LARGE_FILE_BLOCKED_EXT]
            if bad:
                messagebox.showwarning(
                    "已阻止", "以下 %d 个文件受保护（系统目录或可执行/驱动类），不会删除：\n\n%s"
                    % (len(bad), "\n".join(bad[:8]) + ("\n..." if len(bad) > 8 else "")))
                sel = [(p, s) for p, s in sel if p not in bad]
            if not sel:
                return
            tot = sum(s for _p, s in sel)
            if not messagebox.askyesno(
                    "确认", "将把 %d 个文件送入回收站（可还原）：\n\n合计 %s\n\n是否继续？"
                    % (len(sel), fmt_size(tot)), icon="warning"):
                return
            ok, fail = recycle_paths([p for p, _s in sel])
            done = {p for p, _s in sel if not os.path.exists(p)}
            messagebox.showinfo("完成", "已送回收站 %d 个，失败 %d 个。" % (ok, fail))
            for iid in list(self.big_files.keys()):
                if self.big_files[iid][0] in done:
                    self.big_tree.delete(iid)
                    self.big_files.pop(iid, None)
            self._big_update_sel()
            self._refresh_disk()

        # ==================== Tab 3：内存与进程 ====================
        def _build_mem_tab(self):
            # ---------- 顶部实时数据贴片 ----------
            tiles = tk.Frame(self.tab_mem, bg=PAGE)
            tiles.pack(fill="x", padx=SP["m"], pady=(SP["m"], SP["s"]))
            self.tile_mem = StatTile(tiles, label="物理内存使用率", value="—",
                                     outer=PAGE, width=210)
            self.tile_mem.pack(side="left")
            self.tile_memused = StatTile(tiles, label="已用内存", value="—",
                                         accent=BLUE_D, outer=PAGE, width=200)
            self.tile_memused.pack(side="left", padx=(SP["m"], 0))
            self.tile_cfree = StatTile(tiles, label="C: 可用空间", value="—",
                                       accent=CYAN, outer=PAGE, width=200)
            self.tile_cfree.pack(side="left", padx=(SP["m"], 0))

            ch = mk_card(self.tab_mem, "实时曲线", note="每秒采样 · 60 秒滚动窗口",
                         top_accent=True)
            ch.pack(fill="x", padx=SP["m"], pady=SP["s"])
            # 曲线只是趋势参考，压到 118px：省下的纵向空间全部让给下面的进程表
            self.chart = tk.Canvas(ch.body, bg=PANEL, height=118,
                                   highlightthickness=1, highlightbackground=LINE)
            self.chart.pack(fill="x")

            inner = mk_card(self.tab_mem, "后台进程内存占用排行", note=" ")
            inner.pack(fill="both", expand=True, padx=SP["m"], pady=(SP["s"], SP["m"]))
            # 提示文案复用卡片副标题、按钮放到标题右侧，
            # 这样整条 36px 的工具行都不用占纵向空间，进程表能多显示两行。
            self.proc_hint = inner.note_label
            if self.proc_hint is not None:
                self.proc_hint.config(font=FONT_S, fg=SUB)
            hr = inner.head_right
            mk_btn2(hr, "刷新", self.refresh_procs, w=8).pack(side="right")
            mk_btn(hr, "结束选中进程", self.kill_selected, bg=DANGER,
                   w=13).pack(side="right", padx=(0, SP["s"]))

            holder = tk.Frame(inner.body, bg=LINE)
            holder.pack(fill="both", expand=True)
            cols = ("pid", "name", "ws", "title")
            self.proc_tree = ttk.Treeview(holder, columns=cols, show="headings",
                                          selectmode="extended", height=9,
                                          style="Fresh.Treeview")
            for c, t, w in (("pid", "PID", 70), ("name", "进程名", 190),
                            ("ws", "内存占用", 110), ("title", "窗口标题", 420)):
                self.proc_tree.heading(c, text=t)
                self.proc_tree.column(c, width=w,
                                      anchor="w" if c in ("name", "title") else "center")
            ps = ttk.Scrollbar(holder, orient="vertical",
                               style="Slim.Vertical.TScrollbar",
                               command=self.proc_tree.yview)
            self.proc_tree.configure(yscrollcommand=ps.set)
            ps.pack(side="right", fill="y", padx=(0, 1), pady=1)
            self.proc_tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
            self.proc_tree.tag_configure("hint", foreground=SUCCESS)
            self.proc_tree.tag_configure("odd", background=PANEL)
            self.mem_hist = []
            self._tick_mem()
            # 进程枚举放到 mainloop 启动之后再起线程：worker 通过 root.after 回投
            # 结果，若在 mainloop 之前调用会抛 "main thread is not in main loop"，
            # 导致进程列表静默为空。
            self.root.after(300, lambda: threading.Thread(
                target=self.refresh_procs, daemon=True).start())

        def _tick_mem(self):
            mem = get_memory_info()
            free, total = get_disk_free("C")
            self.tile_mem.set_value("%d" % mem["load"], unit="%")
            self.tile_memused.set_value(fmt_size(mem["used"]),
                                        unit="/ %s" % fmt_size(mem["total"]))
            self.tile_cfree.set_value(fmt_size(free), unit="/ %s" % fmt_size(total))
            self.mem_hist.append((mem["load"], free))
            if len(self.mem_hist) > 60:
                self.mem_hist.pop(0)
            self._draw_chart()
            self.root.after(1000, self._tick_mem)

        def _draw_chart(self):
            """实时曲线：内存面积+折线（主色），C: 可用折线（青色虚线），附网格与图例。"""
            c = self.chart
            c.delete("all")
            w = max(400, c.winfo_width() or 800)
            h = max(96, c.winfo_height() or 118)      # 跟随画布实际高度，不写死
            pad_l, pad_r, pad_t, pad_b = 34, 12, 26, 18
            # 网格
            for i in range(1, 4):
                y = pad_t + (h - pad_t - pad_b) * i / 4
                c.create_line(pad_l, y, w - pad_r, y, fill=GRID)
            x0, x1 = pad_l, w - pad_r
            y0, y1 = pad_t, h - pad_b
            for lv, yy in ((100, y0), (0, y1)):
                c.create_text(pad_l - 6, yy, anchor="e", font=FONT_XS,
                              fill=MUTED, text="%d%%" % lv)
            # 图例
            c.create_line(x0 + 2, 12, x0 + 22, 12, fill=BLUE, width=2)
            c.create_text(x0 + 27, 12, anchor="w", font=FONT_XS, fill=SUB,
                          text="内存使用率")
            c.create_line(x0 + 100, 12, x0 + 120, 12, fill=CYAN, width=2, dash=(4, 3))
            c.create_text(x0 + 125, 12, anchor="w", font=FONT_XS, fill=SUB,
                          text="C: 可用空间（归一化）")
            if len(self.mem_hist) < 2:
                c.create_text(w / 2, h / 2, font=FONT_S, fill=MUTED,
                              text="正在采集数据…")
                return
            n = len(self.mem_hist)
            step = (x1 - x0) / max(1, n - 1)
            span = y1 - y0

            def px(i):
                return x0 + i * step

            # 内存：面积填充（浅蓝）+ 折线（主蓝）
            mpts = [(px(i), y1 - (load / 100.0) * span)
                    for i, (load, _f) in enumerate(self.mem_hist)]
            area = [x0, y1]
            for x, y in mpts:
                area += [x, y]
            area += [mpts[-1][0], y1]
            c.create_polygon(area, fill=_tint(BLUE, 0.88), outline="")
            flat = []
            for x, y in mpts:
                flat += [x, y]
            c.create_line(*flat, fill=BLUE, width=2, smooth=True)
            lx, ly = mpts[-1]
            c.create_oval(lx - 3.5, ly - 3.5, lx + 3.5, ly + 3.5, fill=BLUE,
                          outline="#FFFFFF", width=1.5)
            c.create_text(min(lx, x1 - 30), max(ly - 12, 20), anchor="e",
                          font=FONT_XS, fill=BLUE_D,
                          text="%d%%" % self.mem_hist[-1][0])
            # 磁盘可用：归一化虚线（青色副序列）
            frees = [f for _l, f in self.mem_hist]
            mx, mn = (max(frees) or 1), min(frees)
            rng = (mx - mn) or 1
            dflat = []
            for i, f in enumerate(frees):
                dflat += [px(i), y1 - 6 - ((f - mn) / rng) * (span * 0.30)]
            c.create_line(*dflat, fill=CYAN, width=1.6, dash=(4, 3))
            c.create_text(w - pad_r, y1 + 12, anchor="e", font=FONT_XS, fill=CYAN,
                          text="C: 可用 %s" % fmt_size(frees[-1]))

        def refresh_procs(self):
            procs = [p for p in list_processes(150) if not p["protected"]]
            self.root.after(0, lambda: self._fill_procs(procs))

        def _fill_procs(self, procs):
            for i in self.proc_tree.get_children():
                self.proc_tree.delete(i)
            n_hint = 0
            for n, p in enumerate(procs):
                tags = []
                if p["hint"]:
                    tags.append("hint")
                    n_hint += 1
                if n % 2:
                    tags.append("odd")
                name = p["name"] + ("　(可安全关闭)" if p["hint"] else "")
                self.proc_tree.insert("", "end", iid=str(p["pid"]),
                                      values=(p["pid"], name, fmt_size(p["ws"]), p["title"]),
                                      tags=tuple(tags))
            if self.proc_hint is not None:
                self.proc_hint.config(text="共 %d 个可结束进程 · 其中 %d 个标记为可安全关闭"
                                           % (len(procs), n_hint))
            self.set_status("进程列表已刷新：%d 个可结束进程（系统关键进程已隐藏）" % len(procs))

        def kill_selected(self):
            sel = self.proc_tree.selection()
            if not sel:
                messagebox.showinfo("提示", "请先在列表中选择要结束的进程。")
                return
            items = []
            for iid in sel:
                vals = self.proc_tree.item(iid, "values")
                if vals:
                    items.append((int(vals[0]), str(vals[1]), str(vals[2])))
            risky = [i for i in items if any(k in i[1].lower() for k in
                                             ("svchost", "system", "lsass", "winlogon", "csrss"))]
            if risky:
                messagebox.showwarning("已阻止", "所选包含系统关键进程，已被保护机制拒绝。")
                items = [i for i in items if i not in risky]
            if not items:
                return
            txt = "\n".join("PID %d　%s　%s" % i for i in items[:15])
            if not messagebox.askyesno(
                    "确认结束进程",
                    "将强制结束以下 %d 个进程：\n\n%s\n\n"
                    "未保存的数据可能丢失，请先确认这些程序已保存工作。是否继续？"
                    % (len(items), txt), icon="warning"):
                return
            ok = fail = 0
            detail = []
            for pid, name, _m in items:
                s, msg = kill_process(pid)
                if s:
                    ok += 1
                else:
                    fail += 1
                    detail.append("%s(%d)：%s" % (name, pid, msg))
            lines = ["====== 进程结束报告 %s ======" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "成功结束 %d 个，失败 %d 个" % (ok, fail)]
            for d in detail:
                lines.append("  失败：" + d)
            path = write_report(lines, tag="prockill")
            messagebox.showinfo("完成", "成功结束 %d 个，失败 %d 个。\n报告：%s"
                                % (ok, fail, os.path.basename(path)))
            self._refresh_disk()
            threading.Thread(target=self.refresh_procs, daemon=True).start()

        # ==================== Tab 4：自动清理 ====================
        def _build_auto_tab(self):
            box = mk_card(self.tab_auto, "清理规则",
                          note="注册为 Windows 计划任务，到点以静默模式 --autorun 执行并生成报告",
                          top_accent=True)
            box.pack(fill="x", padx=SP["m"], pady=(SP["m"], SP["s"]))
            r1 = tk.Frame(box.body, bg=CARD)
            r1.pack(fill="x", pady=(0, SP["m"]))
            tk.Label(r1, text="执行频率", bg=CARD, fg=TEXT,
                     font=FONT_S).pack(side="left", padx=(0, SP["s"]))
            # 下拉里显示中文，回写配置时再翻回 Windows 认识的关键字
            self.auto_sched = tk.StringVar(
                value=SCHED_ZH.get((self.cfg["auto_schedule"] or "DAILY").upper(),
                                   "每天"))
            ttk.Combobox(r1, textvariable=self.auto_sched, width=10, state="readonly",
                         style="Fresh.TCombobox",
                         values=list(SCHED_ZH.values())).pack(side="left",
                                                              padx=(0, SP["l"]))
            tk.Label(r1, text="执行时间", bg=CARD, fg=TEXT,
                     font=FONT_S).pack(side="left", padx=(0, SP["s"]))
            self.auto_time = tk.StringVar(value=self.cfg["auto_time"])
            mk_field(r1, textvariable=self.auto_time, width=7).pack(side="left",
                                                                   padx=(0, SP["xs"]))
            tk.Label(r1, text="(24 小时制，如 03:00)", bg=CARD, fg=MUTED,
                     font=FONT_XS).pack(side="left", padx=(0, SP["l"]))
            self.auto_enable = tk.BooleanVar(value=self.cfg["auto_enable"])
            mk_check(r1, "启用自动清理", self.auto_enable).pack(side="left")

            r2 = tk.Frame(box.body, bg=CARD)
            r2.pack(fill="x")
            tk.Label(r2, text="清理目标", bg=CARD, fg=TEXT,
                     font=FONT_S).pack(side="left", padx=(0, SP["s"]))
            self.t_sys = tk.BooleanVar(value="system" in self.cfg["auto_targets"])
            self.t_app = tk.BooleanVar(value="appcache" in self.cfg["auto_targets"])
            self.t_rb = tk.BooleanVar(value="recycle" in self.cfg["auto_targets"])
            for var, t in ((self.t_sys, "系统临时文件"), (self.t_app, "应用缓存"),
                           (self.t_rb, "回收站")):
                mk_check(r2, t, var).pack(side="left", padx=(SP["l"], 0))

            act = tk.Frame(self.tab_auto, bg=PAGE)
            act.pack(fill="x", padx=SP["m"], pady=SP["s"])
            mk_btn(act, "保存并注册计划任务", self.auto_save_register,
                   w=13).pack(side="left")
            mk_btn2(act, "取消计划任务", self.auto_unregister,
                    w=11).pack(side="left", padx=SP["s"])
            mk_btn(act, "立即执行一次", self.auto_run_now,
                   bg=CYAN, w=11).pack(side="left", padx=SP["s"])
            mk_btn2(act, "查看任务状态", self.auto_status, w=11).pack(side="left")

            logcard = mk_card(self.tab_auto, "运行日志")
            logcard.pack(fill="both", expand=True, padx=SP["m"],
                         pady=(SP["s"], SP["m"]))
            holder = tk.Frame(logcard.body, bg=LINE)
            holder.pack(fill="both", expand=True)
            self.auto_log = tk.Text(holder, height=14, font=MONO, bd=0, relief="flat",
                                    highlightthickness=0, bg=PANEL, fg=TEXT,
                                    wrap="word", padx=SP["m"], pady=SP["m"])
            asb = ttk.Scrollbar(holder, orient="vertical",
                                style="Slim.Vertical.TScrollbar",
                                command=self.auto_log.yview)
            self.auto_log.configure(yscrollcommand=asb.set)
            asb.pack(side="right", fill="y", padx=(0, 1), pady=1)
            self.auto_log.pack(fill="both", expand=True, padx=1, pady=1)
            self.auto_log.insert("1.0", "提示：注册计划任务需要程序以打包后的 exe 运行；"
                                        "部分系统若提示权限不足，请以管理员身份运行本程序。\n")
            self.auto_status()

        def _auto_cfg_from_ui(self):
            tgs = []
            if self.t_sys.get():
                tgs.append("system")
            if self.t_app.get():
                tgs.append("appcache")
            if self.t_rb.get():
                tgs.append("recycle")
            return {
                "auto_enable": bool(self.auto_enable.get()),
                "auto_schedule": SCHED_RAW.get(self.auto_sched.get().strip(), "DAILY"),
                "auto_time": (self.auto_time.get().strip() or "03:00"),
                "auto_targets": ",".join(tgs),
            }

        def _auto_log_append(self, s):
            self.auto_log.insert("end", s + "\n")
            self.auto_log.see("end")

        def _write_managed_autoclean(self, ac):
            """把自动清理设置写回 ini 托管区块。"""
            try:
                text = ""
                if os.path.exists(self.ini):
                    with open(self.ini, "r", encoding="utf-8-sig", errors="replace") as f:
                        text = f.read()
                i = text.find("; ==AUTO-BEGIN==")
                j = text.find("; ==AUTO-END==")
                if i != -1 and j != -1:
                    text = text[:i] + text[j + len("; ==AUTO-END=="):]
                block = ["; ==AUTO-BEGIN==", "[AutoClean]",
                         "Enable=%d" % (1 if ac["auto_enable"] else 0),
                         "Schedule=%s" % ac["auto_schedule"],
                         "Time=%s" % ac["auto_time"],
                         "Targets=%s" % ac["auto_targets"],
                         "; ==AUTO-END=="]
                with open(self.ini, "w", encoding="utf-8") as f:
                    f.write(text.rstrip() + "\n\n" + "\n".join(block) + "\n")
                return True
            except Exception:
                return False

        def auto_save_register(self):
            ac = self._auto_cfg_from_ui()
            self.cfg.update(ac)
            self._write_managed_autoclean(ac)
            self._auto_log_append("[%s] 规则已保存到 cleanup_config.ini" % time.strftime("%H:%M:%S"))
            if not ac["auto_enable"]:
                self._auto_log_append("自动清理为关闭状态，未注册计划任务。")
                return
            if not getattr(sys, "frozen", False):
                self._auto_log_append("当前是源码运行模式，无法注册计划任务；"
                                      "请使用打包后的 CleanCDrive.exe 执行此操作。")
                return
            ok, msg = scheduled_task_register(ac)
            self._auto_log_append(("✔ 计划任务注册成功：" if ok else "✘ 注册失败：") + (msg or ""))
            if ok:
                self._auto_log_append("    下次执行：%s %s" % (ac["auto_schedule"], ac["auto_time"]))

        def auto_unregister(self):
            ok, msg = scheduled_task_delete()
            self._auto_log_append(("✔ 已取消计划任务。" if ok else "✘ 取消失败：") + (msg or ""))
            self.auto_status()

        def auto_run_now(self):
            if not self.t_sys.get() and not self.t_app.get() and not self.t_rb.get():
                messagebox.showinfo("提示", "请至少勾选一个清理目标。")
                return
            if not messagebox.askyesno("确认", "立即执行一次自动清理？\n\n"
                                               "将按当前勾选的目标清理，并在完成后生成报告。", icon="warning"):
                return
            self._auto_log_append("[%s] 开始执行一次自动清理..." % time.strftime("%H:%M:%S"))
            ac = self._auto_cfg_from_ui()
            threading.Thread(target=self._auto_run_worker, args=(ac,), daemon=True).start()

        def _auto_run_worker(self, ac):
            cfg = dict(self.cfg)
            cfg.update({"auto_enable": True, "auto_targets": ac["auto_targets"],
                        "skip_recycle": False})
            targets = [t for t in ac["auto_targets"].split(",") if t]
            before = get_disk_free("C")
            cats = build_categories(cfg)
            if "system" not in targets:
                cats = [c for c in cats if c["kind"] != "system"]
            if "appcache" not in targets:
                cats = [c for c in cats if c["kind"] != "app"]
            allowed = [c["root"] for c in cats]
            per_cat = []
            deleted = freed = failed = 0
            for c in cats:
                d, f, fl = delete_files(c["files"], allowed)
                if d or fl:
                    per_cat.append((c["name"], d, f, fl))
                deleted += d
                freed += f
                failed += fl
            clean_empty_dirs(allowed)
            rbc = rbs = 0
            if "recycle" in targets:
                info = get_recycle_bin()
                empty_recycle_bin()
                rbc, rbs = info.get("count", 0), info.get("size", 0)
                deleted += rbc
                freed += rbs
            after = get_disk_free("C")
            rep = {"before": before, "after": after, "deleted": deleted, "freed": freed,
                   "failed": failed, "per_cat": per_cat, "rb_count": rbc, "rb_size": rbs}
            path = write_report(["自动清理：释放 %s（成功 %d，跳过 %d）"
                                 % (fmt_size(freed), deleted, failed)], tag="autorun")
            html = write_html_report(rep, tag="autorun")
            self.root.after(0, lambda: self._auto_done(rep, path, html))

        def _auto_done(self, rep, path, html):
            self._auto_log_append("[%s] 完成：释放 %s（成功 %d，跳过 %d）"
                                  % (time.strftime("%H:%M:%S"), fmt_size(rep["freed"]),
                                     rep["deleted"], rep["failed"]))
            for name, d, f, fl in rep["per_cat"]:
                if d:
                    self._auto_log_append("    - %s: %s (%d 项)" % (name, fmt_size(f), d))
            self._auto_log_append("    报告：%s" % os.path.basename(path))
            self._auto_log_append("    图表报告：%s" % os.path.basename(html))
            self.scan_disk()
            self._refresh_disk()

        def auto_status(self):
            ok, info = scheduled_task_query()
            state = "已注册" if ok else "未注册"
            self._auto_log_append("[状态] 计划任务 %s：%s"
                                  % (TASK_NAME, state))
            if ok and info:
                for line in info.splitlines():
                    if line.strip():
                        self._auto_log_append("        " + line.strip())

        # ==================== Tab 5：排除设置 ====================
        def _build_excl_tab(self):
            # 底部操作行优先 pack(side="bottom")：放在最后 pack 时会被上方
            # 弹性列表区抢走空间，按钮高度被压到只剩一半。
            bot = mk_card(self.tab_excl, None)
            bot.pack(side="bottom", fill="x", padx=SP["m"],
                     pady=(SP["s"], SP["m"]))
            tk.Label(bot.body, text="仅清理超过 N 天未修改的文件（0 = 不限制）", bg=CARD,
                     fg=TEXT, font=FONT_S).pack(side="left", padx=(0, SP["s"]))
            self.ex_age = tk.StringVar(value=str(self.cfg["retain_age_days"]))
            mk_field(bot.body, textvariable=self.ex_age, width=5).pack(side="left")
            mk_btn(bot.body, "保存到配置文件", self.ex_save,
                   w=15).pack(side="right")
            mk_btn2(bot.body, "重新载入", self.ex_reload,
                    w=10).pack(side="right", padx=SP["s"])

            info = mk_card(self.tab_excl, "保留项规则",
                           note="被命中的文件不会被任何清理动作删除 · 保存后写回 cleanup_config.ini",
                           top_accent=True)
            info.pack(fill="x", padx=SP["m"], pady=(SP["m"], SP["s"]))
            tk.Label(info.body, text="配置文件：" + self.ini, bg=CARD, fg=MUTED,
                     font=FONT_XS, wraplength=920,
                     justify="left").pack(anchor="w")

            box = tk.Frame(self.tab_excl, bg=PAGE)
            box.pack(fill="both", expand=True, padx=SP["m"], pady=SP["s"])

            l1 = mk_card(box, "保留路径", note="整个目录不清理")
            l1.pack(side="left", fill="both", expand=True)
            # 输入行必须先 pack(side="bottom")：Listbox 的请求高度很大，
            # 后 pack 的输入行会被它挤到只剩半行高。
            e1 = tk.Frame(l1.body, bg=CARD)
            e1.pack(side="bottom", fill="x", pady=(SP["s"], 0))
            self.ex_path_entry = mk_field(e1, width=22, placeholder="例如 D:\\工作\\缓存")
            self.ex_path_entry.pack(side="left", fill="x", expand=True)
            mk_btn2(e1, "添加", self.ex_add_path, w=7).pack(side="left", padx=(SP["s"], 0))
            mk_btn2(e1, "删除", self.ex_del_path, w=7).pack(side="left", padx=(SP["s"], 0))
            w1, self.ex_paths = mk_list(l1.body)
            w1.pack(fill="both", expand=True)

            l2 = mk_card(box, "保留文件名模式", note="支持 * 与 ?")
            l2.pack(side="left", fill="both", expand=True, padx=(SP["m"], 0))
            e2 = tk.Frame(l2.body, bg=CARD)
            e2.pack(side="bottom", fill="x", pady=(SP["s"], 0))
            self.ex_pat_entry = mk_field(e2, width=22, placeholder="例如 *.important")
            self.ex_pat_entry.pack(side="left", fill="x", expand=True)
            mk_btn2(e2, "添加", self.ex_add_pat, w=7).pack(side="left", padx=(SP["s"], 0))
            mk_btn2(e2, "删除", self.ex_del_pat, w=7).pack(side="left", padx=(SP["s"], 0))
            w2, self.ex_pats = mk_list(l2.body)
            w2.pack(fill="both", expand=True)

            self.ex_reload()

        def ex_reload(self):
            self.cfg = parse_config(self.ini)
            self.ex_paths.delete(0, "end")
            self.ex_pats.delete(0, "end")
            for p in self.cfg["retain_paths"]:
                self.ex_paths.insert("end", p)
            for p in self.cfg["retain_patterns"]:
                self.ex_pats.insert("end", p)
            self.ex_age.set(str(self.cfg["retain_age_days"]))

        def ex_add_path(self):
            v = self.ex_path_entry.get().strip()
            if v:
                self.ex_paths.insert("end", v)
                self.ex_path_entry.delete(0, "end")

        def ex_del_path(self):
            for i in reversed(self.ex_paths.curselection()):
                self.ex_paths.delete(i)

        def ex_add_pat(self):
            v = self.ex_pat_entry.get().strip()
            if v:
                self.ex_pats.insert("end", v)
                self.ex_pat_entry.delete(0, "end")

        def ex_del_pat(self):
            for i in reversed(self.ex_pats.curselection()):
                self.ex_pats.delete(i)

        def ex_save(self):
            try:
                age = max(0, int(self.ex_age.get()))
            except Exception:
                age = 0
            ok = save_managed_rules(self.ini,
                                    list(self.ex_paths.get(0, "end")),
                                    list(self.ex_pats.get(0, "end")), age)
            self.cfg = parse_config(self.ini)
            if ok:
                self.set_status("排除规则已保存到 %s" % self.ini)
                messagebox.showinfo("已保存", "保留规则已写回配置：\n%s" % self.ini)
                self.scan_disk()
            else:
                messagebox.showwarning("保存失败", "无法写入配置文件，请检查权限：\n%s" % self.ini)

    root = tk.Tk()

    # —— 环境运行库自检：在构建主界面之前，遇缺失先弹窗声明 ——
    _env_out = {}
    if not _run_env_guard(root, smoke, _env_out):
        root.destroy()
        return []

    root.configure(bg=PAGE)
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    _install_styles()          # 必须在 Tk 根窗口创建之后，否则 Style 会隐式再建一个根
    app = App(root)

    # 启动页可指定：CCD_START_TAB=0..4
    # 供截图 / 自动化流程直接定位到某一页，无需模拟鼠标点击
    # （Tk 不响应进程内注入的鼠标消息，模拟点击在无交互会话里不可靠）。
    try:
        _t0 = int(os.environ.get("CCD_START_TAB", ""))
    except (TypeError, ValueError):
        _t0 = -1
    if 0 <= _t0 <= 4:
        app.tabs.select(_t0)

    if not smoke:
        root.mainloop()
        return None

    # ---------------------------- 冒烟测试 ----------------------------
    # 注意：扫描 / 进程枚举在 worker 线程里通过 root.after 把结果回投主线程，
    #       该调用只有在主线程处于 mainloop 时才合法。所以这里让主线程进入
    #       mainloop，用定时器分阶段驱动断言，跑完再 quit 退出。
    checks = []
    if _env_out.get("items"):
        checks.extend(_env_out["items"])

    def ck(name, fn):
        try:
            v = fn()
            checks.append(("PASS" if v else "FAIL", name,
                           "" if v else "returned %r" % (v,)))
        except Exception as e:  # noqa
            checks.append(("ERROR", name, repr(e)))

    def _find_widgets(parent, cls):
        """递归收集 parent 下所有指定类型的子控件（供冒烟测试断言用）。"""
        out = []
        try:
            for w in parent.winfo_children():
                if isinstance(w, cls):
                    out.append(w)
                out.extend(_find_widgets(w, cls))
        except Exception:
            pass
        return out

    try:
        root.attributes("-alpha", 0.0)   # 透明但已映射：布局尺寸真实，不闪屏
    except Exception:
        pass

    def _dump_layout():
        """把关键控件的实时几何写进 _layout.txt。

        只在设置了环境变量 CCD_LAYOUT_DUMP 时执行 —— 界面「看着不对」时
        靠肉眼估算像素容易出错，直接读真实数值更快也可靠。
        """
        lines = ["== 窗口 client %dx%d ==" % (root.winfo_width(),
                                              root.winfo_height())]

        def rec(w, depth=0, label=""):
            try:
                if not w.winfo_ismapped():
                    return
                lines.append("%s%-22s %s x=%d y=%d %dx%d"
                             % ("  " * depth, w.winfo_class(), label,
                                w.winfo_x(), w.winfo_y(),
                                w.winfo_width(), w.winfo_height()))
            except Exception:
                pass
            if depth >= 3:
                return
            for c in w.winfo_children():
                rec(c, depth + 1)

        named = [("stack", getattr(app, "stack", None)),
                 ("tab_disk", getattr(app, "tab_disk", None)),
                 ("tile_row", getattr(getattr(app, "tile_total", None), "master", None)),
                 ("foot", getattr(getattr(app, "foot_body", None), "master", None)),
                 ("rank", getattr(app, "rank", None)),
                 ("chart", getattr(app, "chart", None)),
                 ("proc_tree", getattr(app, "proc_tree", None)),
                 ("big_tree", getattr(app, "big_tree", None)),
                 ("auto_log", getattr(app, "auto_log", None)),
                 ("status", getattr(app, "status", None))]
        for nm, w in named:
            if w is None:
                continue
            try:
                lines.append("%-12s x=%d y=%d %dx%d  (rooty=%d)"
                             % (nm, w.winfo_x(), w.winfo_y(),
                                w.winfo_width(), w.winfo_height(),
                                w.winfo_rooty() - root.winfo_rooty()))
            except Exception:
                pass
        try:
            lines.append("proc rows=%d  rowheight=%s  可见行数≈%.1f"
                         % (len(app.proc_tree.get_children()),
                            ttk.Style().lookup("Fresh.Treeview", "rowheight"),
                            max(0, app.proc_tree.winfo_height() - 36) / 32.0))
        except Exception:
            pass
        # 药丸导航各段的绝对屏幕坐标：供外部截图脚本精确点击（不依赖估算像素）
        try:
            segs = []
            for i, ch in enumerate(app.tabs.winfo_children()):
                if ch.winfo_ismapped():
                    segs.append("tab%d sx=%d sy=%d w=%d h=%d"
                                % (i, ch.winfo_rootx(), ch.winfo_rooty(),
                                   ch.winfo_width(), ch.winfo_height()))
            lines.append("tabs: " + " | ".join(segs))
        except Exception as e:
            lines.append("tabs 诊断失败: %r" % (e,))
        try:
            lines.append("window: rootx=%d rooty=%d client=%dx%d"
                         % (root.winfo_rootx(), root.winfo_rooty(),
                            root.winfo_width(), root.winfo_height()))
        except Exception:
            pass
        try:
            rows = [w for w in app.inner.winfo_children()
                    if isinstance(w, CheckRow)]
            item = app.canvas.find_all()[0] if app.canvas.find_all() else None
            lines.append(
                "列表: canvas %dx%d  inner %dx%d  行数=%d  首行 %dx%d  "
                "window项宽=%r scrollregion=%r"
                % (app.canvas.winfo_width(), app.canvas.winfo_height(),
                   app.inner.winfo_width(), app.inner.winfo_height(), len(rows),
                   rows[0].winfo_width() if rows else -1,
                   rows[0].winfo_height() if rows else -1,
                   app.canvas.itemcget(item, "width") if item else None,
                   app.canvas.cget("scrollregion")))
        except Exception as e:
            lines.append("列表诊断失败: %r" % (e,))
        st = ttk.Style()
        for sty, opt in (("Fresh.Treeview", "rowheight"),
                         ("Fresh.Treeview.Heading", "padding")):
            try:
                lines.append("style %s %s = %r" % (sty, opt, st.lookup(sty, opt)))
            except Exception:
                pass
        for i, pg in enumerate(app.stack.pages()):
            app._on_tab(i)
            root.update_idletasks()
            lines.append("--- page %d 高 %d ---" % (i, pg.winfo_height()))
            for c in pg.winfo_children():
                if c.winfo_ismapped():
                    lines.append("    %-12s y=%d h=%d bottom=%d"
                                 % (c.winfo_class(), c.winfo_y(),
                                    c.winfo_height(),
                                    c.winfo_y() + c.winfo_height()))
        app._on_tab(0)
        root.update_idletasks()

        def deep(w, depth, out):
            if depth > 5:
                return
            try:
                if w.winfo_ismapped() and w.winfo_width() > 1:
                    out.append("%s%-20s %dx%d" % ("  " * depth, w.winfo_class(),
                                                  w.winfo_width(), w.winfo_height()))
            except Exception:
                pass
            for c in w.winfo_children():
                deep(c, depth + 1, out)

        for nm, w in named:
            if w is None:
                continue
            sub = []
            deep(w, 0, sub)
            lines.append("== 树 %s ==" % nm)
            lines.extend(sub)

        _safe_write("_layout.txt", "\n".join(lines))

    def run_checks():
        """主线程内执行全部断言（此时 mainloop 正在运行）。"""
        _dbg = []
        ck("主题令牌完整（THEME 全键可取色）",
           lambda: all(len(_c(k)) == 7 and _c(k).startswith("#")
                       for k in ("page", "card", "panel", "tint", "tint2", "selbg",
                                 "line", "grid", "blue", "blue_d", "blue_l",
                                 "blue_t", "cyan", "ink", "text", "sub", "muted",
                                 "danger", "danger_d", "warnbg", "success",
                                 "sh1", "sh2")))
        ck("五个标签页建立（自绘页面栈）", lambda: len(app.stack.pages()) == 5)
        ck("页面栈取代 Notebook（界面内不存在原生标签条）",
           lambda: isinstance(app.stack, TabStack)
           and not _find_widgets(root, ttk.Notebook))
        ck("药丸导航 5 段",
           lambda: len(app.tabs._items) == 5)
        ck("药丸导航可切换页面（PillTabs 与页面栈同步）",
           lambda: (app.tabs.select(2),
                    app.stack.current() == 2 and app.tabs.current() == 2)[1])
        ck("药丸导航选中态重绘出图元",
           lambda: len(app.tabs._items[2]["cv"].find_all()) >= 2)
        ck("扫描产出分类", lambda: len(app.categories) > 0)
        ck("全宽圆角勾选行已渲染（CheckRow）",
           lambda: len(app.cat_vars) > 0
           and all(isinstance(r, CheckRow) for r in app.inner.winfo_children()
                   if isinstance(r, CheckRow)))
        ck("圆角勾选行绘制出图元（含勾选框与文字）",
           lambda: all(len(r.find_all()) >= 3 for r in app.inner.winfo_children()
                       if isinstance(r, CheckRow)))
        ck("统计贴片已由「扫描中」更新为真实值",
           lambda: app.tile_total._value not in ("扫描中", ""))
        ck("排行图已绘制条形",
           lambda: len(app.rank.find_all()) > 6)
        ck("全不选联动统计贴片归零",
           lambda: (app._set_all(False), app.tile_sel._value == "0 B")[1])
        ck("全选恢复勾选并累加预估释放",
           lambda: (app._set_all(True), app.tile_sel._value != "0 B")[1])

        def _mem_chart():
            app.mem_hist = [(40 + (i * 7) % 50, (40 + i) * 1024 ** 3)
                            for i in range(40)]
            app._draw_chart()
            items = app.chart.find_all()
            kinds = {app.chart.type(i) for i in items}
            return len(items) >= 10 and "polygon" in kinds and "line" in kinds

        ck("内存图表绘制（面积+折线+图例）", _mem_chart)
        ck("实时贴片显示内存与 C: 可用",
           lambda: app.tile_mem._value.isdigit()
           and app.tile_cfree._value not in ("", "—"))
        ck("顶栏数据胶囊已刷新",
           lambda: "C: 可用" in app.pill_disk._text
           and "内存" in app.pill_mem._text)
        ck("进程表已填充",
           lambda: len(app.proc_tree.get_children()) > 0)
        ck("进程统计文案已更新",
           lambda: "可结束进程" in app.proc_hint.cget("text"))
        ck("大文件阈值绑定到文本变量（未被占位符污染）",
           lambda: isinstance(app.big_thr, tk.StringVar)
           and app.big_thr.get().isdigit())
        ck("大文件表格套用统一 Treeview 样式",
           lambda: str(app.big_tree.cget("style")) == "Fresh.Treeview")
        ck("进程表格套用统一 Treeview 样式",
           lambda: str(app.proc_tree.cget("style")) == "Fresh.Treeview")
        ck("滚动条套用细滚动条样式（Slim）",
           lambda: any(str(w.cget("style")) == "Slim.Vertical.TScrollbar"
                       for w in app.inner.master.master.winfo_children()
                       if isinstance(w, ttk.Scrollbar)))
        ck("自动清理页使用统一样式勾选框（FreshCheck）",
           lambda: len(_find_widgets(app.tab_auto, FreshCheck)) >= 4)
        ck("排除页保留路径列表可增删",
           lambda: (app.ex_path_entry.insert(0, "D:\\__smoke__"),
                    app.ex_add_path(),
                    app.ex_paths.size() >= 1,
                    app.ex_paths.delete(0, "end"),
                    app.ex_paths.size() == 0)[4])
        ck("排除页列表已按统一风格包裹（内嵌面板底）",
           lambda: any((isinstance(w, tk.Frame)
                        and str(w.cget("bg")) == _c("panel"))
                       for w in (app.ex_paths.master.winfo_children()
                                 + [app.ex_paths.master])))

        def _dlg():
            """离屏构建一次确认框警告条，验证绘制图元。"""
            t = tk.Toplevel(root)
            try:
                t.attributes("-alpha", 0.0)
            except Exception:
                pass
            cv = tk.Canvas(t, height=54, width=420, bg=_c("card"),
                           highlightthickness=0)
            cv.pack()
            t.update_idletasks()
            app._warn_strip(cv)
            n = len(cv.find_all())
            t.destroy()
            return n >= 3

        ck("确认框警告条绘制（柔和红底圆角块）", _dlg)
        ck("次级按钮统一走 BTN2/TINT 令牌，无散落硬编码",
           lambda: BTN2 == TINT and BTN2_H == TINT2 and BTN2_FG == BLUE_D
           and TINT == T["tint"] and LINE == T["line"] and GRID == T["grid"])
        ck("卡片圆角/间距来自全局标度（非魔数）",
           lambda: R == R_CARD and PAD == SP["l"] + 2 and GAP == SP["m"])
        ck("自绘组件宽度属性未与 tkinter 内部冲突（_w 不再是控件路径）",
           lambda: isinstance(app.tile_total.w_px, (int, float))
           and isinstance(app.tabs._items[0]["cv"].w_px, (int, float))
           and not isinstance(app.tile_total.w_px, str))

        # ---------------- 布局体检：本轮「文字挤成一团 / 内容被挤出窗口」的回归防线 ----------------
        def _layout_fits():
            """逐页切换，断言页面「直接子块」完整落在页面可视区内。

            磁盘页底部那行「全选 / 一键清理」曾被整体挤出窗口，就是页面内容
            的纵向请求超过可视高度导致的 —— 这里直接守住这条底线。
            """
            bad = []
            for i, pg in enumerate(app.stack.pages()):
                app._on_tab(i)
                root.update_idletasks()
                ph = pg.winfo_height()
                if ph < 50:
                    continue
                for ch in pg.winfo_children():
                    if not ch.winfo_ismapped():
                        continue
                    bottom = ch.winfo_y() + ch.winfo_height()
                    if bottom > ph + 2:
                        bad.append("tab%d 子块底部 %d > 页面高 %d" % (i, bottom, ph))
            if bad:
                _dbg.append("LAYOUT_OVERFLOW: " + " | ".join(bad[:3]))
                return False
            return True

        ck("各标签页内容不超出可视高度（无子块被挤出窗口）", _layout_fits)

        def _footer_not_squeezed():
            """任何「非弹性」控件都不能被压扁：实际高度必须 ≥ 自身请求高度。

            packer 按调用顺序分配空间，后 pack 的控件在空间不足时会被削减。
            已经踩过两次：
              · 页面底部那行「全选 / 一键清理」最后 pack，被弹性内容区挤成半高；
              · 排除设置页列表下方的输入行放在 Listbox 之后 pack，只露出半行文字。
            这里递归检查所有未声明 expand 的控件，把这类回归一次性挡住。
            """
            bad = []

            def walk(w, path, depth=0, parent=None):
                for c in w.winfo_children():
                    if not c.winfo_ismapped():
                        continue
                    try:
                        ex = int(c.pack_info().get("expand", 0)) == 1
                    except Exception:
                        ex = False
                    h, need = c.winfo_height(), c.winfo_reqheight()
                    # RoundedCard 及其内部的自绘背景 Canvas：请求高度是
                    # tk.Canvas 的默认 265px，实际高度由外部布局分配 ——
                    # 这属于设计如此，不算「被压缩」。
                    flexible = (ex or isinstance(c, RoundedCard)
                                or (isinstance(parent, RoundedCard)
                                    and isinstance(c, tk.Canvas)))
                    if not flexible and need > 20 and h < need - 1:
                        bad.append("%s%s 高 %d < 请求 %d"
                                   % (path, c.winfo_class(), h, need))
                    if depth < 5 and len(bad) < 6:
                        walk(c, path + c.winfo_class() + ">", depth + 1, c)

            for i, pg in enumerate(app.stack.pages()):
                app._on_tab(i)
                root.update_idletasks()
                walk(pg, "tab%d/" % i)
            if bad:
                _dbg.append("SQUEEZED: " + " | ".join(bad[:4]))
                return False
            return True

        ck("非弹性控件均未被压扁（实际高度 ≥ 请求高度）", _footer_not_squeezed)

        def _text_in_bounds():
            """所有 Canvas 的文字图元都必须落在画布宽度内。

            长分类名 / 长路径直接压到右侧「项数 · 大小」上，就是靠这条断言发现的。
            注意：失败时必须返回 False（返回错误字符串会被 ck 当成 truthy 判成 PASS）。
            """
            bad = []
            for cv in _find_widgets(root, tk.Canvas):
                try:
                    cw, chh = cv.winfo_width(), cv.winfo_height()
                except Exception:
                    continue
                if cw < 40 or chh < 20 or not cv.winfo_ismapped():
                    continue
                for it in cv.find_all():
                    if cv.type(it) != "text":
                        continue
                    bb = cv.bbox(it)
                    if not bb:
                        continue
                    if bb[2] > cw + 1 or bb[0] < -1:
                        bad.append("%s(%s)「%s」%.0f..%.0f 越出画布宽 %d"
                                   % (cv.winfo_class(), cv.winfo_name(),
                                      cv.itemcget(it, "text"), bb[0], bb[2], cw))
            if bad:
                _dbg.append("TEXT_OUT_OF_BOUNDS: " + " | ".join(bad[:6]))
                return False
            return True

        ck("Canvas 文字不越出画布宽度（长文本已截断）", _text_in_bounds)

        def _ellipsize_works():
            """窄宽度下的长分类名必须真正截断，且容器自适应宽度不放任溢出。

            注意：这里把测试行挂到已映射的页面并用 place 强制宽度，
            用 Toplevel + geometry 会因为几何求解未完成而拿到 winfo_width()==1。
            """
            r = CheckRow(app.tab_disk,
                         name="系统临时: " + "X" * 60 + "\\very\\long\\tail",
                         count=1234, size="999.99 MB", kind="系统临时",
                         variable=tk.BooleanVar(value=True), outer=CARD)
            r.place(x=0, y=0, width=300, height=42)
            root.update_idletasks()
            r._draw()
            cw = max(r.winfo_width(), 200)
            txts = [i for i in r.find_all() if r.type(i) == "text" and r.bbox(i)]
            names = [r.itemcget(i, "text") for i in txts]
            ok = (any(n.endswith("…") for n in names)
                  and all(r.bbox(i)[2] <= cw + 1 for i in txts))
            if not ok:
                _dbg.append("cw=%d winfo_w=%d names=%r bboxes=%r"
                            % (cw, r.winfo_width(), names,
                               [r.bbox(i) for i in txts]))
            r.destroy()
            return ok

        ck("窄宽度下长分类名按像素截断（不压右侧项数/大小）", _ellipsize_works)

        def _btn_width_sane():
            """按钮宽度必须容得下自己的文字 —— w= 是字符数语义，不是像素。"""
            bad = []
            for b in _find_widgets(root, PillButton):
                need = _measure(b._font, b._text) + 8
                if b.w_px < need:
                    bad.append("%r 宽 %d < 需 %d" % (b._text, b.w_px, need))
            if bad:
                _dbg.append("BTN_TOO_NARROW: " + " | ".join(bad[:4]))
                return False
            return True

        ck("所有按钮宽度容得下自身文字（w 按字宽换算）", _btn_width_sane)

        def _stack_switches():
            """页面栈切换后，只有当前页浮在最上层。"""
            app._on_tab(3)
            root.update_idletasks()
            top = app.stack.winfo_children()[-1]
            ok_top = app.stack.pages()[3].winfo_y() == 0
            app._on_tab(0)
            root.update_idletasks()
            return ok_top and app.stack.pages()[0].winfo_y() == 0 and top is not None

        ck("页面栈切换正常（place 叠放 + tkraise）", _stack_switches)

        if _dbg:
            _safe_write("_layout_debug.txt", "\n".join(_dbg))

    # 阶段机：0 等扫描 → 1 等进程 → 2 断言并退出
    st = {"stage": 0, "t": time.time()}

    def stage():
        if st["stage"] == 0:
            if app.categories or time.time() - st["t"] > 30:
                st["stage"] = 1
                st["t"] = time.time()
        elif st["stage"] == 1:
            if app.proc_tree.get_children() or time.time() - st["t"] > 20:
                if os.environ.get("CCD_LAYOUT_DUMP"):
                    _dump_layout()
                run_checks()
                root.quit()
                return
        root.after(200, stage)

    root.after(300, stage)
    root.mainloop()
    root.destroy()
    return checks


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--guitest" in sys.argv:
        guitest()
    elif "--check-env" in sys.argv:
        import traceback
        try:
            import env_probe
            out = env_probe.report()
            try:
                print(out)
            except Exception:
                pass
            _safe_write("_check_env.txt", out)
        except Exception:
            _safe_write("_check_env.txt", "CHECK_ENV_ERROR\n" + traceback.format_exc())
    elif "--autorun" in sys.argv:
        autorun()
    else:
        start_gui()
