"""A small robots.txt check for the public-source fetcher.

The matcher follows the common subset CMS publishes: one ``User-agent: *``
group, ``Allow`` and ``Disallow`` paths, ``*`` wildcards, and ``$`` anchors.
The longest matching rule wins. An allow wins a tie.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    if not anchored:
        regex = regex + ".*"
    return re.compile(regex)


def _star_rules(robots_text: str) -> list[tuple[str, str, re.Pattern[str]]]:
    """Return ``(kind, original, compiled)`` rules for the ``*`` user-agent."""
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []

    def flush() -> None:
        nonlocal agents, rules
        if agents or rules:
            groups.append((agents, rules))
        agents = []
        rules = []

    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            agents.append(value.lower())
        elif key in {"allow", "disallow"} and value:
            rules.append((key, value))
    flush()

    selected: list[tuple[str, str]] = []
    for group_agents, group_rules in groups:
        if "*" in group_agents:
            selected.extend(group_rules)
    return [(kind, pattern, _compile(pattern)) for kind, pattern in selected]


def request_path(url: str) -> str:
    parts = urlsplit(url)
    if parts.query:
        return f"{parts.path}?{parts.query}"
    return parts.path or "/"


def allows(url: str, robots_text: str) -> bool:
    """True when ``url`` is allowed for ``User-agent: *``."""
    path = request_path(url)
    matches: list[tuple[int, int, str]] = []
    for kind, pattern, compiled in _star_rules(robots_text):
        if compiled.fullmatch(path):
            # Longer original patterns win. Allow wins an equal-length tie.
            matches.append((len(pattern), 1 if kind == "allow" else 0, kind))
    if not matches:
        return True
    return max(matches)[2] == "allow"
