import os
import subprocess
import yt_dlp
import string
import re
import requests
from xml.etree import ElementTree
from datetime import datetime, timedelta, timezone
import hashlib
from time import sleep, mktime
from pathlib import Path
import arrow
from random import shuffle
from urllib.parse import urljoin, urlsplit
from lxml.etree import ParserError
from lxml import html as lxml_html
from repeatings_detector import find_duplicate_episode
from utils import *
from config import *
from extract_page import *
from ytrss_transcribe import get_enclosure_link

def cleanup():
    now = arrow.now()
    for file in Path("yt-video").glob("*"):
        if file.is_file():
            file_time = arrow.get(file.stat().st_mtime)
            if now - file_time > timedelta(days=get_config()["max_days"]):
                print(f"Removing old video file: {file}")
                file.unlink()

def get_live_status(link):
    if not get_config().get("yt-dlp-enabled"):
        print(f"Interacting of yt-dlp with youtube is disabled globally in the configuration.")
        return False
    try:
        additional_options = get_config().get("yt-dlp-options")
        full_command = f"yt-dlp {additional_options} --skip-download --print live_status {link}"
        print(full_command)
        proc = subprocess.run(full_command, shell=True, capture_output=True, timeout=60)
        output = proc.stdout.decode().strip()
        return output.lower()
    except Exception as e:
        print(f"Error occurred while trying to check if video {link} is live: {str(e)}")
        return ""

def download_video(link, filename):
    if not get_config().get("yt-dlp-enabled"):
        print(f"Interacting of yt-dlp with youtube is disabled globally in the configuration.")
        return False
    for params in get_config().get("yt-dlp-formats"):
        sleep(get_config().get("yt-dlp-delay"))
        additional_options = get_config().get("yt-dlp-options")
        full_command = f"yt-dlp {additional_options} {params} -o {filename}.dl {link}"
        print(full_command)
        proc = subprocess.run(full_command, shell=True, capture_output=True, timeout=1800)
        for file in Path(".").glob(filename + ".dl*"):
            os.rename(file, filename)
            print(f"Successfully downloaded video {filename}.")
            return True
        print(f"Failed to download video {filename}. yt-dlp output: {proc.stderr.decode()}")
    print(f"Failed to download video {filename} with all attempted resolutions.")
    return False

def get_duration(file_path):
    command = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {file_path}"
    try:
        result = subprocess.run(command, shell=True, capture_output=True, timeout=60)
        output = int(float(result.stdout.decode().strip()))
        return output
    except (subprocess.CalledProcessError, ValueError):
        print("duration estimation error")
        return None

def find_image_in_html(html_text, base_url):
    if not html_text or not html_text.strip():
        return None

    try:
        root = lxml_html.fragment_fromstring(
            html_text,
            create_parent="div",
        )
    except (ParserError, ValueError):
        return None

    for img in root.iter("img"):
        # data-src часто містить справжню картинку
        # при відкладеному завантаженні.
        for attribute in ("data-src", "src"):
            src = (img.get(attribute) or "").strip()
            if not src:
                continue

            try:
                image_url = urljoin(base_url, src)
                parsed = urlsplit(image_url)
            except ValueError:
                continue

            if parsed.scheme in ("http", "https") and parsed.netloc:
                return image_url

    return None

def update_channels_feed():
    from lxml import etree
    NS = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "media": "http://search.yahoo.com/mrss/",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "atom": "http://www.w3.org/2005/Atom",
        "podcast": "https://podcastindex.org/namespace/1.0",
    }
    for ns_name in NS.keys():
        etree.register_namespace(ns_name, NS[ns_name])
    print("Fetching podcasts RSS subscriptions")
    links = get_config()["rss_subscriptions"]
    shuffle(links)
    for link in links:
        try:
            sleep(get_config().get("delay-between-fetches"))
            response = requests.get(link, timeout=60, headers=get_config()["headers"], proxies=get_config().get("proxies-rss"))
            if response.status_code == 200:
                rss = parse_xml_response(response)
                channel = rss.find("channel", NS)
                source_name = channel.find("title", NS).text
                print(f"{link}: {source_name}")
                for entry in channel.findall("item", NS):
                    title = entry.find("title", NS)
                    if title is None or not title.text:
                        print(f"Skipping entry with no title in source {source_name}")
                        continue
                    link_element = entry.find("link", NS)
                    published = entry.find("pubDate", NS)
                    if published is None or not published.text:
                        print(f"Skipping entry with no published date in source {source_name}")
                        continue
                    insertion_date = convert_date_to_iso_with_pendulum(published.text)
                    time_since_insertion = datetime.now(timezone.utc) - insertion_date
                    media_description = entry.find("description", NS)
                    media_thumbnail = entry.find("itunes:image", NS)
                    if time_since_insertion < timedelta(days=get_config()["deliver_days"]):
                        entry_element = ElementTree.Element("entry")
                        title_element = ElementTree.SubElement(entry_element, "title")
                        title_element.text = "[" + source_name + "] " + title.text
                        print(f"Title: {title_element.text}")
                        if link_element is not None and link_element.text is not None:
                            link_element_dest = ElementTree.SubElement(entry_element, "link", href=link_element.text)
                        updated_element = ElementTree.SubElement(entry_element, "updated")
                        updated_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        published_element = ElementTree.SubElement(entry_element, "published")
                        published_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        print(f"Published: {published_element.text}")
                        if media_thumbnail is not None:
                            thumbnail_element = ElementTree.SubElement(entry_element, "image", href=media_thumbnail.get("href"))
                        chars = re.escape(string.punctuation)
                        source_enclosure = entry.find("enclosure", NS)
                        id_element = ElementTree.SubElement(entry_element, "id")
                        fn = ""
                        if link_element is not None and link_element.text is not None:
                            fn += re.sub('['+chars+']', '',link_element.text)
                            id_element.text = link_element.text
                        if source_enclosure is not None and source_enclosure.get("url") is not None:
                            fn += re.sub('['+chars+']', '',source_enclosure.get("url"))
                            id_element.text = source_enclosure.get("url")
                        if fn == "":
                                print(f"Skipping entry with no link and no enclosure in source {source_name}")
                                continue
                        fn = link + fn
                        fn = hashlib.sha256(fn.encode()).hexdigest()

                        if not os.path.exists("yt-video/" + fn + ".desc"):
                            print(f"New episode detected: {fn}")
                            new_episode = {
                                "id": fn,
                                "title": title.text if title.text is not None else "",
                                "description": media_description.text if media_description is not None else "",
                            }
                            duplicate_fn = find_duplicate_episode(new_episode, threshold=get_config()["duplicate_detection_threshold"])
                            if duplicate_fn is not None:
                                if get_enclosure_link(duplicate_fn) is not None \
                                    or source_enclosure is None:
                                    print(f"Duplicate episode found for {fn}, skipping download. Duplicate ID: {duplicate_fn}")
                                    continue

                        article = fetch_readable_article(
                            link_element.text,
                            headers=get_config()["headers"],
                            proxies=get_config().get("proxies-rss"),
                        ) if link_element is not None and link_element.text is not None and source_enclosure is None else None

                        description_element = ElementTree.SubElement(entry_element, "summary")
                        description_element.text = ""
                        if media_description is not None and media_description.text is not None:
                            string_list = media_description.text.split('\n')
                            for line in string_list:
                                description_element.text += "<p>"+line+"</p> <br/>"
                        content_element = entry.find("content", NS)
                        if content_element is not None:
                            description_element.text = content_element.text
                        else:
                            content_element = entry.find("content:encoded", NS)
                            if content_element is not None:
                                description_element.text = content_element.text
                                if media_thumbnail is None and link_element is not None and link_element.text is not None:
                                    img_url = find_image_in_html(content_element.text, link_element.text)
                                    if False and img_url is None:
                                        if article is not None:
                                            img_url = find_image_in_html(
                                                article["html"],
                                                base_url=article["url"],
                                            )
                                    if img_url is not None:
                                        thumbnail_element = ElementTree.SubElement(entry_element, "image", href=img_url)

                        transcription_path = "yt-video/" + fn + ".txt"
                        if source_enclosure is not None:
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url=source_enclosure.get("url"), type=source_enclosure.get("type"), length = source_enclosure.get("length"))
                        elif not os.path.exists(transcription_path):
                            if looks_like_full_article(description_element.text):
                                with open(transcription_path, "w") as f:
                                    f.write(extract_plain_text(description_element.text))
                                    print("Description seems to be long enough to be considered as full text. No need to transcript")
                            if article is not None:
                                if html_text_length(article["html"]) > html_text_length(description_element.text) \
                                    and looks_like_full_article(article["html"]):
                                    with open(transcription_path, "w") as f:
                                        f.write(extract_plain_text(article["html"]))
                                        print("Extracted page text is longer than description. It is considered as full text. No need to transcript")

                        duration = get_duration(source_enclosure.get("url")) if source_enclosure is not None else None
                        if duration is not None:
                            duration_element = ElementTree.SubElement(entry_element, "duration")
                            duration_element.text = str(duration)
                        auto_transcription_element = ElementTree.SubElement(entry_element, "auto_transcription")
                        auto_transcription_element.text = "true" if link not in get_config()["sources_with_disabled_auto_transcription"] else "false"
                        description_path = "yt-video/" + fn + ".desc"
                        with open(description_path, "w") as f:
                            item_string=ElementTree.tostring(entry_element, encoding='utf-8', method='xml').decode('utf-8')+"\n"
                            f.write(item_string)
                        modTime = mktime(insertion_date.timetuple())
                        os.utime(description_path, (modTime, modTime))
                        if os.path.exists(transcription_path):
                            os.utime(transcription_path, (modTime, modTime))
                        if get_config()["auto_transcript_hours"] > 0:
                            if time_since_insertion < timedelta(hours=get_config()["auto_transcript_hours"]):
                                print(f"Processing auto-transcription for video {fn}")
                                if not os.path.exists(transcription_path) and not link in get_config()["sources_with_disabled_auto_transcription"]:
                                    video_list = load_source_list_from_file("transcription_rss.txt")
                                    if not fn in video_list:
                                        print(f"Automatically scheduled video transcription_rss {fn}")
                                        video_list.append(fn)
                                        save_source_list_to_file("transcription_rss.txt", video_list)
                                    else:
                                        print(f"Video {fn} is already scheduled for transcription")
                                else:
                                    print(f"Video {fn} already has transcription")
            else:
                print(f"Failed to fetch {link}: HTTP {response.status_code}")
        except requests.RequestException as e:
            print(f"Error fetching {link}: {e}")
    print(f"Fetching youtube channels and playlists")
    links=[]
    for channel_id in get_config()["channel_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    for playlist_id in get_config()["playlist_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}")
    shuffle(links)
    for link in links:
        sleep(get_config().get("delay-between-fetches"))
        try:
            response = requests.get(link, timeout=60, headers=get_config()["headers"], proxies=get_config().get("proxies-youtube"))
            if response.status_code == 200:
                count_all=0
                count_used=0
                channel_content = parse_xml_response(response)
                source_name = channel_content.find("{http://www.w3.org/2005/Atom}title").text
                if "playlist_id" in link:
                    source_id= link.split("playlist_id=")[-1]
                else:
                    source_id= link.split("channel_id=")[-1]
                print(f"{link}: {source_name}")
                for entry in channel_content.findall("{http://www.w3.org/2005/Atom}entry"):
                    title = entry.find("{http://www.w3.org/2005/Atom}title")
                    if title is None or not title.text:
                        print(f"Skipping entry with no title in source {source_name}")
                        continue
                    link_element = entry.find("{http://www.w3.org/2005/Atom}link")
                    if link_element is None or not link_element.get("href"):
                        print(f"Skipping entry with no link in source {source_name}")
                        continue
                    published = entry.find("{http://www.w3.org/2005/Atom}published")
                    if published is None or not published.text:
                        print(f"Skipping entry with no published date in source {source_name}")
                        continue
                    insertion_date =convert_date_to_iso_with_pendulum(published.text)
                    time_since_insertion = datetime.now(timezone.utc) - insertion_date
                    count_all += 1
                    media_group = entry.find("{http://search.yahoo.com/mrss/}group")
                    media_description = None
                    if media_group is not None:
                        media_description = media_group.find("{http://search.yahoo.com/mrss/}description")
                        media_thumbnail = media_group.find("{http://search.yahoo.com/mrss/}thumbnail")
                    else:
                        media_description = entry.find("{http://www.w3.org/2005/Atom}summary")
                    if "shorts" not in link_element.get("href") and time_since_insertion < timedelta(days=get_config()["deliver_days"]):
                        entry_element = ElementTree.Element("entry")
                        title_element = ElementTree.SubElement(entry_element, "title")
                        title_element.text = "[" + source_name + "] " + title.text
                        print(f"Title: {title_element.text}")
                        link_element_dest = ElementTree.SubElement(entry_element, "link", href=link_element.get("href"))
                        updated_element = ElementTree.SubElement(entry_element, "updated")
                        updated_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        published_element = ElementTree.SubElement(entry_element, "published")
                        published_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        print(f"Published: {published_element.text}")
                        if media_thumbnail is not None:
                            thumbnail_element = ElementTree.SubElement(entry_element, "image", href=media_thumbnail.get("url"))
                        chars = re.escape(string.punctuation)
                        fn = re.sub('['+chars+']', '',link_element.get("href"))
                        file_path = "yt-video/" + fn
                        description_path = "yt-video/" + fn + ".desc"
                        if not os.path.exists(description_path):
                            print(f"New episode detected: {fn}")
                            new_episode = {
                                "id": fn,
                                "title": title_element.text if title_element.text is not None else "",
                                "description": media_description.text if media_description is not None else "",
                            }
                            duplicate_fn = find_duplicate_episode(new_episode, threshold=get_config()["duplicate_detection_threshold"])
                            if duplicate_fn is not None:
                                print(f"Duplicate episode found for {fn}, skipping download. Duplicate ID: {duplicate_fn}")
                                continue

                        if os.path.exists(file_path):
                            print(f"Existing file for video {fn} found")
                        else:
                            if get_live_status(link_element.get("href")) in ["is_live", "is_upcoming"]:
                                print(f"Video {fn} is currently live, skipping item.")
                                continue
                            if source_id not in get_config()["sources_with_disabled_downloading"]:
                                print(f"No existing file for video {fn}, downloading...")
                                if not download_video(link_element.get("href"), file_path):
                                    if time_since_insertion < timedelta(hours=get_config()["wait_for_download_hours"]):
                                        print(f"Video {fn} is too new and failed to download, skipping item.")
                                        continue
                                    print(f"Failed to download video {fn} but proceeding.")
                            else:
                                print(f"Downloading is disabled for source {source_id}")
                        if os.path.exists(file_path):
                            length = os.path.getsize(file_path)
                            modTime = mktime(insertion_date.timetuple())
                            os.utime(file_path, (modTime, modTime))
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url="__URL_LINK__/file/"+fn+".mp4", type="video/mp4", length=str(length))
                            duration = get_duration(file_path)
                            if duration is not None:
                                duration_element = ElementTree.SubElement(entry_element, "duration")
                                duration_element.text = str(duration)
                        else:
                            print(f"The entry {fn} does not have a file after download attempt, skipping enclosure element.")
                        
                        description_element = ElementTree.SubElement(entry_element, "summary")
                        description_element.text = ""
                        if media_description is not None and media_description.text is not None:
                            string_list = media_description.text.split('\n')
                            for line in string_list:
                                description_element.text += "<p>"+line+"</p> <br/>"
                        id_element = ElementTree.SubElement(entry_element, "id")
                        id_element.text = link_element.get("href")

                        if "playlist_id" in link:
                            playlist_name_element = ElementTree.SubElement(entry_element, "yt:playlistname")
                            playlist_name_element.text = source_name
                            playlistid_element = ElementTree.SubElement(entry_element, "yt:playlistid")
                            playlistid_element.text = link.split("playlist_id=")[-1]
                        else:
                            channelid_element = ElementTree.SubElement(entry_element, "yt:channelid")
                            channelid_element.text = link.split("channel_id=")[-1]
                            channelname_element = ElementTree.SubElement(entry_element, "yt:channelname")
                            channelname_element.text = source_name

                        if media_group is not None:
                            for media_content in media_group.findall("{http://search.yahoo.com/mrss/}content"):
                                media_content_element = ElementTree.SubElement(entry_element, "media:content", url=media_content.get("url"), type=media_content.get("type"))
                        auto_transcription_element = ElementTree.SubElement(entry_element, "auto_transcription")
                        auto_transcription_element.text = "true" if source_id not in get_config()["sources_with_disabled_auto_transcription"] else "false"
                        with open(description_path, "w") as f:
                            item_string=ElementTree.tostring(entry_element, encoding='utf-8', method='xml').decode('utf-8')+"\n"
                            f.write(item_string)
                        if get_config()["auto_transcript_hours"] > 0:
                            if time_since_insertion < timedelta(hours=get_config()["auto_transcript_hours"]):
                                print(f"Processing auto-transcription for video {fn}")
                                transcription_path = "yt-video/" + fn + ".txt"
                                if not os.path.exists(transcription_path) and not source_id in get_config()["sources_with_disabled_auto_transcription"]:
                                    video_list = load_source_list_from_file("transcription.txt")
                                    if not fn in video_list:
                                        print(f"Automatically scheduled video transcription {fn}")
                                        video_list.append(fn)
                                        save_source_list_to_file("transcription.txt", video_list)
                                    else:
                                        print(f"Video {fn} is already scheduled for transcription")
                            else:
                                print(f"Video {fn} already has transcription")
                        modTime = mktime(insertion_date.timetuple())
                        os.utime(description_path, (modTime, modTime))
                        count_used += 1
                print(f"Source {link}: {source_name} [{count_used} entries used, {count_all} total entries].")
            else:
                print(f"Failed to fetch {link}: HTTP {response.status_code}")
        except requests.RequestException as e:
            print(f"Error fetching {link}: {e}")
            

if __name__ == "__main__":
    cleanup()
    update_channels_feed()
    update_names_dicts()
    save_config()
