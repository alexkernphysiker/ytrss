import re

import requests
from lxml import html as lxml_html
from trafilatura import extract

def html_text_length(html_text):
    if not html_text:
        return 0

    try:
        document = lxml_html.fragment_fromstring(
            html_text,
            create_parent="div",
        )
    except (ValueError, TypeError):
        return 0

    text = " ".join(document.itertext())
    text = re.sub(r"\s+", " ", text).strip()

    return len(text)

def extract_plain_text(article_html):
    try:
        document = lxml_html.fromstring(article_html)
    except (ValueError, TypeError):
        return ""

    paragraphs = []

    for element in document.xpath(
        "//h1 | //h2 | //h3 | //p | //li | //blockquote"
    ):
        text = " ".join(element.itertext())
        text = re.sub(r"\s+", " ", text).strip()
        if text and len(text) > 5:
            if len(paragraphs)==0 or text != paragraphs[-1]:
                paragraphs.append(text)

    return "\n".join(paragraphs)


def extract_readable_article(page_html, page_url):
    article_html = extract(
        page_html,
        url=page_url,
        output_format="html",
        include_comments=False,
        include_tables=True,
        include_links=True,
        include_images=True,
        favor_precision=True,
        deduplicate=True,
    )
    if not article_html:
        return None
    article_text = extract_plain_text(article_html)
    if len(article_text) < 200:
        return None
    return {
        "html": article_html,
        "text": article_text,
        "url": page_url,
    }


def fetch_readable_article(url, headers=None, proxies=None):
    try:
        response = requests.get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=(5, 20),
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"Cannot fetch article {url}: {error}")
        return None

    content_type = response.headers.get("Content-Type", "").lower()

    if (
        "text/html" not in content_type
        and "application/xhtml+xml" not in content_type
    ):
        print(
            f"Skipping non-HTML article {response.url}: "
            f"{content_type or 'unknown content type'}"
        )
        return None

    return extract_readable_article(
        page_html=response.content,
        page_url=response.url,
    )

def looks_like_full_article(html_text):
    if not html_text:
        return False

    try:
        document = lxml_html.fragment_fromstring(
            html_text,
            create_parent="div",
        )
    except (ValueError, TypeError):
        return False

    paragraphs = []

    for paragraph in document.xpath(".//p"):
        text = re.sub(
            r"\s+",
            " ",
            " ".join(paragraph.itertext()),
        ).strip()

        if len(text) >= 40:
            paragraphs.append(text)

    total_length = sum(len(text) for text in paragraphs)

    return len(paragraphs) >= 3 and total_length >= 768