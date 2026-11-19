"""Download public Oklahoma policy files into the gitignored cache.

Only catalog rows marked fetchable are requested. The fetch is a GET. It does
not log in, does not accept a disclaimer, and does not send a cookie. robots.txt
is checked first. A missing robots.txt allows the download. One blocked URL
does not stop the others.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

from benefitiq.ingestion.robots import allows
from benefitiq.payers.catalog import ROOT, blocked_fetch_reason, fetchable_sources

USER_AGENT = "BenefitIQ-ingest/0.1 (public-corpus research; respects robots.txt)"
MAX_BYTES = 40 * 1024 * 1024
CACHE = ROOT / "corpus" / "payers" / "cache"
INDEXED_MANIFEST = CACHE / "indexed-manifest.yaml"


@dataclass
class FetchView:
    """One download attempt. `payload` is set only when the same address returned a file."""

    kind: str
    detail: str
    payload: bytes | None = None
    final_url: str = ""


def fetch_document(url: str, robots_bodies: dict[str, str | None] | None = None) -> FetchView:
    """GET one public URL. A block, a missing page, or a move does not raise."""
    reason = blocked_fetch_reason(url)
    if reason:
        return FetchView("blocked", reason)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return FetchView("blocked", "The address is not an https URL.")
    host = parts.hostname
    bodies = robots_bodies if robots_bodies is not None else {}
    try:
        if host not in bodies:
            bodies[host] = _fetch_robots(host)
    except OSError:
        return FetchView("error", "The site's robots.txt could not be read.")
    body = bodies[host]
    if body is not None and not allows(url, body):
        return FetchView("blocked", "robots.txt disallows this address.")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            final = urlsplit(response.geturl())
            if final.hostname != host:
                return FetchView("moved", "The file moved to another site.", final_url=response.geturl())
            if final.path != parts.path:
                return FetchView("moved", "The file moved to a different address.", final_url=response.geturl())
            written = bytearray()
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                written.extend(block)
                if len(written) > MAX_BYTES:
                    return FetchView("error", "The file is larger than the download cap.")
            if len(written) < 32:
                return FetchView("error", "The site did not return a document.")
            return FetchView("ok", "downloaded", payload=bytes(written), final_url=response.geturl())
    except HTTPError as exc:
        if exc.code in {404, 410}:
            return FetchView("not_found", "The page was not found.")
        return FetchView("error", "The download failed.")
    except (URLError, TimeoutError, OSError):
        return FetchView("error", "The download failed.")


def fetch_payer_sources() -> dict[str, str]:
    """Download fetchable sources. Returns source_id to cached, downloaded, or a skip reason."""
    CACHE.mkdir(parents=True, exist_ok=True)
    robots_bodies: dict[str, str | None] = {}
    outcomes: dict[str, str] = {}
    documents = []
    for source in fetchable_sources():
        reason = blocked_fetch_reason(source.url)
        if reason:
            outcomes[source.source_id] = f"skipped: {reason}"
            continue
        dest = CACHE / f"{source.source_id}.bin"
        if dest.is_file() and dest.stat().st_size > 0:
            outcomes[source.source_id] = "cached"
            documents.append(_manifest_entry(source, dest.name))
            continue
        try:
            download(source.url, dest, robots_bodies)
        except OSError as exc:
            dest.unlink(missing_ok=True)
            outcomes[source.source_id] = f"skipped: {exc}"
            continue
        outcomes[source.source_id] = "downloaded"
        documents.append(_manifest_entry(source, dest.name))
    if documents:
        INDEXED_MANIFEST.write_text(
            yaml.safe_dump({"documents": documents}, sort_keys=False),
            encoding="utf-8",
        )
    return outcomes


def download(url: str, dest: Path, robots_bodies: dict[str, str | None] | None = None) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise OSError(f"refusing non-https URL {url}")
    host = parts.hostname
    bodies = robots_bodies if robots_bodies is not None else {}
    if host not in bodies:
        bodies[host] = _fetch_robots(host)
    body = bodies[host]
    if body is not None and not allows(url, body):
        raise OSError(f"robots.txt disallows {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    partial = dest.with_name(dest.name + ".partial")
    try:
        with urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            final = urlsplit(response.geturl())
            if final.hostname != host:
                raise OSError(f"refusing redirect from {url} to {response.geturl()}")
            written = 0
            with partial.open("wb") as handle:
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_BYTES:
                        raise OSError(f"{url} exceeds the download cap")
                    handle.write(block)
        if written < 32:
            raise OSError(f"{url} did not return a document")
        partial.replace(dest)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
    except (HTTPError, URLError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise OSError(f"download failed for {url}") from exc


def _fetch_robots(host: str) -> str | None:
    request = Request(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise OSError(f"could not read robots.txt for {host}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OSError(f"could not read robots.txt for {host}") from exc


def _manifest_entry(source, filename: str) -> dict:
    return {
        "path": filename,
        "source_id": f"{source.source_id}-text",
        "family_id": f"{source.source_id}-text",
        "title": source.title,
        "publisher": source.publisher,
        "url": source.url,
        "effective_from": source.date.value.isoformat(),
        "doc_type": "policy_text",
        "loader": "policy_text",
        "payer": source.payer_id,
        "state": source.state,
        "program": source.program,
        "plan_type": source.plan_type,
    }
