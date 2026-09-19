# -*- coding: utf-8 -*-
"""环境运行库自检与修复（纯标准库 + ctypes，无 GUI / 无第三方依赖）。

目的：让 CleanCDrive 在「缺少运行库」的电脑上也能：
  1. 检测（声明缺什么）
  2. 经用户同意后自动修复（下载官方运行库并以管理员身份静默安装）

典型场景：PyInstaller 单文件 exe 在「从未装过 Visual C++ Redistributable」的
干净 Windows 上双击无反应——根因就是缺 vcruntime140.dll / msvcp140.dll。
本模块既供程序内弹窗调用，也供启动器（PowerShell）参考同样的判定逻辑。
"""
import os
import sys
import tempfile
import threading

# 微软官方「最新 VC++ 2015-2022 x64 可再发行组件」稳定重定向地址
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_REDIST_NAME = "vc_redist.x64.exe"


# --------------------------------------------------------------------------
# 基础探测
# --------------------------------------------------------------------------
def _reg_get(hive, sub, name):
    try:
        import winreg
        with winreg.OpenKey(hive, sub) as h:
            return winreg.QueryValueEx(h, name)[0]
    except Exception:
        return None


def os_version_text():
    wv = sys.getwindowsversion()
    names = {10: "Windows 10/11", 6: "Windows Vista/7/8"}
    base = names.get(wv.major, "Windows %d" % wv.major)
    return "%s (build %d.%d.%d)" % (base, wv.major, wv.minor, wv.build)


def machine_arch():
    import platform
    return platform.machine().lower()


def _dll_present(name):
    """指定 DLL 是否存在于系统目录（System32 / SysWOW64）。"""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    for sub in ("System32", "SysWOW64"):
        p = os.path.join(sysroot, sub, name)
        if os.path.exists(p):
            return True
    # 也允许 PATH 命中
    if any(os.path.exists(os.path.join(d, name))
           for d in os.environ.get("PATH", "").split(";") if d):
        return True
    return False


def vc_redist_x64():
    """检测 Microsoft Visual C++ Redistributable 2015-2022 (x64) 是否已安装。

    返回 (已安装: bool, 版本字符串或 None)。
    判定：注册表 Runtimes\\x64 的 Installed==1，或关键 DLL 文件确实存在。
    """
    import winreg
    for sub in (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as h:
                inst = winreg.QueryValueEx(h, "Installed")[0]
                ver = winreg.QueryValueEx(h, "Version")[0]
                if inst == 1:
                    return True, ver
        except Exception:
            pass
    # 回退：若运行所需 DLL 都齐了，也算通过
    if _dll_present("vcruntime140.dll") and _dll_present("msvcp140.dll"):
        return True, "dll-present"
    return False, None


def _check_temp():
    """临时目录是否可写、可用空间（MB）。"""
    td = tempfile.gettempdir()
    try:
        probe = os.path.join(td, ".ccd_probe_%d.tmp" % os.getpid())
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        writable = True
    except Exception:
        writable = False
    free = -1.0
    try:
        import ctypes
        free_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(td), ctypes.byref(free_bytes), None, None)
        free = free_bytes.value / (1024.0 * 1024.0)
    except Exception:
        pass
    return writable, free


# --------------------------------------------------------------------------
# 扫描：返回问题清单
# --------------------------------------------------------------------------
def scan():
    """返回 issue 字典列表。每个 issue:
        key     : 唯一标识
        level   : "ok" | "warn" | "blocker"
        title   : 简短标题
        detail  : 说明
        auto    : 是否可一键自动修复（仅 warn/blocker 可能为 True）
        fix     : 修复方式描述
    """
    import platform  # noqa  (延迟导入，便于在无 tkinter 环境单独使用)
    issues = []

    # 1) 架构
    mach = machine_arch()
    if mach not in ("amd64", "x86_64", "x64"):
        issues.append(dict(
            key="arch", level="blocker", title="系统架构不支持", auto=False,
            detail="本程序为 64 位 Windows 编译，当前系统架构为 %s，无法运行。"
                   % platform.machine(),
            fix="请在下载页选择对应架构版本，或联系作者获取适配版本。"))

    # 2) Windows 版本
    wv = sys.getwindowsversion()
    if wv.major < 10:
        issues.append(dict(
            key="os", level="blocker", title="Windows 版本过低", auto=False,
            detail="需要 Windows 10 或更高版本（当前为 %s）。" % os_version_text(),
            fix="升级操作系统到 Windows 10/11 后重试。"))

    # 3) Visual C++ 运行库（最常见的「打不开」根因）
    ok, ver = vc_redist_x64()
    if ok:
        issues.append(dict(
            key="vcredist", level="ok", title="Visual C++ 运行库", auto=False,
            detail="已安装（%s）。" % ver))
    else:
        issues.append(dict(
            key="vcredist", level="warn", auto=True, title="缺少 Visual C++ 运行库",
            detail="程序运行依赖 Microsoft Visual C++ Redistributable 2015–2022 (x64)，"
                   "本机未检测到。这正是它在部分电脑上「双击无反应」的常见原因。",
            fix="联网并在管理员授权下，自动下载并静默安装官方运行库。"))

    # 4) 临时目录（PyInstaller 单文件需在临时目录解压）
    writable, free = _check_temp()
    if not writable:
        issues.append(dict(
            key="temp", level="blocker", title="临时目录不可写", auto=False,
            detail="程序启动需在临时目录（%s）解压自身，当前无法写入。" % tempfile.gettempdir(),
            fix="检查磁盘权限或杀毒软件拦截，确保临时目录可写后重试。"))
    elif 0 <= free < 200:
        issues.append(dict(
            key="temp", level="warn", auto=False,
            title="临时目录空间不足",
            detail="临时目录可用空间约 %.0f MB，建议至少保留 200 MB 以供解压。" % free,
            fix="清理系统盘空间后重试。"))

    return issues


# --------------------------------------------------------------------------
# 修复：下载 + 管理员静默安装 VC++ 运行库
# --------------------------------------------------------------------------
def _download(url, dest, on_progress=None):
    """下载文件，on_progress(done, total, phase) 回调。phase: downloading/finished"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "CleanCDrive-EnvProbe"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", "0") or "0")
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total, "downloading")
    if on_progress:
        on_progress(done, total, "finished")


def _run_elevated_and_wait(exe, params):
    """以管理员身份运行 exe 并等待结束，返回退出码；无法启动返回 None。

    使用 ShellExecuteEx + SEE_MASK_NOCLOSEPROCESS 获取进程句柄，
    再用 WaitForSingleObject 等待，从而拿到真实退出码（0/3010=成功）。
    """
    import ctypes
    from ctypes import wintypes

    SEE_MASK_NOCLOSEPROCESS = 0x00000040

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hKeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"          # 触发 UAC 提权
    sei.lpFile = exe
    sei.lpParameters = params
    sei.nShow = 1                  # SW_SHOWNORMAL

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return None
    hproc = sei.hProcess
    if not hproc:
        return 0
    ctypes.windll.kernel32.WaitForSingleObject(hproc, 0xFFFFFFFF)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(hproc, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(hproc)
    return code.value


def install_vc_redist(on_progress=None):
    """下载并管理员静默安装 VC++ 运行库。

    on_progress(done, total, phase) 用于 UI 进度。
    返回 True（成功，含需重启的 3010）/ False（失败）/ 字符串（错误信息）。
    """
    dest = os.path.join(tempfile.gettempdir(), VC_REDIST_NAME)
    try:
        if on_progress:
            on_progress(0, 1, "downloading")
        _download(VC_REDIST_URL, dest, on_progress)
        if on_progress:
            on_progress(1, 1, "installing")
        code = _run_elevated_and_wait(dest, "/quiet /norestart")
        if code is None:
            return "无法启动安装程序（可能你取消了 UAC 授权）"
        # 0=成功, 3010=成功但需重启
        if code in (0, 3010):
            try:
                os.remove(dest)
            except Exception:
                pass
            return True
        return "安装程序返回错误码 %s" % code
    except Exception as e:  # noqa
        return "安装失败：%s" % e


# --------------------------------------------------------------------------
# 文本报告（供 --check-env 与启动器参考）
# --------------------------------------------------------------------------
def report():
    lines = ["=== CleanCDrive 环境自检 ===",
             "系统    : %s" % os_version_text(),
             "架构    : %s" % machine_arch(),
             "临时目录: %s" % tempfile.gettempdir()]
    for it in scan():
        mark = {"ok": "✓", "warn": "!", "blocker": "✗"}.get(it["level"], "?")
        lines.append("  [%s] %s" % (mark, it["title"]))
        if it.get("detail"):
            lines.append("      %s" % it["detail"])
        if it.get("auto"):
            lines.append("      → 可一键修复：%s" % it.get("fix", ""))
    blockers = [i for i in scan() if i["level"] == "blocker"]
    warns = [i for i in scan() if i["level"] == "warn"]
    lines.append("结论    : %s"
                 % ("环境就绪" if not (blockers or warns)
                    else "%d 个阻断项, %d 个警告项" % (len(blockers), len(warns))))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
