# 29.10.26

"""
Self-healing domain resolution for services whose address changes often
(StreamingCommunity, AnimeUnity, AnimeWorld, ...).

A candidate domain is accepted only when the page it serves passes the service
fingerprint: a parked domain answering HTTP 200 is not good enough. Candidates are
tried in this order and the first valid one wins:

    1. manual override   (config ``DOMAINS.overrides``)
    2. current domain    (domains.json / cache)
    3. redirect target   (HTTP 3xx, <meta refresh>, ``window.location``)
    4. remote lists      (``DOMAINS.sources`` + built-in mirrors)
    5. TLD probing       (same name on common TLDs, in parallel)

The network is reached only through ``self.fetch`` so the whole chain is testable offline.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


DEFAULT_SOURCES = (
    # Own public list (repository Monti880/OmniVision-domains), refreshed every 3 hours by its workflow
    "https://raw.githubusercontent.com/Monti880/OmniVision-domains/main/domains.json",
    "https://cdn.jsdelivr.net/gh/Monti880/OmniVision-domains@main/domains.json",
    # Upstream list
    "https://raw.githubusercontent.com/AstraeLabs/Domains/refs/heads/main/domains_s.json",
    "https://cdn.jsdelivr.net/gh/AstraeLabs/Domains@main/domains_s.json",
)

PROBE_TLDS = (
    "day", "xyz", "so", "co", "ac", "it", "io", "to", "me", "cc", "tv", "life", "live", "online", "site",
    "store", "fit", "fun", "quest", "stream", "click", "lol", "blog", "art", "bio", "pro", "top", "info",
    "uno", "black", "boo", "esq", "foo", "prof", "computer", "watch", "media", "you", "zip", "mov",
)

PARKED_MARKERS = (
    "domain is for sale", "buy this domain", "this domain may be for sale", "parkingcrew", "sedoparking",
    "bodis.com", "domain parked", "is parked free", "dan.com", "afternic", "hugedomains",
)

# Per-service checks on top of the generic "site name appears in the page" test.
FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "streamingcommunity": (r'id="app"', r"data-page="),
    "animeunity": (r"animeunity", r"csrf-token|livesearch|archivio"),
    "animeworld": (r"animeworld",),
    # The real site is WordPress; copycat landers titled "cb01" (e.g. madara.fans) are not.
    "cb01new": (r"cb01", r"wp-content", r"/category/"),
}

MIN_HEAL_INTERVAL_SECONDS = 60
FETCH_TIMEOUT_SECONDS = 6
PROBE_TIMEOUT_SECONDS = 4


@dataclass
class FetchResult:
    status: int
    url: str
    text: str


@dataclass
class DomainStatus:
    site: str
    url: str | None = None
    ok: bool = False
    source: str = "none"
    checked_at: float = 0.0
    latency_ms: int | None = None
    error: str | None = None
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


FetchFunc = Callable[[str, float], FetchResult]


def _default_fetch(url: str, timeout: float) -> FetchResult:
    from OmniVision.utils.http_client import create_client

    with create_client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
    return FetchResult(status=resp.status_code, url=str(resp.url), text=resp.text or "")


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def host_stem(url: str) -> str:
    """``https://www.streamingcommunityz.day/`` -> ``streamingcommunityz``."""
    host = urlparse(normalize_url(url)).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rsplit(".", 1)[0] if "." in host else host


_META_REFRESH = re.compile(r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*url=([^"'>\s]+)""", re.I)
_JS_LOCATION = re.compile(r"""(?:window\.|document\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""", re.I)


def extract_soft_redirect(base_url: str, html: str) -> str | None:
    for pattern in (_META_REFRESH, _JS_LOCATION):
        match = pattern.search(html or "")
        if match:
            target = urljoin(base_url + "/", match.group(1).strip())
            if urlparse(target).netloc and urlparse(target).netloc != urlparse(base_url).netloc:
                return normalize_url(target)
    return None


def page_matches(site: str, html: str) -> bool:
    """True when ``html`` looks like the real service and not a parked/blocked page."""
    low = (html or "").lower()
    if len(low) < 200 or any(marker in low for marker in PARKED_MARKERS):
        return False
    patterns = FINGERPRINTS.get(site)
    if patterns:
        return all(re.search(p, html, re.I) for p in patterns)
    return site.lower() in low


class DomainResolver:
    def __init__(
        self,
        get_domains: Callable[[], dict],
        save_domain: Callable[[str, str], None],
        *,
        fetch: FetchFunc | None = None,
        sources: Iterable[str] | None = None,
        overrides: Callable[[], dict] | None = None,
        tlds: Iterable[str] = PROBE_TLDS,
        clock: Callable[[], float] = time.time,
        is_enabled: Callable[[str], bool] | None = None,
    ):
        self._get_domains = get_domains
        self._save_domain = save_domain
        self.fetch: FetchFunc = fetch or _default_fetch
        self.sources = tuple(sources) if sources is not None else DEFAULT_SOURCES
        self._overrides = overrides or (lambda: {})
        self.tlds = tuple(tlds)
        self._clock = clock
        self._is_enabled = is_enabled or (lambda site: True)
        self._status: dict[str, DomainStatus] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._last_heal: dict[str, float] = {}

    # ---- public API ----------------------------------------------------------------

    def status(self) -> dict[str, dict]:
        return {site: st.to_dict() for site, st in sorted(self._status.items())}

    def sites(self) -> list[str]:
        return sorted(self._get_domains().keys())

    def check(self, site: str, url: str) -> tuple[bool, str | None, int | None, str | None]:
        """Validate ``url`` for ``site``. Returns (ok, final_url, latency_ms, error)."""
        url = normalize_url(url)
        if not url:
            return False, None, None, "empty url"
        start = self._clock()
        try:
            res = self.fetch(url, FETCH_TIMEOUT_SECONDS)
        except Exception as e:  # network errors are expected here: the domain may be dead
            return False, None, None, f"{type(e).__name__}: {e}"
        latency = int((self._clock() - start) * 1000)
        final = normalize_url(res.url or url)
        if res.status >= 400:
            return False, final, latency, f"HTTP {res.status}"
        if page_matches(site, res.text):
            return True, final, latency, None
        soft = extract_soft_redirect(final, res.text)
        if soft:
            return False, soft, latency, "soft redirect"
        return False, final, latency, "fingerprint mismatch"

    def resolve(self, site: str, *, force: bool = False) -> DomainStatus:
        """Find a working domain for ``site``; persists it when it changed."""
        with self._lock_for(site):
            now = self._clock()
            cached = self._status.get(site)
            if not force and cached and cached.ok and now - cached.checked_at < MIN_HEAL_INTERVAL_SECONDS:
                return cached

            st = DomainStatus(site=site, checked_at=now)
            current = normalize_url((self._get_domains().get(site) or {}).get("full_url", ""))

            for source, candidate in self._candidates(site, current):
                ok, final, latency, error = self.check(site, candidate)
                if not ok and error == "soft redirect" and final:
                    st.history.append(f"{source}:{candidate}:soft redirect")
                    source, candidate = "redirect", final
                    ok, final, latency, error = self.check(site, candidate)
                st.history.append(f"{source}:{candidate}:{'ok' if ok else error}")
                if ok and final:
                    if final != candidate:
                        source = "redirect"
                    st.url, st.ok, st.source, st.latency_ms, st.error = final, True, source, latency, None
                    break
                st.error = error
            else:
                st.url = current or None

            if st.ok and st.url != current:
                logger.info("Domain for %s changed: %s -> %s (%s)", site, current or "-", st.url, st.source)
                self._save_domain(site, st.url + "/")

            self._status[site] = st
            self._last_heal[site] = now
            return st

    def heal(self, site: str) -> bool:
        """Called after a failure: re-resolve unless done very recently. True when the domain changed."""
        before = normalize_url((self._get_domains().get(site) or {}).get("full_url", ""))
        last = self._last_heal.get(site, 0.0)
        if self._clock() - last < MIN_HEAL_INTERVAL_SECONDS and self._status.get(site, DomainStatus(site)).ok:
            return False
        st = self.resolve(site, force=True)
        return bool(st.ok and st.url and st.url != before)

    def resolve_all(self, max_workers: int = 6) -> dict[str, dict]:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="domain-resolve") as pool:
            list(pool.map(lambda s: self.resolve(s, force=True), [s for s in self.sites() if self._is_enabled(s)]))
        return self.status()

    # ---- internals -----------------------------------------------------------------

    def _lock_for(self, site: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(site, threading.Lock())

    def _candidates(self, site: str, current: str):
        seen: set[str] = set()

        def emit(source: str, url: str):
            url = normalize_url(url)
            if url and url not in seen:
                seen.add(url)
                yield source, url

        override = (self._overrides() or {}).get(site)
        if override:
            yield from emit("manual", override)
        if current:
            yield from emit("cache", current)
        for remote in self._remote_candidates(site):
            yield from emit("remote", remote)
        found = self._probe_tlds(site, current, seen)
        if found:
            yield from emit("probe", found)

    def _remote_candidates(self, site: str) -> list[str]:
        out: list[str] = []
        for src in self.sources:
            try:
                res = self.fetch(src, FETCH_TIMEOUT_SECONDS)
                if res.status >= 400:
                    continue
                data = json.loads(res.text)
                url = (data.get(site) or {}).get("full_url")
                if url and normalize_url(url) not in out:
                    out.append(normalize_url(url))
            except Exception as e:
                logger.debug("Domain source %s unusable: %s", src, e)
        return out

    def _probe_tlds(self, site: str, current: str, skip: set[str]) -> str | None:
        stems = []
        for stem in (host_stem(current) if current else "", site):
            if stem and stem not in stems:
                stems.append(stem)
        candidates = [normalize_url(f"https://{stem}.{tld}") for stem in stems for tld in self.tlds]
        candidates = [c for c in candidates if c not in skip]
        if not candidates:
            return None

        def probe(url: str):
            try:
                res = self.fetch(url, PROBE_TIMEOUT_SECONDS)
            except Exception:
                return None
            if res.status < 400 and page_matches(site, res.text):
                return normalize_url(res.url or url)
            return None

        with ThreadPoolExecutor(max_workers=12, thread_name_prefix="domain-probe") as pool:
            futures = {pool.submit(probe, c): c for c in candidates}
            for fut in as_completed(futures):
                found = fut.result()
                if found:
                    for other in futures:
                        other.cancel()
                    return found
        return None


# ---- process-wide instance bound to config_manager ------------------------------------

_resolver: DomainResolver | None = None
_resolver_guard = threading.Lock()


def get_resolver() -> DomainResolver:
    global _resolver
    with _resolver_guard:
        if _resolver is None:
            from OmniVision.utils import config_manager

            def overrides() -> dict:
                try:
                    return config_manager.config.get("DOMAINS", "overrides", dict, default={}) or {}
                except Exception:
                    return {}

            def sources() -> tuple[str, ...]:
                try:
                    extra = config_manager.config.get("DOMAINS", "sources", list, default=[]) or []
                except Exception:
                    extra = []
                return tuple(extra) + DEFAULT_SOURCES

            def is_enabled(site: str) -> bool:
                try:
                    off = config_manager.config.get("DEFAULT", "disabled_services", list, default=[]) or []
                except Exception:
                    return True
                return site.lower() not in {str(s).lower() for s in off}

            _resolver = DomainResolver(
                is_enabled=is_enabled,
                get_domains=lambda: config_manager._domains_data,
                save_domain=config_manager.set_domain,
                sources=sources(),
                overrides=overrides,
            )
        return _resolver
