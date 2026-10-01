import re
import requests
from lxml import html as lxml_html
from trafilatura import extract
from utils import secure_wait

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

AD_MARKERS = {
    "ad",
    "ads",
    "advert",
    "advertisement",
    "advertising",
    "sponsored",
    "sponsor",
    "adsbygoogle",
    "ad-container",
    "ad-wrapper",
    "ad-banner",
    "ad-slot",
    "ad-unit",
}


def _is_ad_element(element):
    # Явні атрибути рекламних систем.
    for attribute in (
        "data-ad-client",
        "data-ad-slot",
        "data-ad-unit",
        "data-ad-format",
    ):
        if element.get(attribute) is not None:
            return True

    # class / id
    for attribute in ("class", "id"):
        value = (element.get(attribute) or "").lower()

        if not value:
            continue

        tokens = {
            token
            for token in re.split(r"\s+", value)
            if token
        }

        for token in tokens:
            if token in AD_MARKERS:
                return True

            # article-ad
            # sidebar_ads
            # google-ad-container
            if re.search(
                r"(^|[-_])(ad|ads|advert|advertisement|advertising|sponsored)([-_]|$)",
                token,
            ):
                return True

    # Доступність часто явно позначає рекламний блок.
    aria_label = (element.get("aria-label") or "").strip().lower()

    if aria_label in {
        "advertisement",
        "advertising",
        "sponsored",
        "sponsored content",
    }:
        return True

    return False


def remove_ad_blocks(page_html):
    if not page_html:
        return page_html

    try:
        document = lxml_html.fromstring(page_html)
    except (ValueError, TypeError):
        return page_html

    # Йдемо знизу вгору, щоб безпечно видаляти вкладені елементи.
    for element in reversed(document.xpath("//*")):
        if _is_ad_element(element):
            parent = element.getparent()

            # Кореневий елемент не чіпаємо.
            if parent is not None:
                element.drop_tree()

    return lxml_html.tostring(
        document,
        encoding="unicode",
        method="html",
    )


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
    from config import get_config

    try:
        secure_wait()
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

    page_html = remove_ad_blocks(response.content)

    return extract_readable_article(
        page_html=page_html,
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