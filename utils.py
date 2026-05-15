from datetime import datetime, timedelta
import os
import subprocess
import re
from flask import send_file
import requests
from xml.etree import ElementTree
from pathlib import Path
import socket

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

channel_names_dict = {}
playlist_names_dict = {}

def update_names_dicts():
    global channel_names_dict, playlist_names_dict
    for description_path in Path("yt-video").glob("*.desc"):
            try:
                parser1 = etree.XMLParser(encoding="utf-8", recover=True)
                entry = etree.parse(description_path, parser1)
                channel_id_element = entry.find("yt:channelid")
                if channel_id_element is not None:
                    channel_name_element = entry.find("yt:channelname")
                    if channel_name_element is not None:
                        channel_names_dict[channel_id_element.text] = channel_name_element.text
                playlist_id_element = entry.find("yt:playlistid")
                if playlist_id_element is not None:
                    playlist_name_element = entry.find("yt:playlistname")
                    if playlist_name_element is not None:
                        playlist_names_dict[playlist_id_element.text] = playlist_name_element.text
            except Exception as e:
                continue

def get_channel_name(channel_id):
    global channel_names_dict
    if channel_id in channel_names_dict:
        return channel_names_dict[channel_id]
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id, timeout=20)
        if response.status_code == 200:
            channel_content = ElementTree.fromstring(response.text)
            return channel_content.find("{http://www.w3.org/2005/Atom}title").text
        else:
            return "<Error fetching channel name>"
    except Exception as e:
        return "<Exception fetching channel name>"

def get_playlist_name(playlist_id):
    global playlist_names_dict
    if playlist_id in playlist_names_dict:
        return playlist_names_dict[playlist_id]
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?playlist_id=" + playlist_id, timeout=20)
        if response.status_code == 200:
            playlist_content = ElementTree.fromstring(response.text)
            return playlist_content.find("{http://www.w3.org/2005/Atom}title").text
        else:
            return "<Error fetching playlist name>"
    except Exception as e:
        return "<Exception fetching playlist name>"

def load_source_list_from_file(filename):
    try:
        with open(filename, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"File {filename} not found")
        return []

def save_source_list_to_file(filename, sources):
    with open(filename, 'w') as f:
        for source in sources:
            f.write(source + '\n') 

def duration_string(duration_secs):
    return f"{duration_secs//3600}:{(duration_secs%3600)//60:02d}:{duration_secs%60:02d}" if duration_secs >= 3600 else f"{duration_secs//60}:{duration_secs%60:02d}"

from lxml import etree
def generate_feed(url_link, is_public):
    output="<feed xmlns=\"http://www.w3.org/2005/Atom\">  <title>Моя стрічка YouTube</title><link href=\"http://youtube.com/\" />\n"
    for description_path in Path("yt-video").glob("*.desc"):
        transcription_path = str(description_path).replace(".desc", ".txt")
        fn = os.path.basename(description_path).replace(".desc", "")
        
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title_element = entry.find("title")
        description_element = entry.find("summary")

        try:
            duration_element = entry.find("duration")
            duration_str = duration_element.text
        except Exception as e:
            duration_str = ""
        
        enclosure_element = entry.find("enclosure")
        if enclosure_element is not None:
            enclosure_element.set("url", enclosure_element.get("url").replace("__URL_LINK__", url_link))

        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time

        if os.path.exists(transcription_path):
            string_list = open(transcription_path, "r").read().split('\n')
            descr = description_element.text if description_element.text is not None else ""
            description_element.text = f"<p>[VIDEO TRANSCRIPTION]</p> <br/><p>[{duration_str}]</p> <br/>"
            for line in string_list:
                description_element.text += "<p>"+line+"</p> <br/>"
            description_element.text += "<p>[VIDEO DESCRIPTION]</p> <br/>" + descr
            title_element.text = "transcribed: " + title_element.text
        elif age < timedelta(days=7) and not is_public:
                transcribe_link = f"<br/> <a href='{url_link}/transcribe/{fn}'>Transcribe this video</a> <br/>[Video description] <br/><p>[{duration_str}]</p> <br/>"
                if not description_element.text is None:
                    description_element.text = transcribe_link + description_element.text
                else:
                    description_element.text = transcribe_link
        else:
            description_element.text = f"[Video description] <br/> <p>[{duration_str}]</p> <br/>" + (description_element.text if description_element.text is not None else "")

        output += etree.tostring(entry, encoding="unicode") + "\n"
    output += "</feed>\n"
    return output


def return_file(filename):
    return send_file("yt-video/"+filename)
