from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def user_path() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
        try:
            value, _ = winreg.QueryValueEx(key, "Path")
            return value
        except FileNotFoundError:
            return ""


def set_user_path(value: str) -> None:
    import ctypes
    import winreg
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)

    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        "Environment",
        SMTO_ABORTIFHUNG,
        5000,
        None,
    )


def norm(value: str) -> str:
    return str(Path(value)).rstrip("\\/").lower()


def main() -> int:
    install_dir = Path(__file__).resolve().parent
    root = install_dir.parent
    app = root / "app"
    bin_dir = app / "bin"

    remove_local = "--remove-local-data" in sys.argv[1:]

    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "ocode"])

    current = user_path()
    entries = [
        x for x in current.split(";")
        if x.strip() and norm(x.strip()) != norm(str(bin_dir))
    ]
    set_user_path(";".join(entries))

    if app.exists():
        shutil.rmtree(app)

    if remove_local:
        for name in ("config", "projects", "runtimes", "logs"):
            target = root / name
            if target.exists():
                shutil.rmtree(target)

    print("[ocode] Uninstallation complete.")
    print("[ocode] Installer files were kept.")
    print("[ocode] Container volumes were not removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
