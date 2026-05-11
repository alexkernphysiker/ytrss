from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from xmlrpc import client
from flask import Flask
from flask import send_file
from flask import request
from utils import *
from lxml import etree

host=get_local_ip()
port=5000
url_link=f"http://{host}:{port}"

app = Flask(__name__)


@app.route("/subscribtion")
def subscribtion():
    return input_channel_id() + input_playlist_id()




##### channel subscriptions

@app.route("/input_channel_id")
def input_channel_id():
    return    "<a>Channel subscription by ID</a> <br/>" + \
              "<form action='/get_chan_info' method='post'><input type='text' name='channel_id'><input type='submit' value='View channel info'></form>" + \
              "<form action='/show_channel_list' method='get'><input type='submit' value='Show subscribed channels'></form>"

@app.route("/show_channel_list")
def show_channel_list():
    chanlist_str = ""
    channels_list = load_source_list_from_file("channels.txt")
    for channel_id in channels_list:
        sleep(0.3)  # To avoid hitting YouTube too hard when fetching channel names
        channel_name = get_channel_name(channel_id)
        chanlist_str += "<li>" + channel_id + " - [" + channel_name + "] " + "<form action='/unsubscribe/channel/"+channel_id+"' method='post'><input type='submit' value='Unsubscribe'></form></li>"
    return "<ul>"+ chanlist_str + "</ul><form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/get_chan_info", methods=['POST'])
def get_chan_info():
    channel_id = request.form['channel_id']
    if channel_id:
        sources_list = load_source_list_from_file("channels.txt")
        subscribed = channel_id in sources_list
        channel_name = get_channel_name(channel_id)
        if not subscribed:
            return f"Channel '{channel_name}' is not subscribed."+ \
                    "<form action='/subscribe/channel/"+channel_id+"' method='post'><input type='submit' value='Subscribe'></form>" + \
                    "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"
        else:
            return f"Channel '{channel_name}' is subscribed."+ \
                    "<form action='/unsubscribe/channel/"+channel_id+"' method='post'><input type='submit' value='Unsubscribe'></form>" + \
                    "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"
    else:
        return "No channel ID provided."

@app.route("/subscribe/channel/<channel_id>", methods=['POST'])
def subscribe_channel(channel_id):
    sources_list = load_source_list_from_file("channels.txt")
    if channel_id not in sources_list:
        sources_list.append(channel_id)
        save_source_list_to_file("channels.txt", sources_list)
    return f"Subscribed to channel {channel_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/unsubscribe/channel/<channel_id>", methods=['POST'])
def unsubscribe_channel(channel_id):
    sources_list = load_source_list_from_file("channels.txt")
    if channel_id in sources_list:
        sources_list.remove(channel_id)
        save_source_list_to_file("channels.txt", sources_list)
    return f"Unsubscribed from channel {channel_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"







#### playlist subscriptions

@app.route("/input_playlist_id")
def input_playlist_id():
    return    "<a>Playlist subscription by ID</a> <br/>" + \
              "<form action='/get_playlist_info' method='post'><input type='text' name='playlist_id'><input type='submit' value='View playlist info'></form>" + \
              "<form action='/show_playlist_list' method='get'><input type='submit' value='Show subscribed playlists'></form>"

@app.route("/show_playlist_list")
def show_playlist_list():
    playlistlist_str = ""
    playlists_list = load_source_list_from_file("playlists.txt")
    for playlist_id in playlists_list:
        sleep(0.3)  # To avoid hitting YouTube too hard when fetching playlist names
        playlist_name = get_playlist_name(playlist_id)
        playlistlist_str += "<li>" + playlist_id + " - [" + playlist_name + "] " + "<form action='/unsubscribe/playlist/"+playlist_id+"' method='post'><input type='submit' value='Unsubscribe'></form></li>"
    return "<ul>"+ playlistlist_str + "</ul><form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/get_playlist_info", methods=['POST'])
def get_playlist_info():
    playlist_id = request.form['playlist_id']
    if playlist_id:
        playlists_list = load_source_list_from_file("playlists.txt")
        subscribed = playlist_id in playlists_list
        playlist_name = get_playlist_name(playlist_id)
        if not subscribed:
            return f"Playlist '{playlist_name}' is not subscribed."+ \
                    "<form action='/subscribe/playlist/"+playlist_id+"' method='post'><input type='submit' value='Subscribe'></form>" + \
                    "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"
        else:
            return f"Playlist '{playlist_name}' is subscribed."+ \
                    "<form action='/unsubscribe/playlist/"+playlist_id+"' method='post'><input type='submit' value='Unsubscribe'></form>" + \
                    "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"
    else:
        return "No playlist ID provided."

@app.route("/subscribe/playlist/<playlist_id>", methods=['POST'])
def subscribe_playlist(playlist_id):
    playlists_list = load_source_list_from_file("playlists.txt")
    if playlist_id not in playlists_list:
        playlists_list.append(playlist_id)
        save_source_list_to_file("playlists.txt", playlists_list)
    return f"Subscribed to playlist {playlist_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/unsubscribe/playlist/<playlist_id>", methods=['POST'])
def unsubscribe_playlist(playlist_id):
    playlists_list = load_source_list_from_file("playlists.txt")
    if playlist_id in playlists_list:
        playlists_list.remove(playlist_id)
        save_source_list_to_file("playlists.txt", playlists_list)
    return f"Unsubscribed from playlist {playlist_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"











### video transcription

@app.route("/transcribe/<filename>")
def transcribe(filename):
    video_list = load_source_list_from_file("transcription.txt")
    if filename not in video_list:
        video_list.append(filename)
        save_source_list_to_file("transcription.txt", video_list)
    return f"Scheduled transcribing video {filename}."


### rss feed generation

@app.route("/")
def yt_feed():
    global url_link
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
            duration=int(duration_element.text)
        except:
            duration=10000
        
        enclosure_element = entry.find("enclosure")
        if enclosure_element is not None:
            enclosure_element.set("url", enclosure_element.get("url").replace("__URL_LINK__", url_link))

        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time
        duration_string = f"{duration//3600}:{(duration%3600)//60:02d}:{duration%60:02d}"

        if os.path.exists(transcription_path):
            string_list = open(transcription_path, "r").read().split('\n')
            descr = description_element.text if description_element.text is not None else ""
            description_element.text = f"<p>[VIDEO TRANSCRIPTION]</p> <br/><p>[{duration_string}]</p> <br/>"
            for line in string_list:
                description_element.text += "<p>"+line+"</p> <br/>"
            description_element.text += "<p>[VIDEO DESCRIPTION]</p> <br/>" + descr
            title_element.text = "transcribed: " + title_element.text
        elif duration < 9000 and age < timedelta(days=7):
            transcribe_link = f"<br/> <a href='{url_link}/transcribe/{fn}'>Transcribe this video</a> <br/>[Video description] <br/><p>[{duration_string}]</p> <br/>"
            if not description_element.text is None:
                description_element.text = transcribe_link + description_element.text
            else:
                description_element.text = transcribe_link
        else:
            description_element.text = f"[Video description] <br/> <p>[{duration_string}]</p> <br/>" + (description_element.text if description_element.text is not None else "")

        output += etree.tostring(entry, encoding="unicode") + "\n"
    output += "</feed>\n"
    return output

@app.route("/file/<path:filename>.mp4")
def download(filename):
    return send_file("yt-video/"+filename)

if __name__ == "__main__":
    app.run(host=host, port=port)