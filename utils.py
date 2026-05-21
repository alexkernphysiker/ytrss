from datetime import datetime, timedelta
from time import sleep
import os
import subprocess
import re
from flask import send_file
import requests
from xml.etree import ElementTree
from pathlib import Path
import socket
from config import *
from ytrss_transcribe import get_engine_list

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

def update_names_dicts():
    channel_names_dict = get_config()["channel_names_dict"]
    playlist_names_dict = get_config()["playlist_names_dict"]
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
    save_config()

def get_channel_name(channel_id):
    channel_names_dict = get_config()["channel_names_dict"]
    if channel_id in channel_names_dict:
        return channel_names_dict[channel_id]
    sleep(5)  # To avoid hitting YouTube's rate limits
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id, timeout=20)
        if response.status_code == 200:
            channel_content = ElementTree.fromstring(response.text)
            channel_name = channel_content.find("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name").text
            channel_names_dict[channel_id] = channel_name
            save_config()
            return channel_name
        else:
            return "<Error fetching channel name>"
    except Exception as e:
        return "<Exception fetching channel name>"

def get_playlist_name(playlist_id):
    playlist_names_dict = get_config()["playlist_names_dict"]
    if playlist_id in playlist_names_dict:
        return playlist_names_dict[playlist_id]
    sleep(5)  # To avoid hitting YouTube's rate limits
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?playlist_id=" + playlist_id, timeout=20)
        if response.status_code == 200:
            playlist_content = ElementTree.fromstring(response.text)
            playlist_name = playlist_content.find("{http://www.w3.org/2005/Atom}title").text
            playlist_names_dict[playlist_id] = playlist_name
            save_config()
            return playlist_name
        else:
            return "<Error fetching playlist name>"
    except Exception as e:
        return "<Exception fetching playlist name>"

def load_source_list_from_file(filename):
    try:
        with open(filename, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

def save_source_list_to_file(filename, sources):
    with open(filename, 'w') as f:
        for source in sources:
            f.write(source + '\n') 

def duration_string(duration_secs):
    return f"{duration_secs//3600}:{(duration_secs%3600)//60:02d}:{duration_secs%60:02d}" if duration_secs >= 3600 else f"{duration_secs//60}:{duration_secs%60:02d}"

from lxml import etree
def generate_atom_feed(url_link, is_public):
    output="<feed xmlns=\"http://www.w3.org/2005/Atom\">  <title>Моя стрічка YouTube</title><link href=\"http://youtube.com/\" />\n"
    for description_path in Path("yt-video").glob("*.desc"):
        transcription_path = str(description_path).replace(".desc", ".txt")
        log_path = str(description_path).replace(".desc", ".log")
        fn = os.path.basename(description_path).replace(".desc", "")
        
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title_element = entry.find("title")
        description_element = entry.find("summary")

        try:
            duration_element = entry.find("duration")
            duration_str = duration_element.text
        except Exception as e:
            duration_str = "unknown duration"
        
        enclosure_element = entry.find("enclosure")
        if enclosure_element is not None:
            enclosure_element.set("url", enclosure_element.get("url").replace("__URL_LINK__", url_link))

        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        descr = description_element.text if description_element.text is not None else ""
        description_element.text = f"<p>[{duration_str}]</p> <br/>"
        if os.path.exists(log_path) and not os.path.exists(transcription_path) and not is_public:
            log_content = open(log_path, "r").read()
            description_element.text += "<p>[LOG]</p> <br/>" + log_content + "<br/>"
        if os.path.exists(transcription_path):
            string_list = open(transcription_path, "r").read().split('\n')
            description_element.text += f"<p>[VIDEO TRANSCRIPTION]</p> <br/>"
            for line in string_list:
                description_element.text += "<p>"+line+"</p> <br/>"
            description_element.text += f"<br/> <a href='{url_link}/remove_transcription/{fn}'>Remove this transcription</a><br/>"
            description_element.text += "<p>[VIDEO DESCRIPTION]</p> <br/>" + descr
        elif age < timedelta(days=get_config()["manual_transcript_days"]) and not is_public:
                transcribe_link = f"<br/> <a>Transcript with</a> <a>|</a> "
                for engine in get_engine_list():
                    transcribe_link += f"<a href='{url_link}/transcribe/{engine}/{fn}'>{engine.capitalize()}</a> <a>|</a> "
                description_element.text += transcribe_link + "<br/>[Video description] <br/>" + descr
        else:
            description_element.text += f"[Video description] <br/> " + descr

        output += etree.tostring(entry, encoding="unicode") + "\n"
    output += "</feed>\n"
    return output

def generate_transcriptions_page():
    pubs = {}
    for description_path in Path("yt-video").glob("*.desc"):
        transcription_path = str(description_path).replace(".desc", ".txt")
        fn = os.path.basename(description_path).replace(".desc", "")
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title_element = entry.find("title")
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        if age > timedelta(days=get_config()["max_days_read_page"]):
            continue
        if os.path.exists(transcription_path):
            output = ""
            string_list = open(transcription_path, "r").read().split('\n')
            for line in string_list:
                if line.strip() != "":
                    output += f"<p>{line}</p> <br/>"
                else:
                    output += f"<a href='#{fn}-title'>[back]</a>"
            pubs[age] = (fn, title_element.text, output)
    asc = {k: v for k, v in sorted(pubs.items(), key=lambda item: item[0])}
    titles = ""
    output = ""
    for age, (fn, title, content) in asc.items():
        titles += f"<li><div id='{fn}-title'><a href='#{fn}'>{title}</a><br/></div></li>"
        content = content
        output += f"<div id='{fn}'> <h2><li>{title}</li></h2><br/>{content}<br/> <a href='#{fn}-title'>Back to top</a></div>"
    return f"<html><body><h1>List of transcribed videos</h1><ul>{titles}</ul> <h1>Transcriptions</h1><ul>{output}</ul></body></html>"

def return_file(filename):
    return send_file("yt-video/"+filename)
