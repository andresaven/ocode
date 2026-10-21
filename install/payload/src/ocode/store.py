from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from .model import GlobalConfig, ProjectConfig, SensitivePathsError
from .paths import Paths

SENSITIVE_PERMISSION_PATTERNS = ['*src/main/resources/configuration/des*', '*src/main/resources/configuration/pre*', '*src/main/resources/configuration/prod*', '*src/main/resources/certs*', '*src/main/resources/application-desa.yaml*', '*src/main/resources/application-pre.yaml*', '*src/main/resources/application-pro.yaml*', '*src/main/resources/db/production*', '*src/main/resources/db/db.changelog-desa.yaml*', '*src/main/resources/db/db.changelog-pre.yaml*', '*src/main/resources/db/liquibase-desa.properties*', '*src/main/resources/db/liquibase-pre.properties*', '*k8s*']

BUILTIN_SENSITIVE_PATHS = ['**/src/main/resources/configuration/des', '**/src/main/resources/configuration/pre', '**/src/main/resources/configuration/prod', '**/src/main/resources/certs', '**/src/main/resources/application-desa.yaml', '**/src/main/resources/application-pre.yaml', '**/src/main/resources/application-pro.yaml', '**/src/main/resources/db/production', '**/src/main/resources/db/db.changelog-desa.yaml', '**/src/main/resources/db/db.changelog-pre.yaml', '**/src/main/resources/db/liquibase-desa.properties', '**/src/main/resources/db/liquibase-pre.properties', '**/k8s']


def _ensure_permission_section(
    text: str,
    section: str,
    next_section: str | None,
    patterns: list[str],
) -> str:
    marker = f'"{section}": {{'
    start = text.find(marker)
    if start < 0:
        return text

    end = (
        text.find(f'"{next_section}": {{', start)
        if next_section
        else len(text)
    )
    if end < 0:
        end = len(text)

    section_text = text[start:end]
    missing = [
        pattern
        for pattern in patterns
        if f'"{pattern}": "deny"' not in section_text
    ]
    if not missing:
        return text

    # Prefer inserting after a general allow if present. Otherwise insert
    # immediately after the section opening; this also works for deny-only
    # project permission blocks.
    allow_marker = '"*": "allow",'
    allow_pos = text.find(allow_marker, start, end)
    if allow_pos >= 0:
        insert_pos = allow_pos + len(allow_marker)
    else:
        insert_pos = start + len(marker)

    insertion = "".join(
        f'\n      "{pattern}": "deny",'
        for pattern in missing
    )
    return text[:insert_pos] + insertion + text[insert_pos:]


def _ensure_sensitive_permissions(path: Path) -> None:
    """Add ocode built-in sensitive path denies without replacing user config."""
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    original = text

    text = _ensure_permission_section(
        text, "read", "edit", SENSITIVE_PERMISSION_PATTERNS
    )
    text = _ensure_permission_section(
        text, "edit", "list", SENSITIVE_PERMISSION_PATTERNS
    )

    if '"list": {' in text:
        text = _ensure_permission_section(
            text, "list", "bash", SENSITIVE_PERMISSION_PATTERNS
        )

    if '"list": {' not in text:
        bash_marker = '    "bash": {'
        if bash_marker in text:
            lines = [
                '    "list": {',
                '      "*": "allow",',
            ]
            for pattern in SENSITIVE_PERMISSION_PATTERNS:
                lines.append(f'      "{pattern}": "deny",')
            lines[-1] = lines[-1].rstrip(",")
            lines.append("    },")
            block = "\n".join(lines) + "\n"
            text = text.replace(bash_marker, block + bash_marker, 1)

    if text != original:
        backup = path.with_name(path.name + ".bak-pre-0.4.3")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(text, encoding="utf-8")



def _ensure_project_sensitive_permissions(path: Path) -> None:
    """Ensure project config reinforces built-in sensitive path restrictions."""
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")

    if '"permission"' not in text:
        permission = {
            "read": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
            "edit": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
            "list": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
        }
        block = json.dumps(
            {"permission": permission},
            indent=2,
            ensure_ascii=False,
        )
        inner = block.strip()[1:-1].strip()

        close = text.rfind("}")
        if close >= 0:
            prefix = text[:close].rstrip()
            if not prefix.endswith("{") and not prefix.endswith(","):
                prefix += ","
            text = prefix + "\n  " + inner.replace("\n", "\n  ") + "\n}\n"
            path.write_text(text, encoding="utf-8")
            return

    _ensure_sensitive_permissions(path)


def _ensure_project_skills_source(path: Path) -> None:
    """Add the external per-project skill source without replacing user config."""
    text = path.read_text(encoding="utf-8")
    entry = "/etc/ocode/project-skills"
    marker = '"skills"'
    start = text.find(marker)
    if start >= 0:
        match = re.match(r"\s*:\s*\[", text[start + len(marker):])
        if not match:
            raise RuntimeError(f"El campo skills no es un array en {path}")
        opening = start + len(marker) + match.end() - 1
        depth = 0
        quoted = False
        escaped = False
        closing = -1
        for index in range(opening, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise RuntimeError(f"Array skills sin cerrar en {path}")
        if entry in text[opening:closing]:
            return
        content = text[opening + 1:closing]
        separator = "" if not content.strip() or content.rstrip().endswith(",") else ","
        updated = content.rstrip() + separator + f'\n    "{entry}",\n  '
        text = text[:opening + 1] + updated + text[closing:]
    else:
        closing = text.rfind("}")
        if closing < 0:
            raise RuntimeError(f"JSONC inválido en {path}")
        prefix = text[:closing].rstrip()
        separator = "" if prefix.endswith("{") or prefix.endswith(",") else ","
        text = prefix + separator + f'\n  "skills": ["{entry}"],\n' + text[closing:]
    backup = path.with_name(path.name + ".bak-pre-project-skills")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")


def _ensure_external_edit_rules(path: Path) -> None:
    """Allow only the two ocode-managed project resources to be edited."""
    text = path.read_text(encoding="utf-8")
    rules = [
        {"action": action, "resource": resource, "effect": "allow"}
        for resource in ("/etc/ocode/project-skills/*", "/etc/ocode/project-AGENTS.md")
        for action in ("external_directory", "read", "edit")
    ]
    marker = '"permissions"'
    start = text.find(marker)
    if start >= 0:
        match = re.match(r"\s*:\s*\[", text[start + len(marker):])
        if not match:
            raise RuntimeError(f"El campo permissions no es un array en {path}")
        opening = start + len(marker) + match.end() - 1
        depth = 0
        quoted = escaped = False
        closing = -1
        for index in range(opening, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise RuntimeError(f"Array permissions sin cerrar en {path}")
        section = text[opening:closing]
        def already_present(rule):
            expression = (
                r'\{\s*"action"\s*:\s*"' + re.escape(rule["action"]) +
                r'"\s*,\s*"resource"\s*:\s*"' + re.escape(rule["resource"]) +
                r'"\s*,\s*"effect"\s*:\s*"allow"\s*\}'
            )
            return re.search(expression, section) is not None

        missing = [rule for rule in rules if not already_present(rule)]
        if not missing:
            return
        content = text[opening + 1:closing]
        separator = "" if not content.strip() or content.rstrip().endswith(",") else ","
        inserted = ",\n    ".join(json.dumps(rule, ensure_ascii=False) for rule in missing)
        text = text[:opening + 1] + content.rstrip() + separator + "\n    " + inserted + ",\n  " + text[closing:]
    else:
        closing = text.rfind("}")
        if closing < 0:
            raise RuntimeError(f"JSONC inválido en {path}")
        prefix = text[:closing].rstrip()
        separator = "" if prefix.endswith("{") or prefix.endswith(",") else ","
        serialized = ",\n    ".join(json.dumps(rule, ensure_ascii=False) for rule in rules)
        text = prefix + separator + '\n  "permissions": [\n    ' + serialized + "\n  ],\n" + text[closing:]
    backup = path.with_name(path.name + ".bak-pre-project-skills")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")

def _project_folder_base(name: str, branch: str) -> str:
    raw = f"{name}-{branch}".strip()
    # Windows-invalid filename characters and control chars.
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", raw)
    safe = re.sub(r"\s+", " ", safe).strip().rstrip(". ")
    return safe or "project"


def _available_project_folder(projects_dir: Path, base: str, current: Path | None = None) -> Path:
    candidate = projects_dir / base
    if current is not None and candidate.resolve() == current.resolve():
        return candidate
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = projects_dir / f"{base}-{index}"
        if current is not None and candidate.resolve() == current.resolve():
            return candidate
        if not candidate.exists():
            return candidate
        index += 1


class Store:
    def __init__(self, paths: Paths):
        self.paths = paths

    def initialized(self) -> bool:
        return self.paths.global_config.exists()

    def _require_default(self, path: Path) -> None:
        if not path.exists():
            raise RuntimeError(f"Falta un fichero distribuido por ocode: {path}")

    def _migrate_project_directories(self) -> None:
        """Rename legacy UUID folders to Proyecto-Rama without changing project ids."""
        if not self.paths.projects_dir.exists():
            return

        directories = [
            p for p in self.paths.projects_dir.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        ]

        for directory in directories:
            try:
                project = ProjectConfig.from_file(directory / "project.json")
            except SensitivePathsError:
                raise
            except Exception:
                continue

            desired_base = _project_folder_base(project.name, project.branch)
            if directory.name.casefold() == desired_base.casefold():
                continue

            target = _available_project_folder(
                self.paths.projects_dir,
                desired_base,
                current=directory,
            )

            if target.resolve() != directory.resolve():
                directory.rename(target)

    def initialize(self) -> GlobalConfig:
        self.paths.ensure_dirs()
        self._migrate_project_directories()

        for path in (
            self.paths.default_agents,
            self.paths.default_opencode_config,
            self.paths.default_project_agents,
            self.paths.default_project_config,
        ):
            self._require_default(path)

        if not self.paths.global_config.exists():
            cfg = GlobalConfig.defaults()
            self.save_global(cfg)
        else:
            cfg = self.load_global()
            # Persist version-added defaults (e.g. new Java profiles) without
            # overwriting user-selected values.
            self.save_global(cfg)

        # Los defaults solo inicializan. Nunca pisan personalizaciones existentes.
        if not self.paths.opencode_config.exists():
            shutil.copy2(self.paths.default_opencode_config, self.paths.opencode_config)

        if not self.paths.global_agents.exists():
            shutil.copy2(self.paths.default_agents, self.paths.global_agents)

        self.paths.global_skills.mkdir(parents=True, exist_ok=True)
        _ensure_project_skills_source(self.paths.opencode_config)
        _ensure_external_edit_rules(self.paths.opencode_config)

        # Keep user configuration, but append mandatory ocode sensitive denies
        # if this installation predates them.
        _ensure_sensitive_permissions(self.paths.opencode_config)

        # Normalize legacy built-ins and add newly introduced built-ins.
        # User-added entries are preserved.
        legacy_defaults = {
            "src/main/resources/configuration/des": "**/src/main/resources/configuration/des",
            "src/main/resources/configuration/pre": "**/src/main/resources/configuration/pre",
            "src/main/resources/configuration/prod": "**/src/main/resources/configuration/prod",
            "src/main/resources/certs": "**/src/main/resources/certs",
        }

        for project in self.list_projects():
            self.paths.project_skills(project.project_id).mkdir(parents=True, exist_ok=True)
            changed = not self.paths.project_sensitive_paths(project.project_id).exists()
            migrated: list[str] = []

            for value in project.sensitive_paths:
                normalized = legacy_defaults.get(value, value)
                if normalized != value:
                    changed = True
                if normalized not in migrated:
                    migrated.append(normalized)

            # Defaults are applied only during legacy migration. The editable
            # file is authoritative, including intentionally removed rules.
            if not self.paths.project_sensitive_paths(project.project_id).exists():
                for value in BUILTIN_SENSITIVE_PATHS:
                    if value not in migrated:
                        migrated.append(value)
                        changed = True

            if changed:
                project.sensitive_paths = migrated
                self.save_project(project)

            _ensure_project_sensitive_permissions(
                self.paths.project_opencode_config(project.project_id)
            )

        return cfg

    def load_global(self) -> GlobalConfig:
        data = json.loads(self.paths.global_config.read_text(encoding="utf-8"))
        return GlobalConfig.from_dict(data)

    def save_global(self, cfg: GlobalConfig) -> None:
        self.paths.ensure_dirs()
        self.paths.global_config.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_projects(self) -> list[ProjectConfig]:
        projects: list[ProjectConfig] = []
        if not self.paths.projects_dir.exists():
            return projects

        for path in self.paths.projects_dir.glob("*/project.json"):
            try:
                projects.append(ProjectConfig.from_file(path))
            except SensitivePathsError:
                raise
            except Exception:
                continue

        return sorted(projects, key=lambda p: (p.name.lower(), p.branch.lower()))

    def get_project(self, project_id: str) -> ProjectConfig:
        return ProjectConfig.from_file(self.paths.project_json(project_id))

    def create_project(
        self,
        *,
        name: str,
        branch: str,
        workspace: str,
        runtime: str,
        default_mode: str,
        port: int,
        maven_repository: str = "",
        build_paths: list[str] | None = None,
    ) -> ProjectConfig:
        if not 1 <= port <= 65535:
            raise ValueError("Puerto no válido (1–65535).")
        project_id = str(uuid.uuid4())
        global_cfg = self.load_global()
        project = ProjectConfig(
            project_id=project_id,
            name=name,
            branch=branch,
            workspace=str(Path(workspace).resolve()),
            runtime=runtime,
            default_mode=default_mode,
            port=port,
            maven_repository=maven_repository,
            build_paths=list(build_paths or []),
            web_initial_preferences={
                "show_agent": global_cfg.web_show_agent,
                "color_scheme": global_cfg.web_color_scheme,
            },
            sensitive_paths=list(BUILTIN_SENSITIVE_PATHS),
        )

        folder_base = _project_folder_base(name, branch)
        project_dir = _available_project_folder(
            self.paths.projects_dir,
            folder_base,
        )
        project_dir.mkdir(parents=True, exist_ok=False)
        (project_dir / "skills").mkdir()

        project.save(project_dir / "project.json")

        shutil.copy2(
            self.paths.default_project_agents,
            project_dir / "AGENTS.md",
        )
        shutil.copy2(
            self.paths.default_project_config,
            project_dir / "opencode.jsonc",
        )
        return project

    def save_project(self, project: ProjectConfig, *, save_sensitive_paths: bool = False) -> None:
        project.save(
            self.paths.project_json(project.project_id),
            save_sensitive_paths=save_sensitive_paths,
        )

    def delete_project_config(self, project_id: str) -> None:
        directory = self.paths.project_dir(project_id)
        if directory.exists():
            shutil.rmtree(directory)
