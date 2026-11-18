"""Download public source files into the manifest's cache directory.

Downloads are HTTPS GETs of the manifest URL. There is no login and no
terms-acceptance step. robots.txt is checked first. A missing robots.txt
allows the download. Any other robots failure refuses it.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from benefitiq.ingestion.loaders import IngestionError
from benefitiq.ingestion.robots import allows
from benefitiq.ingestion.service import load_manifest

USER_AGENT = "BenefitIQ-ingest/0.1 (public-corpus research; respects robots.txt)"
MAX_BYTES = 200 * 1024 * 1024
ALLOWED_HOSTS = frozenset(
    {"cms.gov", "www.cms.gov", "download.cms.gov", "data.healthcare.gov"}
)


def fetch_manifest(manifest_path: Path) -> dict[str, str]:
    """Download each manifest URL into its relative cache path.

    An existing non-empty file is left in place. Returns ``source_id`` to
    ``cached`` or ``downloaded``.
    """
    base_dir, manifest = load_manifest(manifest_path)
    robots_bodies: dict[str, str | None] = {}
    outcomes: dict[str, str] = {}
    for entry in manifest.documents:
        dest = (base_dir / entry.path).resolve()
        try:
            dest.relative_to(base_dir.resolve())
        except ValueError as exc:
            raise IngestionError(f"cache path escapes the manifest directory: {entry.path}") from exc
        if dest.is_file() and dest.stat().st_size > 0:
            outcomes[entry.source_id] = "cached"
            continue
        download(entry.url, dest, robots_bodies)
        outcomes[entry.source_id] = "downloaded"
    return outcomes


def download(url: str, dest: Path, robots_bodies: dict[str, str | None] | None = None) -> None:
    host = _require_public_host(url)
    bodies = robots_bodies if robots_bodies is not None else {}
    if not robots_allow(url, host, bodies):
        raise IngestionError(f"robots.txt disallows {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=180, context=ssl.create_default_context()) as response:
            final_host = urlsplit(response.geturl()).hostname
            if final_host not in ALLOWED_HOSTS:
                raise IngestionError(f"refusing redirect from {url} to {response.geturl()}")
            written = 0
            with partial.open("wb") as handle:
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_BYTES:
                        raise IngestionError(f"{url} exceeds the {MAX_BYTES} byte download cap")
                    handle.write(block)
        payload = partial.read_bytes()[:64].lstrip().lower()
        if written < 16 or payload.startswith((b"<!doctype", b"<html", b"not found")):
            raise IngestionError(f"{url} did not return a data file")
        partial.replace(dest)
    except IngestionError:
        partial.unlink(missing_ok=True)
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise IngestionError(f"download failed for {url}: {exc}") from exc


def robots_allow(url: str, host: str, robots_bodies: dict[str, str | None]) -> bool:
    if host not in robots_bodies:
        robots_bodies[host] = fetch_robots(host)
    body = robots_bodies[host]
    if body is None:
        return True
    return allows(url, body)


def fetch_robots(host: str) -> str | None:
    """Return the robots.txt body, or None when the host has no such file."""
    robots_url = f"https://{host}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise IngestionError(f"could not read {robots_url}: {exc}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise IngestionError(f"could not read {robots_url}: {exc}") from exc


def _require_public_host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise IngestionError(f"public sources must be https URLs: {url}")
    if parts.hostname not in ALLOWED_HOSTS:
        raise IngestionError(f"refusing URL outside the public CMS hosts: {url}")
    return parts.hostname
