import os
import subprocess
import string
import re
import requests
from xml.etree import ElementTree
from xml.etree import cElementTree
from datetime import datetime, timedelta, timezone
import dateutil.parser
import time
from pathlib import Path
import arrow
from openai import OpenAI
from sklearn import tree
from utils import *

max_days = 18

def cleanup():
    now = arrow.now()
    for file in Path("yt-video").glob("*"):
        if file.is_file():
            file_time = arrow.get(file.stat().st_mtime)
            if now - file_time > timedelta(days=max_days):
                print(f"Removing old video file: {file}")
                file.unlink()


format = "18/93/139/140/249/251"

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

def update_channels_feed():
    print(f"Fetching channels")
    links=[]
    for channel_id in load_source_list_from_file("channels.txt"):
        links.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    for playlist_id in load_source_list_from_file("playlists.txt"):
        links.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}")

    for link in links:
        try:
            response = requests.get(link, timeout=20)
            if response.status_code == 200:
                count_all=0
                count_used=0
                channel_content = ElementTree.fromstring(response.text)
                source_name = channel_content.find("{http://www.w3.org/2005/Atom}title").text
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
                            if time_since_insertion < timedelta(days=3):
                                file_size_yt = get_yt_file_size(link_element.get("href"))
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
                                        download_video(link_element.get("href"), file_path)
                                    else:
                                        print(f"Existing file for video {fn} is of expected size, using existing file")
                                else:
                                    print(f"Could not get expected file size for video {fn}, skip updating")
                                    continue
                            else:
                                print(f"Video {fn} is old enough, skip updating")
                                continue
                        else:
                            print(f"No existing file for video {fn}, downloading...")
                            if not download_video(link_element.get("href"), file_path):
                                print(f"Failed to download video {fn}, skipping item.")
                                continue
                        if os.path.exists(file_path):
                            length = os.path.getsize(file_path)
                            modTime = time.mktime(insertion_date.timetuple())
                            os.utime(file_path, (modTime, modTime))
                            enclosure_element = ElementTree.SubElement(entry_element, "enclosure", url="__URL_LINK__/file/"+fn+".mp4", type="video/mpeg", length=str(length))
                        else:
                            print(f"Failed to download video {fn}")
                        
                        description_element = ElementTree.SubElement(entry_element, "summary")
                        description_element.text = ""
                        if media_description is not None and media_description.text is not None:
                            string_list = media_description.text.split('\n')
                            for line in string_list:
                                description_element.text += "<p>"+line+"</p> <br/>"
                        id_element = ElementTree.SubElement(entry_element, "id")
                        id_element.text = link_element.get("href")
                        chanid_element = ElementTree.SubElement(entry_element, "yt:channelid")
                        chanid_element.text = channel_id
                        channame_element = ElementTree.SubElement(entry_element, "yt:channelname")
                        channame_element.text = source_name
                        if media_group is not None:
                            for media_content in media_group.findall("{http://search.yahoo.com/mrss/}content"):
                                media_content_element = ElementTree.SubElement(entry_element, "media:content", url=media_content.get("url"), type=media_content.get("type"))
                        with open(description_path, "w") as f:
                            item_string=ElementTree.tostring(entry_element, encoding='utf-8', method='xml').decode('utf-8')+"\n"
                            f.write(item_string)
                        modTime = time.mktime(insertion_date.timetuple())
                        os.utime(description_path, (modTime, modTime))
                        count_used += 1
                print(f"Source {link}: {source_name} [{count_used} entries used, {count_all} total entries].")
                time.sleep(1)
            else:
                print(f"Failed to fetch {link}: HTTP {response.status_code}")
        except requests.RequestException as e:
            print(f"Error fetching {link}: {e}")

if __name__ == "__main__":
    while True:
        cleanup()
        update_channels_feed()
        print("Sleeping before next update...")
        time.sleep(120)
    
