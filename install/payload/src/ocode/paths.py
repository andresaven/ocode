from pathlib import Path
import os


def ocode_root() -> Path:
    override = os.environ.get("OCODE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(r"C:\entorno_desarrollo\ocode")


class Paths:
    def __init__(self, home: Path | None = None):
        self.home = (home or ocode_root()).resolve()

        self.app_home = self.home / "app"
        self.defaults_dir = self.app_home / "defaults"
        self.sensitive_empty_dir = self.app_home / "_sensitive_empty"
        self.web_proxy_script = self.app_home / "src" / "ocode" / "web_proxy.mjs"
        self.instructions_script = self.app_home / "src" / "ocode" / "instructions.mjs"

        self.config_dir = self.home / "config"
        self.projects_dir = self.home / "projects"
        self.runtimes_dir = self.home / "runtimes"
        self.logs_dir = self.home / "logs"

        self.global_config = self.config_dir / "config.json"
        self.opencode_config = self.config_dir / "opencode.jsonc"
        self.global_agents = self.config_dir / "AGENTS.md"
        self.global_skills = self.config_dir / "skills"
        self.secrets_dir = self.config_dir / "secrets"

        self.default_agents = self.defaults_dir / "AGENTS.md"
        self.default_opencode_config = self.defaults_dir / "opencode.jsonc"
        self.default_skills = self.defaults_dir / "skills"
        self.default_project_agents = self.defaults_dir / "project-AGENTS.md"
        self.default_project_config = self.defaults_dir / "project-opencode.jsonc"

    def ensure_dirs(self):
        for p in (
            self.app_home,
            self.config_dir,
            self.projects_dir,
            self.runtimes_dir,
            self.logs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        """Resolve a project by internal id regardless of its human folder name."""
        direct = self.projects_dir / project_id
        if direct.exists():
            return direct

        if self.projects_dir.exists():
            for directory in self.projects_dir.iterdir():
                if not directory.is_dir():
                    continue
                project_json = directory / "project.json"
                if not project_json.is_file():
                    continue
                try:
                    import json
                    data = json.loads(project_json.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if data.get("project_id") == project_id:
                    return directory

        return direct

    def project_json(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def project_sensitive_paths(self, project_id: str) -> Path:
        from .model import SENSITIVE_PATHS_FILENAME
        return self.project_dir(project_id) / SENSITIVE_PATHS_FILENAME

    def project_agents(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "AGENTS.md"

    def project_skills(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "skills"

    def project_config(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "opencode.jsonc"

    def project_opencode_config(self, project_id: str) -> Path:
        return self.project_config(project_id)
