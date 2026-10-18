"""Runtime configuration, read from a project `.env` file and environment variables.

Secrets are never stored in source code. API keys come from `.env` (git-ignored)
or from the environment, and are handed to the provider SDKs at runtime.

Precedence: a non-empty value in `.env` wins over an inherited environment
variable. This stops an old key set system-wide (for example a Windows user
variable) from silently replacing the key written in `.env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
TEMPLATE_ENV_FILE = PROJECT_ROOT / ".env.example"

# The eight logical agents, in the order a story first meets them. Keys double as role names.
AGENT_LABELS = {
    "world_architect": "World Architect",
    "plot_planner": "Plot Planner",
    "intent_interpreter": "Intent Interpreter",
    "scene_director": "Scene Director",
    "character_agent": "Character Agent",
    "narrator": "Narrator",
    "narrative_critic": "Narrative Critic",
    "chronicler": "Chronicler",
}
ROLES = tuple(AGENT_LABELS)
# Older .env files used four role names; each still sets the agents that inherited that job.
LEGACY_EFFORT_VARS = {
    "EFFORT_ARCHITECT": ("world_architect", "plot_planner"),
    "EFFORT_DIRECTOR": ("intent_interpreter", "scene_director"),
}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
DEFAULT_EFFORTS = {
    "world_architect": "medium", "plot_planner": "medium", "intent_interpreter": "low", "scene_director": "low",
    "character_agent": "low", "narrator": "medium", "narrative_critic": "low", "chronicler": "low",
}
KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")
OFFICIAL_ANTHROPIC_URL = "https://api.anthropic.com"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    return value.strip() or default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def mask(secret: str) -> str:
    """Show just enough of a key to recognise it, never the whole thing."""
    if not secret:
        return "(empty)"
    if len(secret) <= 12:
        return secret[:3] + "..."
    return f"{secret[:7]}...{secret[-4:]} ({len(secret)} chars)"


def _template_has_key(template: Path) -> bool:
    if not template.exists():
        return False
    values = dotenv_values(template)
    return any((values.get(k) or "").strip() for k in KEY_VARS)


def load_env_file(path: Path) -> set[str]:
    """Load non-empty values from an env file into os.environ, overriding inherited ones.

    Returns the names that were set from the file.
    """
    if not path.exists():
        return set()
    loaded = set()
    for name, value in dotenv_values(path).items():
        value = (value or "").strip()
        if value:
            os.environ[name] = value
            loaded.add(name)
    return loaded


@dataclass(frozen=True)
class Settings:
    provider: str
    anthropic_model: str = "claude-opus-5-5"
    anthropic_refusal_fallback: bool = True
    anthropic_base_url: str | None = None
    openai_model: str = "gpt-4.1"
    openai_base_url: str | None = None
    efforts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EFFORTS))
    self_correction: bool = True
    save_dir: Path = Path("saves")
    story_language: str = "English"
    # Credentials are kept out of repr() so they never end up in logs or tracebacks.
    anthropic_api_key: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    env_file: Path | None = None
    key_source: str = ""
    template_has_key: bool = False

    @property
    def model_label(self) -> str:
        if self.provider == "anthropic":
            return self.anthropic_model
        if self.provider == "openai":
            return self.openai_model
        return "offline mock"

    @property
    def active_key(self) -> str:
        if self.provider == "anthropic":
            return self.anthropic_api_key or ""
        if self.provider == "openai":
            return self.openai_api_key or ""
        return ""

    def diagnostics(self) -> list[str]:
        """Plain-language hints about the configuration, shown at startup."""
        notes: list[str] = []
        if self.env_file is None and self.template_has_key:
            notes.append(
                "Your API key is in .env.example, but only .env is read. "
                "Copy it with: copy .env.example .env (or run start.bat)."
            )
        elif self.template_has_key:
            notes.append(
                ".env.example still contains a real key. It is a template that may be shared "
                "or committed, so clear it from the template."
            )
        if self.provider == "mock":
            notes.append(
                "No API key found, so the offline mock (placeholder text) is running. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
            )
        if self.provider == "anthropic" and self.anthropic_base_url:
            if self.anthropic_base_url.rstrip("/") != OFFICIAL_ANTHROPIC_URL:
                notes.append(
                    f"Requests will go to ANTHROPIC_BASE_URL={self.anthropic_base_url}. If you did not "
                    f"set this, put ANTHROPIC_BASE_URL={OFFICIAL_ANTHROPIC_URL} in .env."
                )
        if self.provider == "openai" and self.openai_base_url:
            notes.append(f"OpenAI-compatible endpoint: {self.openai_base_url}")
        return notes

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None,
                 template_file: str | os.PathLike | None = None) -> "Settings":
        path = Path(env_file) if env_file else DEFAULT_ENV_FILE
        from_file = load_env_file(path)
        template = Path(template_file) if template_file else TEMPLATE_ENV_FILE

        # Keys pasted with stray spaces ("KEY= sk-...") break authentication: clean them in place.
        for name in KEY_VARS:
            if name in os.environ:
                os.environ[name] = os.environ[name].strip()

        anthropic_key = _env("ANTHROPIC_API_KEY") or None
        openai_key = _env("OPENAI_API_KEY") or None

        provider = _env("LLM_PROVIDER").lower()
        if not provider:
            if anthropic_key or os.getenv("ANTHROPIC_AUTH_TOKEN"):
                provider = "anthropic"
            elif openai_key:
                provider = "openai"
            else:
                provider = "mock"
        if provider not in {"anthropic", "openai", "mock"}:
            raise ValueError(f"LLM_PROVIDER must be anthropic, openai or mock (got {provider!r})")

        key_var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
        if key_var is None:
            key_source = ""
        elif key_var in from_file:
            key_source = f"{key_var} from {path.name}"
        elif os.getenv(key_var):
            key_source = f"{key_var} from system environment"
        else:
            key_source = f"{key_var} not set"

        efforts = dict(DEFAULT_EFFORTS)
        settings_by_var = [(var, roles) for var, roles in LEGACY_EFFORT_VARS.items()]
        settings_by_var += [(f"EFFORT_{role.upper()}", (role,)) for role in ROLES]  # specific names win
        for var, roles in settings_by_var:
            value = _env(var).lower()
            if value:
                if value not in VALID_EFFORTS:
                    raise ValueError(f"{var} must be one of {sorted(VALID_EFFORTS)}")
                for role in roles:
                    efforts[role] = value

        save_dir = Path(_env("SAVE_DIR", "saves"))
        if not save_dir.is_absolute():
            save_dir = PROJECT_ROOT / save_dir

        return cls(
            provider=provider,
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-opus-5-5"),
            anthropic_refusal_fallback=_env_bool("ANTHROPIC_REFUSAL_FALLBACK", True),
            anthropic_base_url=_env("ANTHROPIC_BASE_URL") or None,
            openai_model=_env("OPENAI_MODEL", "gpt-4.1"),
            openai_base_url=_env("OPENAI_BASE_URL") or None,
            efforts=efforts,
            self_correction=_env_bool("SELF_CORRECTION", True),
            save_dir=save_dir,
            story_language=_env("STORY_LANGUAGE", "English"),
            anthropic_api_key=anthropic_key,
            openai_api_key=openai_key,
            env_file=path if path.exists() else None,
            key_source=key_source,
            template_has_key=_template_has_key(template),
        )
