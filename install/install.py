from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def log(message: str) -> None:
    print(f"[ocode] {message}", flush=True)


def user_path() -> str:
    import winreg
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ,
    ) as key:
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
        winreg.SetValueEx(
            key,
            "Path",
            0,
            winreg.REG_EXPAND_SZ,
            value,
        )

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


def add_user_path(path: Path) -> bool:
    current = user_path()
    entries = [x for x in current.split(";") if x.strip()]
    wanted = norm(str(path))

    if any(norm(x.strip()) == wanted for x in entries):
        return False

    separator = ";" if current and not current.endswith(";") else ""
    set_user_path(current + separator + str(path))
    return True


def replace_dir(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def validate_distribution(payload: Path) -> None:
    required_dirs = (
        payload / "src",
        payload / "defaults",
    )
    required_files = (
        payload / "pyproject.toml",
        payload / "src" / "ocode" / "__main__.py",
        payload / "src" / "ocode" / "web_proxy.mjs",
        payload / "src" / "ocode" / "instructions.mjs",
        payload / "defaults" / "AGENTS.md",
        payload / "defaults" / "opencode.jsonc",
        payload / "defaults" / "project-AGENTS.md",
        payload / "defaults" / "project-opencode.jsonc",
    )

    missing = [
        str(path)
        for path in (*required_dirs, *required_files)
        if not path.exists()
    ]

    if missing:
        detail = "\n".join(f"  - {path}" for path in missing)
        raise RuntimeError(
            "Distribution incomplete. Missing required files:\n"
            f"{detail}"
        )


def create_update_launcher(root: Path) -> Path:
    launcher = root / "update.cmd"
    launcher.write_bytes(
        (
            "@echo off\r\n"
            "setlocal\r\n"
            'if not exist "%~dp0install\\install.cmd" (\r\n'
            "  echo [ocode] ERROR: install\\install.cmd is missing. Extract the distribution first.\r\n"
            "  exit /b 1\r\n"
            ")\r\n"
            'call "%~dp0install\\install.cmd" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        ).encode("ascii")
    )
    return launcher


def main() -> int:
    install_dir = Path(__file__).resolve().parent
    root = install_dir.parent
    payload = install_dir / "payload"
    app = root / "app"
    bin_dir = app / "bin"
    config = root / "config"

    log("Starting installation")
    log(f"Root: {root}")
    log(f"Installer: {install_dir}")
    log(f"App: {app}")
    log(f"Config: {config}")

    if os.name != "nt":
        raise RuntimeError("This installer is intended for Windows.")

    # Preflight first: do not touch the existing app until the ZIP is known-good.
    validate_distribution(payload)
    log("Distribution validated")

    for directory in (
        root,
        app,
        bin_dir,
        config,
        root / "projects",
        root / "runtimes",
        root / "logs",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    replace_dir(payload / "src", app / "src")
    log("Installed src")

    replace_dir(payload / "defaults", app / "defaults")
    log("Installed defaults")

    shutil.copy2(
        payload / "pyproject.toml",
        app / "pyproject.toml",
    )
    log("Installed pyproject.toml")

    # README is documentation, not a runtime dependency.
    readme_candidates = (
        payload / "README.md",
        root / "README.md",
    )
    for source in readme_candidates:
        if source.is_file():
            shutil.copy2(source, app / "README.md")
            log(f"Installed README from {source}")
            break
    else:
        log("README not present; continuing")

    log("Installing Python package")
    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "-e",
            str(app),
        ]
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"pip returned exit code {cp.returncode}"
        )

    launcher = bin_dir / "ocode.cmd"
    launcher.write_text(
        "@echo off\r\n"
        f"set OCODE_HOME={root}\r\n"
        f"\"{sys.executable}\" -m ocode %*\r\n",
        encoding="ascii",
    )
    log(f"Launcher created: {launcher}")

    defaults = app / "defaults"
    for name in ("AGENTS.md", "opencode.jsonc"):
        source = defaults / name
        target = config / name

        if target.exists():
            log(f"Keeping existing config\\{name}")
        else:
            shutil.copy2(source, target)
            log(f"Created config\\{name}")

    if add_user_path(bin_dir):
        log(f"Added to user PATH: {bin_dir}")
    else:
        log("User PATH already contains app\\bin")

    update_launcher = create_update_launcher(root)
    log(f"Update launcher created: {update_launcher}")

    log("Installation complete")
    print()
    print("Open a new terminal and run: ocode")
    print("To update later, extract the new distribution here and run update.cmd")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[ocode] ERROR: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise
