"""Configuration loading for the council.

Config is TOML (see ``council.toml``). The loader is tolerant: a missing config
file falls back to a sensible built-in default so the tool runs out of the box.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG: dict = {
    "council": {
        "rounds": 2,
        "chair": "claude",
        "timeout": 600,
        "parallel": True,
        "verify": True,
        "ci": {"fail_on": ["critical", "major"], "ignore_unverified": True},
    },
    "agent": [
        {
            "name": "claude",
            "vendor": "anthropic",
            "command": "claude",
            "extra_args": [
                "--output-format", "text",
                "--disallowed-tools", "Edit,Write,NotebookEdit,Bash",
                # Avoid `-p` blocking on a permission prompt in non-interactive mode.
                "--dangerously-skip-permissions",
            ],
        },
        {"name": "codex", "vendor": "openai", "command": "codex", "extra_args": []},
        {
            "name": "agy",
            "vendor": "google",
            "command": "agy",
            "extra_args": ["--dangerously-skip-permissions"],
        },
    ],
}


@dataclass
class AgentSpec:
    name: str
    vendor: str
    command: str
    model: str | None = None
    timeout: int = 600
    enabled: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class CiConfig:
    fail_on: list[str] = field(default_factory=lambda: ["critical", "major"])
    ignore_unverified: bool = True


@dataclass
class CouncilConfig:
    rounds: int = 2
    chair: str = "claude"
    timeout: int = 600
    parallel: bool = True
    verify: bool = True
    agents: list[AgentSpec] = field(default_factory=list)
    ci: CiConfig = field(default_factory=CiConfig)

    @property
    def enabled_agents(self) -> list[AgentSpec]:
        return [a for a in self.agents if a.enabled]


def _ci_from_dict(data: dict) -> CiConfig:
    fail_on = data.get("fail_on", ["critical", "major"])
    if not isinstance(fail_on, list):
        fail_on = [fail_on]
    fail_on = [str(s).strip().lower() for s in fail_on if str(s).strip()]
    return CiConfig(
        fail_on=fail_on,
        ignore_unverified=bool(data.get("ignore_unverified", True)),
    )


def _from_dict(data: dict) -> CouncilConfig:
    council = data.get("council", {})
    default_timeout = int(council.get("timeout", 600))
    agents: list[AgentSpec] = []
    for raw in data.get("agent", []):
        agents.append(
            AgentSpec(
                name=raw["name"],
                vendor=raw.get("vendor", "unknown"),
                command=raw["command"],
                model=raw.get("model"),
                timeout=int(raw.get("timeout", default_timeout)),
                enabled=bool(raw.get("enabled", True)),
                extra_args=list(raw.get("extra_args", [])),
            )
        )
    return CouncilConfig(
        rounds=int(council.get("rounds", 2)),
        chair=council.get("chair", agents[0].name if agents else "claude"),
        timeout=default_timeout,
        parallel=bool(council.get("parallel", True)),
        verify=bool(council.get("verify", True)),
        agents=agents,
        ci=_ci_from_dict(council.get("ci", {})),
    )


def load_config(path: str | Path | None = None) -> CouncilConfig:
    """Load council config from *path*, or fall back to the built-in default.

    If *path* is None, look for ``council.toml`` in the current directory.
    """
    if path is None:
        candidate = Path("council.toml")
        if not candidate.exists():
            return _from_dict(DEFAULT_CONFIG)
        path = candidate
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _from_dict(data)
