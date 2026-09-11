from dataclasses import dataclass
import os
from pathlib import Path
from lxml import etree

from lingua import LanguageDetectorBuilder

from config import get_config


@dataclass(frozen=True)
class LanguageProbability:
    language: str
    probability: float


_detector = LanguageDetectorBuilder.from_all_languages().build()


def detect_languages(
    text: str,
    *,
    limit: int = 2,
) -> list[LanguageProbability]:
    """
    Detect probable languages of the given text.

    Returns candidates ordered from the most to the least probable.
    Language codes are ISO 639-1 where available, e.g. "uk", "en", "de".
    """
    text = text.strip()

    if not text:
        return []

    confidences = _detector.compute_language_confidence_values(text)

    result: list[LanguageProbability] = []

    for confidence in confidences[:limit]:
        iso_code = confidence.language.iso_code_639_1

        if iso_code is None:
            continue

        result.append(
            LanguageProbability(
                language=iso_code.name.lower(),
                probability=confidence.value,
            )
        )

    return result


def perform_test_language_detection():
    for description_path in Path("yt-video").glob("*.desc"):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title_element = entry.find("title")
        description_element = entry.find("summary")
        text = title_element.text if title_element.text else "" + "\n" + description_element.text if description_element.text else ""
        detected_languages = detect_languages(text)
        print(f"Detected languages for '{text}':")
        for lang_prob in detected_languages:
            print(f"Language: {lang_prob.language}, Probability: {lang_prob.probability:.4f}")
        print()

def detect_language(description_path) -> str:
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title_element = entry.find("title")
        description_element = entry.find("summary")
        text = title_element.text if title_element.text else "" + "\n" + description_element.text if description_element.text else ""
        detected_languages = detect_languages(text)
        if detected_languages:
            if len(detected_languages) > 0 and detected_languages[0].probability > 0.6:
                return detected_languages[0].language
    return ""



if __name__ == "__main__":
    perform_test_language_detection()