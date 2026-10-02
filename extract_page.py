import re
import json
import requests
from lxml import html as lxml_html
from lxml.etree import ParserError
from PIL import Image
from io import BytesIO
from urllib.parse import urljoin, urlsplit
from trafilatura import extract
from utils import secure_wait
from config import get_config

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
        remove_ad_blocks(page_html),
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
        "full_content": remove_ad_blocks(page_html)
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



def _get_dimension(img, name):
    value = img.get(name)
    if value:
        match = re.match(r"^\s*(\d+(?:\.\d+)?)", value)
        if match:
            return float(match.group(1))
    style = img.get("style") or ""
    match = re.search(
        rf"(?:^|;)\s*{name}\s*:\s*(\d+(?:\.\d+)?)px",
        style,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1))
    return None

def _parse_dimension(value, reference_size=None):
    if not value:
        return None

    value = str(value).strip().lower()

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*px", value)
    if match:
        return float(match.group(1))

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", value)
    if match and reference_size is not None:
        return reference_size * float(match.group(1)) / 100

    match = re.fullmatch(r"(\d+(?:\.\d+)?)", value)
    if match:
        return float(match.group(1))

    return None

def find_largest_image_in_html(html_text, base_url, check_actual_size=True):
    if not html_text or not html_text.strip():
        return None

    try:
        root = lxml_html.fragment_fromstring(
            html_text,
            create_parent="div"
        )
    except (ParserError, ValueError):
        return None

    image_urls = []

    def add_url(src):
        src = (src or "").strip()
        if not src:
            return

        try:
            image_url = urljoin(base_url, src)
            parsed = urlsplit(image_url)
        except ValueError:
            return

        if parsed.scheme in ("http", "https") and parsed.netloc:
            image_urls.append(image_url)

    for img in root.iter("img"):
        for attribute in ("data-src", "src"):
            add_url(img.get(attribute))

        for attribute in ("data-srcset", "srcset"):
            srcset = (img.get(attribute) or "").strip()

            if not srcset:
                continue

            for candidate in srcset.split(","):
                src = candidate.strip().split()[0]
                add_url(src)

    image_urls = list(dict.fromkeys(image_urls))

    best_url = None
    best_score = 0

    for img in root.iter("img"):
            image_url = None

            for attribute in ("data-src", "src"):
                src = (img.get(attribute) or "").strip()
                if not src:
                    continue

                try:
                    url = urljoin(base_url, src)
                    parsed = urlsplit(url)
                except ValueError:
                    continue

                if parsed.scheme in ("http", "https") and parsed.netloc:
                    image_url = url
                    break

            if not image_url:
                continue

            width = _get_dimension(img, "width")
            height = _get_dimension(img, "height")
            score = 0
            if width is not None and height is not None:
                w = _parse_dimension(width, 1000)
                h = _parse_dimension(height, 1000)
                if w is not None and h is not None:
                    score = w * h 

            if score == 0 and check_actual_size:
                try:
                    response = requests.get(image_url, timeout=10, headers=get_config()["headers"], proxies=get_config().get("proxies-rss"))
                    if response.status_code == 200:
                        image_data = BytesIO(response.content)
                        with Image.open(image_data) as img_obj:
                            width, height = img_obj.size
                            score = width * height
                except Exception:
                    continue

            if score > best_score:
                best_score = score
                best_url = image_url

    return best_url if best_score > 500 else None

def _find_jsonld_article_image(data):
    if isinstance(data, list):
        for item in data:
            result = _find_jsonld_article_image(item)
            if result:
                return result

        return None

    if not isinstance(data, dict):
        return None

    graph = data.get("@graph")

    if graph:
        result = _find_jsonld_article_image(graph)
        if result:
            return result

    object_type = data.get("@type", [])

    if isinstance(object_type, str):
        object_types = {object_type}
    else:
        object_types = set(object_type)

    if object_types & {
        "Article",
        "NewsArticle",
        "BlogPosting",
        "Report",
    }:
        image = data.get("image")

        if isinstance(image, str):
            return image

        if isinstance(image, list):
            for item in image:
                if isinstance(item, str):
                    return item

                if isinstance(item, dict):
                    url = item.get("url") or item.get("contentUrl")
                    if url:
                        return url

        if isinstance(image, dict):
            return image.get("url") or image.get("contentUrl")

    return None

def find_metadata_image(html_text, base_url):
    if not html_text:
        return None

    try:
        document = lxml_html.fromstring(html_text)
    except (ValueError, TypeError, ParserError):
        return None

    # 1. OpenGraph
    for xpath in (
        '//meta[@property="og:image"]/@content',
        '//meta[@property="og:image:secure_url"]/@content',
        '//meta[@name="twitter:image"]/@content',
        '//meta[@name="twitter:image:src"]/@content',
    ):
        values = document.xpath(xpath)

        for value in values:
            url = _normalize_image_url(value, base_url)
            if url:
                return url

    # 2. JSON-LD
    for script in document.xpath(
        '//script[@type="application/ld+json"]/text()'
    ):
        try:
            data = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue

        image = _find_jsonld_article_image(data)

        if image:
            url = _normalize_image_url(image, base_url)
            if url:
                return url

    return find_largest_image_in_html(html_text, base_url)


def _normalize_image_url(src, base_url):
    if not isinstance(src, str):
        return None

    src = src.strip()

    if not src:
        return None

    try:
        url = urljoin(base_url, src)
        parsed = urlsplit(url)
    except ValueError:
        return None

    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url

    return None