"""Compare a hub's config.toml with the template it was made from (#126).

Every hub -- dev, test, production -- starts from config.toml.example, and only
a short, documented list of keys is meant to differ between them: who the hub
is, where its files live, its secrets, its sign-in and its alerting. Anything
else that differs is drift, and drift is how one environment ends up testing
something the others never run. ``diff_against_template`` names it.

Keys the template leaves commented out (``# public_url = ...``) are documented
but unset: setting one on a host is a difference like any other, and a key the
template does not mention at all is reported separately, since it is usually
stale or misspelt (the hub ignores keys it does not know).

Values are printed so the difference can be read, except for keys whose name
says they hold a secret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import toml

# Keys that are meant to differ per host. A trailing ".*" covers a section.
PER_HOST: tuple[str, ...] = (
    # where the hub listens and is reached; behind Caddy it is 127.0.0.1:8000
    "server.host",
    "server.port",
    "server.public_url",
    # where its files live
    "server.data_dir",
    "database.path",
    "games.root",
    "games.drive_c",
    "games.images",
    "games.image_url",
    "games.image_credentials",
    # who the hub is
    "hub.*",
    # secrets and trust
    "security.jwt_secret",
    "security.trusted_proxies",
    # sign-in and alerting are per deployment
    "identity.*",
    "alerting.*",
)

_SECRET = re.compile(r"(secret|password|api_key|token)$", re.I)
_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_COMMENTED_KEY = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

UNSET = object()


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _commented_keys(text: str) -> set[str]:
    """Keys the template documents in a comment, as section.key."""
    keys: set[str] = set()
    section = ""
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = _COMMENTED_KEY.match(line)
        if m and section:
            keys.add(f"{section}.{m.group(1)}")
    return keys


def is_per_host(key: str) -> bool:
    for pattern in PER_HOST:
        if pattern.endswith(".*"):
            if key.startswith(pattern[:-1]):
                return True
        elif key == pattern:
            return True
    return False


def is_secret(key: str) -> bool:
    return bool(_SECRET.search(key.rsplit(".", 1)[-1]))


@dataclass
class TemplateDiff:
    differs: list[tuple[str, Any, Any]] = field(default_factory=list)
    """(key, template value or UNSET, config value): drift."""
    unset: list[tuple[str, Any]] = field(default_factory=list)
    """(key, template value): the config leaves it to the code's default."""
    unknown: list[tuple[str, Any]] = field(default_factory=list)
    """(key, config value): the template does not mention it."""

    @property
    def clean(self) -> bool:
        return not (self.differs or self.unset or self.unknown)


def diff_against_template(config_text: str, template_text: str) -> TemplateDiff:
    config = _flatten(toml.loads(config_text))
    template = _flatten(toml.loads(template_text))
    documented = _commented_keys(template_text) - template.keys()

    result = TemplateDiff()
    for key in sorted(template.keys() | config.keys() | documented):
        if is_per_host(key):
            continue
        in_config = key in config
        if key in template:
            if not in_config:
                result.unset.append((key, template[key]))
            elif config[key] != template[key]:
                result.differs.append((key, template[key], config[key]))
        elif key in documented:
            if in_config:
                result.differs.append((key, UNSET, config[key]))
        else:
            result.unknown.append((key, config[key]))
    return result


def _show(key: str, value: Any) -> str:
    if value is UNSET:
        return "(commented out)"
    if is_secret(key):
        return "(hidden)"
    return toml.dumps({"v": value}).split("=", 1)[1].strip()


def format_diff(diff: TemplateDiff, config_path: str, template_path: str) -> str:
    lines = [f"{config_path} against {template_path}"]
    lines.append(f"  (not compared, set per host: {', '.join(PER_HOST)})")
    if diff.clean:
        lines.append("  no differences")
        return "\n".join(lines)
    if diff.differs:
        lines.append("")
        lines.append("Differs from the template:")
        for key, tmpl, conf in diff.differs:
            lines.append(f"  {key}: {_show(key, conf)}  (template: {_show(key, tmpl)})")
    if diff.unset:
        lines.append("")
        lines.append("Not set, so the code's default applies:")
        for key, tmpl in diff.unset:
            lines.append(f"  {key}  (template: {_show(key, tmpl)})")
    if diff.unknown:
        lines.append("")
        lines.append("Not in the template (stale or misspelt? the hub ignores keys it does not know):")
        for key, conf in diff.unknown:
            lines.append(f"  {key}: {_show(key, conf)}")
    return "\n".join(lines)


def run(config_path: str, template_path: str) -> int:
    diff = diff_against_template(Path(config_path).read_text(), Path(template_path).read_text())
    print(format_diff(diff, config_path, template_path))
    return 0 if diff.clean else 1
