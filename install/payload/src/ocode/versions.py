from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .model import (
    MIN_OPENCODE_VERSION,
    OPENCODE_REGISTRY,
    OPENCODE_REPOSITORY,
    normalize_opencode_version,
)

TOKEN_URL = (
    f"https://{OPENCODE_REGISTRY}/token?"
    f"scope=repository:{{repository}}:pull&service={OPENCODE_REGISTRY}"
)
TAGS_URL = f"https://{OPENCODE_REGISTRY}/v2/{{repository}}/tags/list"
NPM_VERSION_URL = "https://registry.npmjs.org/@opencode%2Fcli/{version}"
OPENCODE_GITHUB_REPO = "anomalyco/opencode"
RELEASE_URL = (
    f"https://api.github.com/repos/{OPENCODE_GITHUB_REPO}/releases/tags/v{{version}}"
)
RELEASES_PAGE = f"https://github.com/{OPENCODE_GITHUB_REPO}/releases"
USER_AGENT = "ocode-version-check"
TIMEOUT_SECONDS = 15
MAX_PAGES = 20

_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionListError(RuntimeError):
    """Fallo al consultar las versiones publicadas de OpenCode."""


def parse_stable_version(tag: str) -> tuple[int, int, int] | None:
    """Return the numeric version of a stable tag, or None otherwise."""
    match = _STABLE_VERSION_RE.fullmatch(tag.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _next_link(value: str) -> str | None:
    for part in value.split(","):
        target, _, params = part.partition(";")
        if 'rel="next"' in params:
            return target.strip().strip("<>")
    return None


def _request_json(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            link = response.headers.get("Link", "")
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, None, {}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VersionListError(
            f"No se pudo consultar el registro de OpenCode: {exc}"
        ) from exc

    try:
        return status, json.loads(payload), {"Link": link}
    except ValueError as exc:
        raise VersionListError(
            "El registro de OpenCode devolvió una respuesta no válida."
        ) from exc


def _request_status(url: str) -> int:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VersionListError(f"No se pudo consultar npm: {exc}") from exc


def npm_version_available(version: str) -> bool:
    """Whether the package version exists in the npm registry."""
    version = normalize_opencode_version(version)
    status = _request_status(NPM_VERSION_URL.format(version=version))
    if status == 200:
        return True
    if status == 404:
        return False
    raise VersionListError(
        f"No se pudo comprobar OpenCode {version} en npm (HTTP {status})."
    )


def _release_highlights(body: str, limit: int = 1200) -> str:
    text = body.strip()
    marker = "### Highlights"
    index = text.find(marker)
    if index >= 0:
        text = text[index + len(marker):].lstrip()
        end = text.find("\n### ")
        if end >= 0:
            text = text[:end]
    text = text.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def release_summary(version: str) -> tuple[str, str]:
    """Best-effort release highlights and URL for an OpenCode version.

    Older tags might not have a GitHub release; the caller can still open the
    releases page.
    """
    version = normalize_opencode_version(version)
    try:
        status, data, _ = _request_json(
            RELEASE_URL.format(version=version),
            {"Accept": "application/vnd.github+json"},
        )
    except VersionListError:
        return "", RELEASES_PAGE
    if status != 200 or not isinstance(data, dict):
        return "", RELEASES_PAGE
    url = str(data.get("html_url") or RELEASES_PAGE)
    body = str(data.get("body") or "")
    return (_release_highlights(body) if body else ""), url


def list_remote_versions() -> list[str]:
    """Published stable OpenCode versions, newest first.

    The registry requires an anonymous pull token even to list tags. If the
    registry is unreachable the caller can still type a version manually.
    """
    status, data, _ = _request_json(TOKEN_URL.format(repository=OPENCODE_REPOSITORY))
    if status != 200 or not isinstance(data, dict) or not data.get("token"):
        raise VersionListError(
            "No se pudo obtener acceso de lectura al registro de OpenCode."
        )
    headers = {"Authorization": f"Bearer {data['token']}"}
    url = TAGS_URL.format(repository=OPENCODE_REPOSITORY) + "?n=1000"
    versions: set[str] = set()
    for _ in range(MAX_PAGES):
        status, data, extra = _request_json(url, headers)
        if status != 200 or not isinstance(data, dict):
            raise VersionListError(
                "El registro de OpenCode no devolvió la lista de versiones."
            )
        for tag in data.get("tags") or []:
            tag = str(tag)
            parsed = parse_stable_version(tag)
            if parsed is not None and parsed >= MIN_OPENCODE_VERSION:
                versions.add(tag)
        next_page = _next_link(extra.get("Link", ""))
        if not next_page:
            break
        url = (
            next_page
            if next_page.startswith("http")
            else f"https://{OPENCODE_REGISTRY}{next_page}"
        )
    return sorted(
        versions,
        key=lambda version: parse_stable_version(version) or (0, 0, 0),
        reverse=True,
    )
