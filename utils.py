from datetime import datetime, timedelta
from time import sleep
import os
import re
from flask import send_file
import requests
import subprocess
from xml.etree import ElementTree
from pathlib import Path
from random import seed, random
from config import edit_config, get_config
import html
import json
import pendulum
from lxml import etree
from tinyurl import get_share_url
import json
from html import escape
from urllib.parse import quote
from web_ui import page_start as _read_page_start

def clp_copy_button(text: str) -> str:
    payload = escape(json.dumps(text), quote=True)

    return f"""
<span class="copy-widget">
  <button type="button" data-text="{payload}">Share</button>
  <span role="status"></span>

  <script>
    (() => {{
      const widget = document.currentScript.parentElement;
      const button = widget.querySelector("button");
      const status = widget.querySelector('[role="status"]');

      button.addEventListener("click", async () => {{
        const text = JSON.parse(button.dataset.text);

        try {{
          if (window.isSecureContext && navigator.clipboard?.writeText) {{
            await navigator.clipboard.writeText(text);
          }} else {{
            const field = document.createElement("textarea");
            field.value = text;
            field.style.position = "fixed";
            field.style.left = "-9999px";
            document.body.appendChild(field);

            try {{
              field.focus();
              field.select();
              field.setSelectionRange(0, field.value.length);

              if (!document.execCommand("copy")) {{
                throw new Error("Cannot copy to clipboard");
              }}
            }} finally {{
              field.remove();
            }}
          }}

          status.textContent = "Copied to clipboard!";
        }} catch {{
          status.textContent = "Copying is not available.";
          window.prompt("Text to copy:", text);
        }}
      }});
    }})();
  </script>
</span>
"""

seed()

def filter_subs(long_text, max_length = 0):
    output=""
    prev_line=""
    for line in long_text.splitlines():
        if line.strip() == "":
            continue
        if "-->" in line and "align:" in line and "position:" in line:
            continue
        if "<c>" in line or "</c>" in line:
            continue
        if line.strip()==prev_line:
            continue

        prev_line = line.strip()
        if max_length > 0:
            if len(output) + len(line) + 1 > max_length:
                per_sentence = output.split(".\n")
                if len(per_sentence) > 1:
                    yield (".\n".join(per_sentence[:-1]) + ".\n").replace("? ", "?\n").replace("! ", "!\n").replace(">> ", "\n>> ")
                    output = per_sentence[-1]
                else:
                    yield output
                    output=""
        output += line.replace(". ", ".\n") + " "
    if output != "":
        yield output


def secure_wait():
    delay = 0.0
    n=8
    for i in range(n):
        delay += random()
    T = float(get_config().get("delay-between-fetches"))
    minT = 5.0
    delay = minT + delay *  (T-minT) * 2.0 / float(n)
    sleep(delay)

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
    channel_names_dict = {}
    playlist_names_dict = {}
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
    with edit_config() as cfg:
        cfg["channel_names_dict"].update(channel_names_dict)
        cfg["playlist_names_dict"].update(playlist_names_dict)

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
            with edit_config() as cfg:
                cfg["channel_names_dict"][channel_id] = channel_name
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
            with edit_config() as cfg:
                cfg["playlist_names_dict"][playlist_id] = playlist_name
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
            with edit_config() as cfg:
                cfg["rss_names_dict"][link] = source_name
            return source_name
        else:
            return ""
    except Exception as e:
        return ""

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
      try:
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
                    from ytrss_transcribe import get_engine_map
                    for engine, engine_name in get_engine_map().items():
                        transcribe_link += f"<a href='{url_link}/transcribe/{engine}/{fn}'>{engine_name}</a> <a>|</a> "
                    description_element.text += f"<br/> {transcribe_link}<br/>"
                    description_element.text += f"<br/> <a href='{url_link}/remove_transcription/{fn}'>Remove this transcription</a><br/>"
        elif auto_transcription:
            if age < timedelta(hours=get_config()["auto_transcript_hours"]+1):
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
        output_link.text = f"{url_link}/read/{fn}/full"
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
                    if ext != "raw":
                        url = f"{url_link}/file/{fn}.{ext}"
                        enclosure_element = etree.Element("enclosure", url=url, type=media_type, length=str(length))
                        output_item.append(enclosure_element)

        output += "\n    " + etree.tostring(output_item, encoding="utf-8", method="xml").decode("utf-8")
      except Exception as e:
          print(e)
          
    output += """
    </channel>
</rss>"""
    return output

def generate_transcriptions_list_page(url_link, search_request = None):
    from lxml import etree
    page_start = _read_page_start(get_config()["title"])
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
        if search_request is not None:
            description_element = entry.find("summary")
            if title_element is not None and search_request.lower() not in title_element.text.lower():
                if description_element is not None and (description_element.text is None or search_request.lower() not in description_element.text.lower()):
                    continue
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time

        auto_transcription = False
        auto_transcription_element = entry.find("auto_transcription")
        if auto_transcription_element is not None:
            auto_transcription = auto_transcription_element.text=="true"
        if not os.path.exists(transcription_path) and auto_transcription:
            if age < timedelta(hours=get_config()["auto_transcript_hours"]+1):
                continue # will wait for transcription and final deduplication

        news_url = f"{url_link.rstrip('/')}/read/{quote(fn, safe='')}"
        image_element = entry.find("image")
        image_url = (
            (image_element.get("href") or "").replace("__URL_LINK__", url_link)
            if image_element is not None else ""
        )
        image_html = ""
        if image_url:
            image_html = (
                f'<img class="news-image" src="{escape(image_url, quote=True)}"'
                ' alt="" loading="lazy" decoding="async">'
            )
        output = f"""<li class="news-card" id="{escape(fn, quote=True)}">
  <a class="news-link" href="{escape(news_url, quote=True)}">
    {image_html}
    <div class="news-content">
      <h2 class="news-title">{escape(title_element.text or "Без заголовка")}</h2>
      <time class="news-time" datetime="{modified_time.isoformat(timespec='seconds')}">
        {modified_time.strftime("%d.%m.%Y, %H:%M")}
      </time>
    </div>
  </a>
</li>"""

        pubs[age] = (fn, title_element.text, output, modified_time)

    output = ""
    for age, (fn, title, content, modified_time) in {k: v for k, v in sorted(pubs.items(), key=lambda item: item[0])}.items():
        output += content
    if search_request is None:
       search = (
           f'<form class="news-search" method="post" action="{escape(url_link, quote=True)}/read/search">'
           '<input type="search" name="search_request" placeholder="Search news…" aria-label="Search news">'
           '<button type="submit">Search</button></form>'
       )
    else:
       search = (
           f'<h2>{escape(search_request)}</h2>'
           f'<form class="news-search" method="get" action="{escape(url_link, quote=True)}/read">'
           '<button type="submit">Back to list</button></form>'
       )
    return (
        page_start
        + f'<h1>{escape(get_config()["title"])}</h1>'
        + search
        + f'<ul class="news-grid">{output}</ul>'
        + '</main></body></html>'
    )

def _render_read_article(url_link, fn, entry, content, full=False, share_html=""):
    site_title = get_config()["title"]
    title = entry.find("title").text or "Без заголовка"
    base_url = url_link.rstrip('/')
    read_url = f"{base_url}/read/{quote(fn, safe='')}"
    back_url = f"{base_url}/read#{quote(fn, safe='')}"
    alternate_label = "Summary" if full else "Full text"
    alternate_url = read_url if full else f"{read_url}/full"

    link_element = entry.find("link")
    listen_url = (
        (link_element.get("href") or "").replace("__URL_LINK__", url_link)
        if link_element is not None else ""
    )
    actions = (
        f'<a target="_blank" rel="noopener noreferrer" href="{escape(listen_url, quote=True)}">'
        'View the episode</a>'
    )
    enclosure_element = entry.find("enclosure")
    enclosure_url = (
        (enclosure_element.get("url") or "").replace("__URL_LINK__", url_link)
        if enclosure_element is not None else ""
    )
    if enclosure_url:
        actions += (
            f'<a target="_blank" rel="noopener noreferrer" href="{escape(enclosure_url, quote=True)}">'
            'enclosure</a>'
        )
    alternate_link = f'<a href="{escape(alternate_url, quote=True)}">{alternate_label}</a>'
    back_link = f'<a href="{escape(back_url, quote=True)}">Back to list</a>'
    actions += alternate_link + back_link

    image_element = entry.find("image")
    image_url = (
        (image_element.get("href") or "").replace("__URL_LINK__", url_link)
        if image_element is not None else ""
    )
    image_html = (
        f'<img class="reader-image" src="{escape(image_url, quote=True)}" alt="{escape(title, quote=True)}">'
        if image_url else ""
    )
    share_section = f'<div class="reader-share">{share_html}</div>' if share_html else ""
    footer = back_link if full else alternate_link + back_link
    return _read_page_start(f"{site_title} - {title}", "reader-page") + f"""
<article class="reader-card">
  <header class="reader-header">
    {image_html}
    <div class="reader-heading">
      <p class="reader-site">{escape(site_title)}</p>
      <h1 class="reader-title">{escape(title)}</h1>
    </div>
  </header>
  <nav class="reader-actions" aria-label="Article actions">{actions}</nav>
  {share_section}
  <div class="reader-text">{content}</div>
  <nav class="reader-footer" aria-label="Article navigation">{footer}</nav>
</article>
</main></body></html>"""


def generate_transcription_page(url_link, fn):
        from lxml import etree
        ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        MEDIA_NS = "http://search.yahoo.com/mrss/"
        etree.register_namespace("itunes", ITUNES_NS)
        etree.register_namespace("media", MEDIA_NS)

        transcription_path = "yt-video/"+fn+".txt"
        description_path = "yt-video/"+fn+".desc"
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)

        title_element = entry.find("title")
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        share_html = ""
        if get_config()["tinyurl_key"]:
            short_url = get_share_url(fn)
            if short_url:
                share_html = clp_copy_button(f"{title_element.text}:\n{short_url}")
            else:
                share_url = f"{url_link.rstrip('/')}/read/{quote(fn, safe='')}/share"
                share_html = f'<a href="{escape(share_url, quote=True)}">Get shareable link</a>'

        output = ""
        if os.path.exists(transcription_path):
            with open(transcription_path, "r") as stream:
                string_list = stream.read().split('\n')
            for line in string_list:
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        elif entry.find("summary") is not None and entry.find("summary").text is not None:
            for line in entry.find("summary").text.split('\n'):
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        return _render_read_article(url_link, fn, entry, output, share_html=share_html)

def generate_full_transcription_page(url_link, fn):
        from lxml import etree
        ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        MEDIA_NS = "http://search.yahoo.com/mrss/"
        etree.register_namespace("itunes", ITUNES_NS)
        etree.register_namespace("media", MEDIA_NS)

        srt_path = "yt-video/"+fn+".srt"
        description_path = "yt-video/"+fn+".desc"
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)

        title_element = entry.find("title")
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        output = ""
        if os.path.exists(srt_path):
            with open(srt_path, "r") as stream:
                string_list = filter_subs(stream.read())
            for chunk in string_list:
              for line in chunk.splitlines():
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        elif entry.find("summary") is not None and entry.find("summary").text is not None:
            for line in entry.find("summary").text.split('\n'):
                if line.strip() != "":
                    output += f"<p>{line}</p>"
        return _render_read_article(url_link, fn, entry, output, full=True)


def return_file(filename):
    return send_file("yt-video/"+filename)
