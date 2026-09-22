from datetime import datetime, timedelta
from time import sleep
import os
import re
from flask import send_file
import requests
import subprocess
from xml.etree import ElementTree
from pathlib import Path
from config import *
from ytrss_transcribe import get_engine_map
import html
import pendulum

def convert_date_to_iso_with_pendulum(date):
    date_cp = date
    for tzcode, tzoffs in {
        "UT": "+0", "UTC": "+0", "GMT": "+0",
        "EST": "-5", "EDT": "-4",
        "CST": "-6", "CDT": "-5",
        "MST": "-7", "MDT": "-6",
        "PST": "-8", "PDT": "-7",
        "HST": "-10", "AKST": "-9", "AKDT": "-8",
        "CEDT": "+2", "EET": "+2", "EEST": "+3",
        "CES": "+1", "MET": "+1" 
    }.items():
        date_cp.replace(tzcode, tzoffs)
    return pendulum.parse(date_cp, strict=False)


def parse_xml_response(response):
    return ElementTree.fromstring(response.content)

def remove_invalid_xml_characters(value):
    if value is None:
        return ""
    return re.sub(
        r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]",
        "",
        value,
    )


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
    sleep(1)  # To avoid hitting YouTube's rate limits
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id, headers=get_config()["headers"], timeout=20)
        if response.status_code == 200:
            channel_content = ElementTree.fromstring(response.text)
            channel_name = channel_content.find("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name").text
            channel_names_dict[channel_id] = channel_name
            save_config()
            return channel_name
        else:
            return ""
    except Exception as e:
        return ""

def get_playlist_name(playlist_id):
    playlist_names_dict = get_config()["playlist_names_dict"]
    if playlist_id in playlist_names_dict:
        return playlist_names_dict[playlist_id]
    sleep(1)  # To avoid hitting YouTube's rate limits
    try:
        response = requests.get("https://www.youtube.com/feeds/videos.xml?playlist_id=" + playlist_id, headers=get_config()["headers"], timeout=20)
        if response.status_code == 200:
            playlist_content = ElementTree.fromstring(response.text)
            playlist_name = playlist_content.find("{http://www.w3.org/2005/Atom}title").text
            playlist_names_dict[playlist_id] = playlist_name
            save_config()
            return playlist_name
        else:
            return ""
    except Exception as e:
        return ""

def get_rss_name(link):
    rss_names_dict = get_config()["rss_names_dict"]
    if link in rss_names_dict:
        return rss_names_dict[link]
    try:
        response = requests.get(link, timeout=20, headers=get_config()["headers"])
        if response.status_code == 200:
            rss = parse_xml_response(response)  
            channel = rss.find("channel")
            source_name = channel.find("title").text
            rss_names_dict[link] = source_name
            save_config()
            return source_name
        else:
            return ""
    except Exception as e:
        return ""

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

def probe_media(file_path):
    try:
        command = f"ffprobe -v error -show_entries format=format_name,format_long_name,duration -of json {file_path}"
        result = subprocess.run(command, shell=True, capture_output=True, timeout=60)
        data = json.loads(result.stdout)
        format_info = data.get("format", {})
        duration_value = format_info.get("duration")

        return {
            "duration": (
                round(float(duration_value))
                if duration_value is not None
                else None
            ),
            "format_names": format_info.get(
                "format_name", ""
            ).split(","),
            "format_long_name": format_info.get(
                "format_long_name"
            ),
            "streams": data.get("streams", []),
        }

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        print(f"media probe error: {error}")
        return None

def detect_mimetype(media_info):
    formats = set(media_info["format_names"])

    if "webm" in formats:
        return "webm", "video/webm"

    if "mp4" in formats or "mov" in formats:
        return "mp4", "video/mp4" 

    if "mp3" in formats:
        return "mp3", "audio/mpeg"

    if "ogg" in formats:
        return "ogg", "audio/ogg"

    if "flac" in formats:
        return "flac", "audio/flac"

    if "wav" in formats:
        return "wav", "audio/wav"

    if "m4a" in formats:
        return "m4a", "audio/m4a"

    return "raw", "application/octet-stream"

def generate_atom_feed(url_link, is_public):
    from lxml import etree
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)
    output=f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" 
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" 
     xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:atom="http://www.w3.org/2005/Atom">
    
    <channel>
        <title>{get_config()["title"]}</title>
        <link>{url_link}/read</link>
        <description>feed generated by ytrss</description>
        <atom:link href="{url_link}/feed" rel="self" type="application/rss+xml" />"""

    for description_path in Path("yt-video").glob("*.desc"):
        transcription_path = str(description_path).replace(".desc", ".txt")
        log_path = str(description_path).replace(".desc", ".log")
        fn = os.path.basename(description_path).replace(".desc", "")
        
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        input_entry = etree.parse(description_path, parser1)

        duplicate_element = input_entry.find("duplicate")
        if duplicate_element is not None and duplicate_element.text == "true":
            continue  # Skip duplicate entries
    
        title_element = input_entry.find("title")
        description_element = input_entry.find("summary")
        
        enclosure_element = input_entry.find("enclosure")
        if enclosure_element is not None:
            enclosure_element.set("url", enclosure_element.get("url").replace("__URL_LINK__", url_link))

        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        if age > timedelta(days=get_config()["deliver_days"]):
            continue
        descr = description_element.text if description_element.text is not None else ""
        description_element.text = ""
        if os.path.exists(log_path) and not os.path.exists(transcription_path) and not is_public:
            log_content = open(log_path, "r").read()
            description_element.text += "<p>[LOG]</p> <br/>" + log_content + "<br/>"
        auto_transcription = False
        auto_transcription_element = input_entry.find("auto_transcription")
        if auto_transcription_element is not None:
            auto_transcription = auto_transcription_element.text=="true"
        if os.path.exists(transcription_path):
                string_list = open(transcription_path, "r").read().split('\n')
                for line in string_list:
                    description_element.text += "<p>"+line+"</p> <br/>"
                if get_config()["re-transcription"]:
                    transcribe_link = f"<br/> <a>Get new text version</a> <a>|</a> "
                    for engine, engine_name in get_engine_map().items():
                        transcribe_link += f"<a href='{url_link}/transcribe/{engine}/{fn}'>{engine_name}</a> <a>|</a> "
                    description_element.text += f"<br/> {transcribe_link}<br/>"
                    description_element.text += f"<br/> <a href='{url_link}/remove_transcription/{fn}'>Remove this transcription</a><br/>"
        elif auto_transcription:
            continue # will wait for transcription and final deduplication
        else:
                if get_config()["re-transcription"]:
                    transcribe_link = f"<br/> <a>Transcript with</a> <a>|</a> "
                    for engine, engine_name in get_engine_map().items():
                        transcribe_link += f"<a href='{url_link}/transcribe/{engine}/{fn}'>{engine_name}</a> <a>|</a> "
                    description_element.text += transcribe_link
                description_element.text += descr
        description_element.set("type", "html")
        image_url = input_entry.find("image").get("href").replace("__URL_LINK__", url_link) if input_entry.find("image") is not None else ""
        input_duration_element = input_entry.find("duration")

        output_item = etree.Element("item")
        output_guid = etree.Element("guid")
        link_url = input_entry.find("link").get("href").replace("__URL_LINK__", url_link) if input_entry.find("link") is not None else ""
        output_guid.text = link_url
        output_item.append(output_guid)
        output_title = etree.Element("title")
        output_title.text = title_element.text
        output_item.append(output_title)
        output_link = etree.Element("link")
        output_link.text = link_url
        output_item.append(output_link)
        output_description = etree.Element("description")
        output_description.text = etree.CDATA(
            remove_invalid_xml_characters(html.unescape(description_element.text or ""))
        )
        output_item.append(output_description)
        output_pubDate = etree.Element("pubDate")
        output_pubDate.text = modified_time.strftime("%a, %d %b %Y %H:%M:%S +0000")
        output_image = etree.Element(f"{{{ITUNES_NS}}}image", href=image_url)
        output_item.append(output_image)
        output_image2 = etree.Element(f"{{{MEDIA_NS}}}thumbnail", url=image_url)
        output_item.append(output_image2)
        output_item.append(output_pubDate)
        if input_duration_element is not None:
            output_duration_element = etree.Element(f"{{{ITUNES_NS}}}duration")
            output_duration_element.text = input_duration_element.text
            output_item.append(output_duration_element)
        
        if enclosure_element is not None:
            if url_link not in enclosure_element.get("url"):
                output_item.append(enclosure_element)
            else:
                file_path = "yt-video/"+fn
                if os.path.exists(file_path):
                    length = os.path.getsize(file_path)
                    ext,media_type = detect_mimetype(probe_media(file_path))
                    url = f"{url_link}/file/{fn}.{ext}"
                    enclosure_element = etree.Element("enclosure", url=url, type=media_type, length=str(length))
                    output_item.append(enclosure_element)

        output += "\n    " + etree.tostring(output_item, encoding="utf-8", method="xml").decode("utf-8")

    output += """
    </channel>
</rss>"""
    return output

def generate_transcriptions_page(url_link):
    from lxml import etree
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    MEDIA_NS = "http://search.yahoo.com/mrss/"
    etree.register_namespace("itunes", ITUNES_NS)
    etree.register_namespace("media", MEDIA_NS)
    pubs = {}
    for description_path in Path("yt-video").glob("*.desc"):
        transcription_path = str(description_path).replace(".desc", ".txt")
        fn = os.path.basename(description_path).replace(".desc", "")
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)

        duplicate_element = entry.find("duplicate")
        if duplicate_element is not None and duplicate_element.text == "true":
            continue  # Skip duplicate entries

        title_element = entry.find("title")
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        if age > timedelta(days=get_config()["deliver_days"]):
            continue
        listen_url = entry.find("link").get("href").replace("__URL_LINK__", url_link) if entry.find("link") is not None else ""
        output = f"<a target='_blank' rel='noopener noreferrer' href='{listen_url}'>[View the episode]</a>"
        enclosure_url = entry.find("enclosure").get("url").replace("__URL_LINK__", url_link) if entry.find("enclosure") is not None else ""
        if enclosure_url != "":
            output += f"<a target='_blank' rel='noopener noreferrer' href='{enclosure_url}'>[enclosure]</a>"
        output += f"<br/><a href='#{fn}-end'>[next]</a>"
        image_url = entry.find("image").get("href").replace("__URL_LINK__", url_link) if entry.find("image") is not None else ""
        if image_url != "":
            output+=f"<br/> <img src='{image_url}' width='30%' alt='{title_element.text}'>"
        if os.path.exists(transcription_path):
            string_list = open(transcription_path, "r").read().split('\n')
            for line in string_list:
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        elif entry.find("summary") is not None and entry.find("summary").text is not None:
            for line in entry.find("summary").text.split('\n'):
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        pubs[age] = (fn, title_element.text, output, modified_time)
    asc = {k: v for k, v in sorted(pubs.items(), key=lambda item: item[0])}
    output = ""
    for age, (fn, title, content, modified_time) in asc.items():
        output += f"<div id='{fn}'> <h2><li>{title} ({modified_time})</li></h2><br/>{content}<br/></div><br/><div id='{fn}-end'><a href='#{fn}'>[back]</a></div><br/>"
    return f"<html><body><h1>{get_config()["title"]}</h1><ul>{output}</ul></body></html>"

def return_file(filename):
    return send_file("yt-video/"+filename)
