import os
import subprocess
import yt_dlp
import string
import re
import requests
from xml.etree import ElementTree
from xml.etree import cElementTree
from datetime import datetime, timedelta, timezone
import dateutil.parser
from time import sleep, mktime
from pathlib import Path
import arrow
from random import shuffle
from utils import *
from config import *

def cleanup():
    now = arrow.now()
    for file in Path("yt-video").glob("*"):
        if file.is_file():
            file_time = arrow.get(file.stat().st_mtime)
            if now - file_time > timedelta(days=get_config()["max_days"]):
                print(f"Removing old video file: {file}")
                file.unlink()

def is_live(link):
    try:
        additional_options = get_config().get("yt-dlp-options")
        full_command = f"yt-dlp {additional_options} --skip-download --print is_live {link}"
        print(full_command)
        proc = subprocess.run(full_command, shell=True, capture_output=True)
        output = proc.stdout.decode().strip()
        return output.lower() == "true"
    except Exception as e:
        print(f"Error occurred while trying to check if video {link} is live: {str(e)}")
        return False

def download_video(link, filename):
    for params in get_config().get("yt-dlp-formats"):
        print(f"Trying to download video {filename} with parameters {params}p...")
        additional_options = get_config().get("yt-dlp-options")
        full_command = f"yt-dlp {additional_options} {params} -o {filename}.dl {link}"
        print(full_command)
        proc = subprocess.run(full_command, shell=True, capture_output=True)
        for file in Path(".").glob(filename + ".dl*"):
            os.rename(file, filename)
            print(f"Successfully downloaded video {filename} with parameters {params}p.")
            return True
        print(f"Failed to download video {filename} with parameters {params}. yt-dlp output: {proc.stderr.decode()}")
    print(f"Failed to download video {filename} with all attempted resolutions.")
    return False

def get_duration(file_path):
    command = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        file_path
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return int(float(result.stdout.strip()))
    except (subprocess.CalledProcessError, ValueError):
        return None

def update_channels_feed():
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)
    print("Fetching podcasts RSS subscriptions")
    for link in get_config()["rss_subscriptions"]:
        sleep(1)
        response = requests.get(link, timeout=60)
        if response.status_code == 200:
            rss = ElementTree.fromstring(response.text)
            channel = rss.find("channel")
            source_name = channel.find("title").text
            print(f"{link}: {source_name}")
            for entry in channel.findall("item"):
                    title = entry.find("title")
                    if title is None or not title.text:
                        print(f"Skipping entry with no title in source {source_name}")
                        continue
                    link_element = entry.find("link")
                    if link_element is None:
                        print(f"Skipping entry with no link in source {source_name}")
                        continue
                    published = entry.find("pubDate")
                    if published is None or not published.text:
                        print(f"Skipping entry with no published date in source {source_name}")
                        continue
                    insertion_date = dateutil.parser.parse(published.text)
                    time_since_insertion = datetime.now(timezone.utc) - insertion_date
                    media_description = entry.find("description")
                    media_thumbnail = entry.find(f"{{{ITUNES_NS}}}image")
                    if time_since_insertion < timedelta(days=get_config()["max_days"]):
                        entry_element = ElementTree.Element("entry")
                        title_element = ElementTree.SubElement(entry_element, "title")
                        title_element.text = "[" + source_name + "] " + title.text
                        print(f"Title: {title_element.text}")
                        link_element_dest = ElementTree.SubElement(entry_element, "link", href=link_element.text)
                        updated_element = ElementTree.SubElement(entry_element, "updated")
                        updated_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        published_element = ElementTree.SubElement(entry_element, "published")
                        published_element.text = insertion_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        print(f"Published: {published_element.text}")
                        if media_thumbnail is not None:
                            thumbnail_element = ElementTree.SubElement(entry_element, "image", href=media_thumbnail.get("href"))
                        chars = re.escape(string.punctuation)
                        fn = re.sub('['+chars+']', '',link_element.text)
                        description_element = ElementTree.SubElement(entry_element, "summary")
                        description_element.text = ""
                        if media_description is not None and media_description.text is not None:
                            string_list = media_description.text.split('\n')
                            for line in string_list:
                                description_element.text += "<p>"+line+"</p> <br/>"
                        id_element = ElementTree.SubElement(entry_element, "id")
                        id_element.text = link_element.text
                        source_enclosure = entry.find("enclosure")
                        if source_enclosure == None:
                            print(f"Item {title} does not contain enclosure")
                        else:
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url=source_enclosure.get("url"), type=source_enclosure.get("type"), length = source_enclosure.get("length"))

                        duration = get_duration(source_enclosure.get("url")) if source_enclosure is not None else None
                        if duration is not None:
                            duration_element = ElementTree.SubElement(entry_element, "duration")
                            duration_element.text = str(duration)
                        description_path = "yt-video/" + fn + ".desc"
                        with open(description_path, "w") as f:
                            item_string=ElementTree.tostring(entry_element, encoding='utf-8', method='xml').decode('utf-8')+"\n"
                            f.write(item_string)
                        modTime = mktime(insertion_date.timetuple())
                        os.utime(description_path, (modTime, modTime))
                        if get_config()["auto_transcript_hours"] > 0:
                            if time_since_insertion < timedelta(hours=get_config()["auto_transcript_hours"]):
                                print(f"Processing auto-transcription for video {fn}")
                                transcription_path = "yt-video/" + fn + ".txt"
                                if not os.path.exists(transcription_path) and not link in get_config()["sources_with_disabled_auto_transcription"]:
                                    video_list = load_source_list_from_file("transcription.txt")
                                    if not fn in video_list:
                                        print(f"Automatically scheduled video transcription {fn}")
                                        video_list.append(fn)
                                        save_source_list_to_file("transcription.txt", video_list)
                                    else:
                                        print(f"Video {fn} is already scheduled for transcription")
                                else:
                                    print(f"Video {fn} already has transcription")

    print(f"Fetching youtube channels and playlists")
    links=[]
    for channel_id in get_config()["channel_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    for playlist_id in get_config()["playlist_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}")
    shuffle(links)
    for link in links:
        sleep(1)
        try:
            response = requests.get(link, timeout=60)
            if response.status_code == 200:
                count_all=0
                count_used=0
                channel_content = ElementTree.fromstring(response.text)
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
                    insertion_date = dateutil.parser.parse(published.text)
                    time_since_insertion = datetime.now(timezone.utc) - insertion_date
                    count_all += 1
                    media_group = entry.find("{http://search.yahoo.com/mrss/}group")
                    media_description = None
                    if media_group is not None:
                        media_description = media_group.find("{http://search.yahoo.com/mrss/}description")
                        media_thumbnail = media_group.find("{http://search.yahoo.com/mrss/}thumbnail")
                    else:
                        media_description = entry.find("{http://www.w3.org/2005/Atom}summary")
                    if "shorts" not in link_element.get("href") and time_since_insertion < timedelta(days=get_config()["max_days"]):
                        file_duration_yt=""
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
                        if os.path.exists(file_path):
                            print(f"Existing file for video {fn} found")
                        else:
                            if is_live(link_element.get("href")):
                                print(f"Video {fn} is currently live, skipping item.")
                                continue
                            if source_id not in get_config()["sources_with_disabled_downloading"]:
                                print(f"No existing file for video {fn}, downloading...")
                                if not download_video(link_element.get("href"), file_path):
                                    print(f"Failed to download video {fn}, skipping item.")
                                    continue
                            else:
                                print(f"Downloading is disabled for source {source_id}")
                        if os.path.exists(file_path):
                            length = os.path.getsize(file_path)
                            modTime = mktime(insertion_date.timetuple())
                            os.utime(file_path, (modTime, modTime))
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url="__URL_LINK__/file/"+fn+".mp4", type="video/webm", length=str(length))
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
