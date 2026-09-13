import os
import re
from lxml import etree
from xml.etree import ElementTree
from pathlib import Path
from thefuzz import fuzz

def get_known_episodes(directory="yt-video"):
    episodes = []
    
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)

    for description_path in Path("yt-video").glob("*.desc"):
        try:
            fn = os.path.basename(description_path).replace(".desc", "")
            parser1 = etree.XMLParser(encoding="utf-8", recover=True)
            input_entry = etree.parse(description_path, parser1)
            title_element = input_entry.find("title")
            description_element = input_entry.find("summary")
            id_element = input_entry.find("id")
            episodes.append({
                "id": fn,
                "title": title_element.text if title_element is not None else "",
                "description": description_element.text if description_element is not None else "",
            })
        except etree.ParseError as e:
            print(f"XML parsing error in file: {description_path}: {e}")
        except Exception as e:
            print(f"Unexpected error processing {description_path}: {e}")
            
    return episodes

def get_episode_transcription(episode_id):
    transcription_path = Path(f"yt-video/{episode_id}.transcription")
    if transcription_path.exists():
        with open(transcription_path, "r", encoding="utf-8") as f:
            return f.read()
    return None

def get_text_to_compare(episode, transcription=None):
    title = episode.get("title", "")
    description = episode.get("description", "")

    if transcription:
        combined_text = f"{title} {transcription}"
    else:
        combined_text = f"{title} {description}"

    return combined_text.lower()  

def compare_episodes(episode1, episode2):
    transcription1 = get_episode_transcription(episode1.get("id"))
    transcription2 = get_episode_transcription(episode2.get("id"))

    text1 = get_text_to_compare(episode1, transcription1 if transcription1 and transcription2 else None)
    text2 = get_text_to_compare(episode2, transcription2 if transcription2 and transcription1 else None)

    # Use token_set_ratio for better handling of variations in titles and descriptions
    score = fuzz.token_set_ratio(text1, text2)
    
    return score

def find_duplicate_episode(new_episode, threshold=85):

    best_match_fn = None
    highest_score = 0
    
    for known in get_known_episodes():
        score = compare_episodes(new_episode, known)
        if score > highest_score:
            highest_score = score
            best_match_fn = known.get("id")
            
    if highest_score >= threshold:
        return best_match_fn
        
    return None

def mark_episode_as_duplicate(episode_id):
    description_path = Path(f"yt-video/{episode_id}.desc")
    if description_path.exists():
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        
        duplicate_element = entry.find("duplicate")
        if duplicate_element is None:
            duplicate_element = etree.SubElement(entry.getroot(), "duplicate")
        duplicate_element.text = "true"
        
        entry.write(description_path, encoding="utf-8", xml_declaration=True)