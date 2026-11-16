"""Verify every service domain and rewrite domains.json (run by .github/workflows/update.yml).

Exit code 0 always; services that could not be resolved are listed in $GITHUB_STEP_SUMMARY
and in broken_domains.txt so the workflow can open an issue.

domain_resolver.py is a copy of OmniVision/utils/domain_resolver.py: keep the two in sync.
"""

import json
import os

from curl_cffi import requests

from domain_resolver import DomainResolver, FetchResult

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "domains.json")

# Other public lists checked when a domain stops working (this repository's own list is the current state).
SOURCES = (
    "https://raw.githubusercontent.com/AstraeLabs/Domains/refs/heads/main/domains_s.json",
    "https://cdn.jsdelivr.net/gh/AstraeLabs/Domains@main/domains_s.json",
)


def fetch(url: str, timeout: float) -> FetchResult:
    resp = requests.get(url, timeout=timeout, allow_redirects=True, impersonate="chrome")
    return FetchResult(status=resp.status_code, url=str(resp.url), text=resp.text or "")


def main() -> None:
    with open(PATH, encoding="utf-8") as f:
        domains = json.load(f)

    def save(site: str, url: str) -> None:
        domains.setdefault(site, {})["full_url"] = url

    resolver = DomainResolver(get_domains=lambda: domains, save_domain=save, fetch=fetch, sources=SOURCES)
    status = resolver.resolve_all()

    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(domains, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, PATH)

    broken = [site for site, st in status.items() if not st["ok"]]
    lines = ["| Servizio | Dominio | Origine | Esito |", "|---|---|---|---|"]
    for site, st in status.items():
        lines.append(f"| {site} | {st['url'] or '-'} | {st['source']} | {'ok' if st['ok'] else st['error']} |")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report + "\n")
    with open(os.path.join(ROOT, "broken_domains.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(broken))


if __name__ == "__main__":
    main()
