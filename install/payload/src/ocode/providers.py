"""Claves globales de proveedores y sus ficheros de entorno.

Cada clave se guarda en `config\\secrets\\<id>.env` y se inyecta en cada
contenedor con `--env-file`, de modo que nunca aparece en la línea de comandos
ni en la vista «Comando Docker». Este módulo no registra ni imprime la clave.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    env_var: str
    filename: str
    label: str
    hint: str


PROVIDERS: dict[str, Provider] = {
    "opencode-go": Provider(
        id="opencode-go",
        name="OpenCode Go",
        env_var="OPENCODE_API_KEY",
        filename="opencode-go.env",
        label="io.ocode.opencode-go",
        hint="opencode-go/<modelo>",
    ),
    "openai": Provider(
        id="openai",
        name="OpenAI",
        env_var="OPENAI_API_KEY",
        filename="openai.env",
        label="io.ocode.openai",
        hint="openai/<modelo>",
    ),
}

MIN_LENGTH = 16


def provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise KeyError(f"Proveedor desconocido: {provider_id}") from None


def env_file(paths, provider_id: str) -> Path:
    """Ruta del fichero de entorno, exista o no."""
    return paths.secrets_dir / provider(provider_id).filename


def key_configured(paths, provider_id: str) -> bool:
    return _read_key(env_file(paths, provider_id), provider(provider_id).env_var) is not None


def key_fingerprint(paths, provider_id: str) -> str | None:
    key = _read_key(env_file(paths, provider_id), provider(provider_id).env_var)
    if key is None:
        return None
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def key_last4(paths, provider_id: str) -> str | None:
    key = _read_key(env_file(paths, provider_id), provider(provider_id).env_var)
    if key is None or len(key) < 4:
        return None
    return key[-4:]


def save_key(paths, provider_id: str, key: str) -> None:
    """Valida y guarda la clave de forma atómica. Nunca incluye la clave en errores."""
    entry = provider(provider_id)
    value = _validate(key)
    path = env_file(paths, provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{entry.id}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{entry.env_var}={value}\n")
        _restrict_permissions(Path(temp_name))
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def clear_key(paths, provider_id: str) -> None:
    """Elimina la clave; no falla si ya no existe."""
    try:
        env_file(paths, provider_id).unlink()
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


def _read_key(path: Path, env_var: str) -> str | None:
    """Lee la primera entrada válida; un fichero vacío o mal formado no cuenta."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name != env_var:
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
