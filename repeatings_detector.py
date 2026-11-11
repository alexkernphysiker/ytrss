import os
import re
from lxml import etree
from xml.etree import ElementTree
from pathlib import Path
from thefuzz import fuzz
import json
from datetime import datetime, timezone
from config import get_config
from threading import RLock

_duplicate_lock = RLock()

def get_known_episodes(directory="yt-video"):
    episodes = []
    
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)

    for description_path in sorted(Path(directory).glob("*.desc")):
        try:
            fn = os.path.basename(description_path).replace(".desc", "")
            parser1 = etree.XMLParser(encoding="utf-8", recover=True)
            input_entry = etree.parse(description_path, parser1)
            duplicate_element = input_entry.find("duplicate")
            if duplicate_element is not None and duplicate_element.text == "true":
                continue
            title_element = input_entry.find("title")
            description_element = input_entry.find("summary")
            id_element = input_entry.find("id")
            episodes.append({
                "id": fn,
                "title": title_element.text if title_element is not None else "",
                "description": description_element.text if description_element is not None else "",
                "published": input_entry.findtext("published"),
            })
        except etree.ParseError as e:
            print(f"XML parsing error in file: {description_path}: {e}")
        except Exception as e:
            print(f"Unexpected error processing {description_path}: {e}")
            
    return episodes

def get_episode_transcription(episode_id):
    for suffix in (".raw_srt", ".srt", ".txt"):
        path = Path("yt-video", str(episode_id) + suffix)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if text.strip():
            return text
    return None


def get_text_to_compare(episode, transcription=None):
    title = episode.get("title", "")
    description = episode.get("description", "")

    if transcription:
        return f"{transcription}".lower()
    else:
        return f"{title} {description}".lower()

def compare_episodes(episode1, episode2):
    transcription1 = get_episode_transcription(episode1.get("id"))
    transcription2 = get_episode_transcription(episode2.get("id"))

    text1 = get_text_to_compare(episode1, transcription1 if transcription1 and transcription2 else None)
    text2 = get_text_to_compare(episode2, transcription2 if transcription2 and transcription1 else None)

    # Use token_set_ratio for better handling of variations in titles and descriptions
    score = fuzz.token_set_ratio(text1, text2)
    
    return score

def _publication_time(episode):
    published = episode.get("published")
    if not published:
        episode_id = episode.get("id")
        if not episode_id:
            return None
        try:
            entry = etree.parse(
                Path("yt-video", str(episode_id) + ".desc"),
                etree.XMLParser(encoding="utf-8", recover=True),
            )
            published = entry.findtext("published")
        except (OSError, etree.XMLSyntaxError):
            return None
    try:
        if isinstance(published, str):
            published = datetime.fromisoformat(published.strip().replace("Z", "+00:00"))
        if not isinstance(published, datetime):
            return None
        # Older stored dates without an offset are interpreted as UTC.
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def find_duplicate_episode(new_episode, threshold):
    with _duplicate_lock:
        published = _publication_time(new_episode)
        if published is None:
            return None
        maximum_seconds = get_config()["duplicate_max_age_difference_days"] * 86400
        best_match_fn = None
        highest_score = 0
        transcription = True if get_episode_transcription(new_episode.get("id")) else False
        for known in get_known_episodes():
            if known.get("id") == new_episode.get("id"):
                continue
            known_published = _publication_time(known)
            if known_published is None:
                continue
            if abs((published - known_published).total_seconds()) > maximum_seconds:
                continue
            if transcription:
                if not get_episode_transcription(known.get("id")):
                    continue
            score = compare_episodes(new_episode, known)
            if score > highest_score:
                highest_score = score
                best_match_fn = known.get("id")
            
        if highest_score >= threshold:
            return best_match_fn
        
        return None

def mark_episode_as_duplicate(episode_id, other_episode_id):
    with _duplicate_lock:
        if episode_id == other_episode_id:
            return False
        target_path = Path(f"yt-video/{other_episode_id}.desc")
        if not target_path.is_file():
            return False
        target = etree.parse(target_path, etree.XMLParser(encoding="utf-8", recover=True))
        target_duplicate = target.find("duplicate")
        if target_duplicate is not None and target_duplicate.text == "true":
            return False
        description_path = Path(f"yt-video/{episode_id}.desc")
        if description_path.exists():
            parser1 = etree.XMLParser(encoding="utf-8", recover=True)
            entry = etree.parse(description_path, parser1)
        
            duplicate_element = entry.find("duplicate")
            if duplicate_element is None:
                duplicate_element = etree.SubElement(entry.getroot(), "duplicate")
            duplicate_element.text = "true"

            other_episode_element = entry.find("duplicate_of")
            if other_episode_element is None:
                other_episode_element = etree.SubElement(entry.getroot(), "duplicate_of")
            other_episode_element.text = other_episode_id
        
            entry.write(description_path, encoding="utf-8", xml_declaration=True)
