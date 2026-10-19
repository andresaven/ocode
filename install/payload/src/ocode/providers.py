"""Clave global de OpenCode Go y su fichero de entorno.

La clave se guarda en `config\\secrets\\opencode-go.env` y se inyecta en cada
contenedor con `--env-file`, de modo que nunca aparece en la línea de comandos
ni en la vista «Comando Docker». Este módulo no registra ni imprime la clave.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ENV_VAR = "OPENCODE_API_KEY"
FILENAME = "opencode-go.env"
MIN_LENGTH = 16


def go_env_file(paths) -> Path:
    """Ruta del fichero de entorno, exista o no."""
    return paths.secrets_dir / FILENAME


def go_key_configured(paths) -> bool:
    return _read_key(go_env_file(paths)) is not None


def go_key_fingerprint(paths) -> str | None:
    key = _read_key(go_env_file(paths))
    if key is None:
        return None
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def go_key_last4(paths) -> str | None:
    key = _read_key(go_env_file(paths))
    if key is None or len(key) < 4:
        return None
    return key[-4:]


def save_go_key(paths, key: str) -> None:
    """Valida y guarda la clave de forma atómica. Nunca incluye la clave en errores."""
    value = _validate(key)
    path = go_env_file(paths)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=".opencode-go-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{ENV_VAR}={value}\n")
        _restrict_permissions(Path(temp_name))
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def clear_go_key(paths) -> None:
    """Elimina la clave; no falla si ya no existe."""
    try:
        go_env_file(paths).unlink()
    except FileNotFoundError:
        pass


def _validate(key: str) -> str:
    value = key.strip()
    if not value:
        raise ValueError("La clave no puede estar vacía.")
    if len(value) < MIN_LENGTH:
        raise ValueError(
            f"La clave debe tener al menos {MIN_LENGTH} caracteres."
        )
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError(
            "La clave no puede contener espacios ni caracteres de control."
        )
    return value


def _read_key(path: Path) -> str | None:
    """Lee la primera entrada válida; un fichero vacío o mal formado no cuenta."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name != ENV_VAR:
            continue
        try:
            valid = _validate(value)
        except ValueError:
            return None
        # No se pasa a Docker un valor con espacios que él leería literalmente.
        return valid if valid == value else None
    return None


def _restrict_permissions(path: Path) -> None:
    """Best effort: limita el fichero al usuario actual en Windows."""
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
