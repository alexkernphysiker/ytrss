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
port=get_config()["port"]
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
    channels_list = get_config()["channel_subscriptions"]
    for channel_id in channels_list:
        channel_name = get_channel_name(channel_id)
        chanlist_str += "<li>" + channel_id + " - [" + channel_name + "] " + "<form action='/unsubscribe/channel/"+channel_id+"' method='post'><input type='submit' value='Unsubscribe'></form></li>"
    return "<ul>"+ chanlist_str + "</ul><form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/get_chan_info", methods=['POST'])
def get_chan_info():
    channel_id = request.form['channel_id']
    if channel_id:
        sources_list = get_config()["channel_subscriptions"]
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
    sources_list = get_config()["channel_subscriptions"]
    if channel_id not in sources_list:
        sources_list.append(channel_id)
        save_config()
    return f"Subscribed to channel {channel_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/unsubscribe/channel/<channel_id>", methods=['POST'])
def unsubscribe_channel(channel_id):
    sources_list = get_config()["channel_subscriptions"]
    if channel_id in sources_list:
        sources_list.remove(channel_id)
        save_config()
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
    playlists_list = get_config()["playlist_subscriptions"]
    for playlist_id in playlists_list:
        playlist_name = get_playlist_name(playlist_id)
        playlistlist_str += "<li>" + playlist_id + " - [" + playlist_name + "] " + "<form action='/unsubscribe/playlist/"+playlist_id+"' method='post'><input type='submit' value='Unsubscribe'></form></li>"
    return "<ul>"+ playlistlist_str + "</ul><form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/get_playlist_info", methods=['POST'])
def get_playlist_info():
    playlist_id = request.form['playlist_id']
    if playlist_id:
        playlists_list = get_config()["playlist_subscriptions"]
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
    playlists_list = get_config()["playlist_subscriptions"]
    if playlist_id not in playlists_list:
        playlists_list.append(playlist_id)
        save_config()
    return f"Subscribed to playlist {playlist_id}." + \
              "<form action='/subscribtion' method='get'><input type='submit' value='Back'></form>"

@app.route("/unsubscribe/playlist/<playlist_id>", methods=['POST'])
def unsubscribe_playlist(playlist_id):
    playlists_list = get_config()["playlist_subscriptions"]
    if playlist_id in playlists_list:
        playlists_list.remove(playlist_id)
        save_config()
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
@app.route("/feed")
def yt_feed():
    global url_link
    return generate_feed(url_link, False)
@app.route("/")
def index():
    global url_link
    return generate_feed(url_link, False)
@app.route("/file/<path:filename>.mp4")
def download(filename):
    return return_file(filename)

if __name__ == "__main__":
    app.run(host=host, port=port)
