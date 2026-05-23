import os
import subprocess
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

max_days = get_config()["max_days"]
recheck_size_days = get_config()["recheck_size_days"]
auto_transcript_hours = get_config()["auto_transcript_hours"]

def cleanup():
    now = arrow.now()
    for file in Path("yt-video").glob("*"):
        if file.is_file():
            file_time = arrow.get(file.stat().st_mtime)
            if now - file_time > timedelta(days=max_days):
                print(f"Removing old video file: {file}")
                file.unlink()


format = "18/93/139/140/249/251"

def is_live(link):
    try:
        proc = subprocess.run("yt-dlp --skip-download --print is_live "+link, shell=True, capture_output=True)
        output = proc.stdout.decode().strip()
        return output.lower() == "true"
    except Exception as e:
        print(f"Error occurred while trying to check if video {link} is live: {str(e)}")
        return False

def download_subtitles(link, filename):
    try:
        description_path = "yt-video/" + filename + ".desc"
        transcription_path = "yt-video/" + filename + ".txt"
        proc = subprocess.run(f"yt-dlp --skip-download --write-auto-subs --write-subs --sub-lang {detect_language(description_path)} --convert-subs srt --sub-format txt --postprocessor-args \"-ss 00:00:00 -to 99:59:59 -f srt - | sed '/^[0-9]*:[0-9]*:[0-9]*,[0-9]* --> [0-9]*:[0-9]*:[0-9]*,[0-9]*$/d' | tr -s '\\n' ' ' > {transcription_path}\" {link}", shell=True, capture_output=True)
        for line in proc.stdout.decode().splitlines():
            if line.strip().startswith("[download] Destination: "):
                srtname = line.strip().split("[download] Destination: ")[-1]
                if os.path.exists(srtname):
                    os.rename(srtname, transcription_path)
                    modtime = os.path.getmtime(description_path)
                    os.utime(transcription_path, (modtime, modtime))
                    print(f"Successfully downloaded subtitles for video {filename}")
                    return True
        print(f"Failed to download subtitles for video {filename}. yt-dlp output: {proc.stderr.decode()}")
        return False
    except Exception as e:
        print(f"Error occurred while trying to download subtitles for video {link}: {str(e)}")
        return False

def get_yt_file_size(link):
    try:
        proc = subprocess.run("yt-dlp  -f " + format + " --print \"%(filesize,filesize_approx)s\" "+link, shell=True, capture_output=True)
        return int(proc.stdout.decode().strip())
    except Exception as e:
        print(f"Error occurred while trying to get file size for video {link}: {str(e)}")
        return 0

def get_file_duration(link):
    try:
        proc = subprocess.run("yt-dlp  -f " + format + " --print \"%(duration)s\" "+link, shell=True, capture_output=True)
        return proc.stdout.decode().strip()
    except Exception as e:
        print(f"Error occurred while trying to get duration for video {link}: {str(e)}")
        return ""
def download_video(link, filename):
    print(f"Trying to download video {filename} with format {format}...")
    proc = subprocess.run("yt-dlp -f " + format + " -o " + filename + " "+link, shell=True, capture_output=True)
    if os.path.exists(filename):
        print(f"Successfully downloaded video {filename } with format {format}")   
        return True
    else:
        print(f"Failed to download video {filename} with format {format}. yt-dlp output: {proc.stderr.decode()}")
        if re.search('premiere', proc.stderr.decode(), re.IGNORECASE):
            print(f"Video {filename} is a future premiere, skipping item.")
            return False
        if re.search('live', proc.stderr.decode(), re.IGNORECASE):
            print(f"Video {filename} is a future live, skipping item.")
            return False
        return False

def update_channels_feed():
    print(f"Fetching channels")
    links=[]
    for channel_id in get_config()["channel_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    for playlist_id in get_config()["playlist_subscriptions"]:
        links.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}")
    shuffle(links)
    for link in links:
        sleep(1)
        try:
            response = requests.get(link, timeout=30)
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
                    if "shorts" not in link_element.get("href") and time_since_insertion < timedelta(days=max_days):
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
                            thumbnail_element = ElementTree.SubElement(entry_element, "itunes:image", href=media_thumbnail.get("url"))
                        chars = re.escape(string.punctuation)
                        fn = re.sub('['+chars+']', '',link_element.get("href"))
                        file_path = "yt-video/" + fn
                        description_path = "yt-video/" + fn + ".desc"
                        if os.path.exists(file_path):
                            if time_since_insertion < timedelta(days=recheck_size_days):
                                file_size_yt = get_yt_file_size(link_element.get("href"))
                                sleep(1)
                                file_duration_yt = get_file_duration(link_element.get("href"))
                                try:
                                    duration_secs = int(file_duration_yt)
                                    duration_string_element = ElementTree.SubElement(entry_element, "duration")
                                    duration_string_element.text = duration_string(duration_secs)
                                    duration_string_element2 = ElementTree.SubElement(entry_element, "itunes:duration")
                                    duration_string_element2.text = duration_string(duration_secs)
                                except:
                                    print(f"Could not parse duration for video {fn}, skipping duration string")
                                if file_size_yt > 0:
                                    try:
                                        file_size = os.path.getsize(file_path)
                                    except OSError:
                                        print(f"Error occurred while trying to get size of existing file for video {fn}")
                                        file_size = 0
                                    if file_size_yt != file_size:
                                        print(f"File for video {fn} has different size than expected, redownloading...")
                                        os.remove(file_path)
                                        sleep(1)
                                        if not download_video(link_element.get("href"), file_path):
                                            print(f"Failed to download video {fn}, skipping item.")
                                            continue
                                    else:
                                        print(f"Existing file for video {fn} is of expected size, using existing file")
                                else:
                                    print(f"Could not get expected file size for video {fn}, skip updating")
                                    continue
                            else:
                                print(f"Video {fn} is old enough, skip updating")
                                continue
                        else:
                            sleep(1)
                            if is_live(link_element.get("href")):
                                print(f"Video {fn} is currently live, skipping item.")
                                continue
                            if source_id not in get_config()["sources_with_disabled_downloading"]:
                                print(f"No existing file for video {fn}, downloading...")
                                sleep(1)
                                if not download_video(link_element.get("href"), file_path):
                                    print(f"Failed to download video {fn}, skipping item.")
                                    continue
                            else:
                                print(f"Downloading is disabled for source {source_id}")
                        if os.path.exists(file_path):
                            length = os.path.getsize(file_path)
                            modTime = mktime(insertion_date.timetuple())
                            os.utime(file_path, (modTime, modTime))
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url="__URL_LINK__/file/"+fn+".mp4", type="video/mpeg", length=str(length))
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
                        if auto_transcript_hours > 0 and os.path.exists(file_path):
                            if time_since_insertion < timedelta(hours=auto_transcript_hours):
                                print(f"Processing auto-transcription for video {fn}")
                                transcription_path = "yt-video/" + fn + ".txt"
                                if not os.path.exists(transcription_path) and not source_id in get_config()["sources_with_disabled_auto_transcription"]:
                                    sleep(1)
                                    download_subtitles(link_element.get("href"), fn)
                                    if not os.path.exists(transcription_path):
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
