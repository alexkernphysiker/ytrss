import requests
import feedparser

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from config import *


def discover_rss_feeds(site_url: str) -> list[tuple[str, str, str, str]]:
    if not urlparse(site_url).scheme:
        site_url = "https://" + site_url

    session = requests.Session()
    session.headers.update(get_config()["headers"])

    try:
        response = session.get(site_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return []

    page_url = response.url

    parsed = feedparser.parse(response.content)
    if parsed.version:
        return [(
            parsed.feed.get("title", ""),
            page_url,
            parsed.feed.get("subtitle", ""),
            parsed.feed.get("link", page_url),
        )]

    soup = BeautifulSoup(response.content, "html.parser")

    base = soup.find("base", href=True)
    base_url = urljoin(page_url, base["href"]) if base else page_url

    candidates = []

    if soup.head:
        for link in soup.head.find_all("link", href=True):
            rel = link.get("rel", [])
            mime = link.get("type", "").split(";")[0].strip().lower()

            if "alternate" in rel and mime in (
                "application/rss+xml",
                "application/atom+xml",
                "application/rdf+xml",
            ):
                candidates.append(
                    urljoin(base_url, link["href"])
                )

    # Перевіряємо типові адреси.
    origin = (
        f"{urlparse(page_url).scheme}://"
        f"{urlparse(page_url).netloc}"
    )

    for path in (
        "/feed",
        "/rss",
        "/feed.xml",
        "/rss.xml",
        "/atom.xml",
    ):
        candidates.append(urljoin(origin, path))

    results = []
    seen = set()

    for candidate in dict.fromkeys(candidates):
        try:
            response = session.get(candidate, timeout=10)
            response.raise_for_status()

            feed_url = response.url

            if feed_url in seen:
                continue

            parsed = feedparser.parse(response.content)

            # Відкидаємо відповіді, які не є RSS/Atom.
            if not parsed.version:
                continue

            seen.add(feed_url)

            results.append((
                parsed.feed.get("title", ""),
                feed_url,
                parsed.feed.get("subtitle", ""),
                parsed.feed.get("link", page_url),
            ))

        except requests.RequestException:
            continue

    return results