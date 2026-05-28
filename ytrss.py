from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from xmlrpc import client
from flask import Flask, url_for
from flask import send_file
from flask import request
from flask import redirect
from utils import *
from lxml import etree
from ytrss_transcribe import get_engine_map

 
host=get_local_ip()
port=get_config()["port"]
url_link=f"http://{host}:{port}"

app = Flask(__name__)


@app.route("/subscription")
def subscribtion():
    return "<form action='/show_channel_list' method='get'><input type='submit' value='Show subscribed channels'></form>" + \
           "<form action='/show_playlist_list' method='get'><input type='submit' value='Show subscribed playlists'></form>" + \
           "<form action='/auto-transcription/show' method='get'><input type='submit' value='Auto-transcription'></form>" + \
           "<form action='/downloading/show' method='get'><input type='submit' value='Auto-downloading'></form>"




##### channel subscriptions

@app.route("/input_channel_id")
def input_channel_id():
    return    "<a>Channel subscription by ID</a> <br/>" + \
              "<form action='/get_chan_info' method='post'><input type='text' name='channel_id'><input type='submit' value='View channel info'></form>"

@app.route("/show_channel_list")
def show_channel_list():
    chanlist_str = subscribtion() + input_channel_id() + "<a> Subscribed channels </a><br/>"
    for channel_id in get_config()["channel_subscriptions"]:
        chanlist_str += f"<li> <form action='/unsubscribe/channel/{channel_id}' method='post'>[{get_channel_name(channel_id)}] <input type='submit' value='Unsubscribe'></form></li>"
    chanlist_str += "<br/><a> Other known channels </a><br/>"
    for channel_id in get_config()["channel_names_dict"].keys():
        if  not channel_id in get_config()["channel_subscriptions"]:
                chanlist_str += f"<li> <form action='/subscribe/channel/{channel_id}' method='post'>[{get_channel_name(channel_id)}] <input type='submit' value='Subscribe'></form></li>"
    return f"<ul>{chanlist_str}</ul><br />" + subscribtion()

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
                    "<form action='/show_channel_list' method='get'><input type='submit' value='Back'></form>"
        else:
            return f"Channel '{channel_name}' is subscribed."+ \
                    "<form action='/unsubscribe/channel/"+channel_id+"' method='post'><input type='submit' value='Unsubscribe'></form>" + \
                    "<form action='/show_channel_list' method='get'><input type='submit' value='Back'></form>"
    else:
        return "No channel ID provided."

@app.route("/subscribe/channel/<channel_id>", methods=['POST'])
def subscribe_channel(channel_id):
    sources_list = get_config()["channel_subscriptions"]
    if channel_id not in sources_list:
        sources_list.append(channel_id)
        save_config()
    return redirect(url_for('show_channel_list'))

@app.route("/unsubscribe/channel/<channel_id>", methods=['POST'])
def unsubscribe_channel(channel_id):
    sources_list = get_config()["channel_subscriptions"]
    if channel_id in sources_list:
        sources_list.remove(channel_id)
        save_config()
    return redirect(url_for('show_channel_list'))



#### playlist subscriptions

@app.route("/input_playlist_id")
def input_playlist_id():
    return    "<a>Playlist subscription by ID</a> <br/>" + \
              "<form action='/get_playlist_info' method='post'><input type='text' name='playlist_id'><input type='submit' value='View playlist info'></form>"

@app.route("/show_playlist_list")
def show_playlist_list():
    playlistlist_str = subscribtion() + input_playlist_id() + "<a> Subscribed playlists </a><br/>"
    for playlist_id in get_config()["playlist_subscriptions"]:
        playlistlist_str += f"<li><form action='/unsubscribe/playlist/{playlist_id}' method='post'>[{get_playlist_name(playlist_id)}] <input type='submit' value='Unsubscribe'></form></li>"
    playlistlist_str += "<br/><a> Other known playlists </a><br/>"
    for playlist_id in get_config()["playlist_names_dict"].keys():
        if playlist_id in get_config()["playlist_subscriptions"]:
                continue
        playlistlist_str += f"<li><form action='/subscribe/playlist/{playlist_id}' method='post'>[{get_playlist_name(playlist_id)}] <input type='submit' value='Subscribe'></form></li>"
    return f"<ul>{playlistlist_str}</ul><br />" + subscribtion()

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
                    "<form action='/show_playlist_list' method='get'><input type='submit' value='Back'></form>"
        else:
            return f"Playlist '{playlist_name}' is subscribed."+ \
                    "<form action='/unsubscribe/playlist/"+playlist_id+"' method='post'><input type='submit' value='Unsubscribe'></form>" + \
                    "<form action='/show_playlist_list' method='get'><input type='submit' value='Back'></form>"
    else:
        return "No playlist ID provided."

@app.route("/subscribe/playlist/<playlist_id>", methods=['POST'])
def subscribe_playlist(playlist_id):
    playlists_list = get_config()["playlist_subscriptions"]
    if playlist_id not in playlists_list:
        playlists_list.append(playlist_id)
        save_config()
    return redirect(url_for('show_playlist_list'))

@app.route("/unsubscribe/playlist/<playlist_id>", methods=['POST'])
def unsubscribe_playlist(playlist_id):
    playlists_list = get_config()["playlist_subscriptions"]
    if playlist_id in playlists_list:
        playlists_list.remove(playlist_id)
        save_config()
    return redirect(url_for('show_playlist_list'))


#auto-downloading management for channels and playlists
@app.route("/downloading/show")
def show_downloading_status():
    downloading_str = subscribtion() + "<a> Subscribed channels and playlists with enabled auto-downloading </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id not in get_config()["sources_with_disabled_downloading"]:
            downloading_str+=f"<li><form action='/downloading/disable/{source_id}' method='post'>[{source_name}] <input type='submit' value='Disable'></form></li>"
    downloading_str += "<br/><a> Subscribed channels and playlists with disabled auto-downloading </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id in get_config()["sources_with_disabled_downloading"]:
             downloading_str+=f"<li><form action='/downloading/enable/{source_id}' method='post'>[{source_name}] <input type='submit' value='Enable'></form></li>"
    return f"<ul>{downloading_str}</ul><br />" + subscribtion()

@app.route("/downloading/disable/<source_id>", methods=['POST'])
def disable_downloading(source_id):
    sources_list = get_config()["sources_with_disabled_downloading"]
    if source_id not in sources_list:
        sources_list.append(source_id)
        save_config()
    return redirect(url_for('show_downloading_status'))

@app.route("/downloading/enable/<source_id>", methods=['POST'])
def enable_downloading(source_id):
    sources_list = get_config()["sources_with_disabled_downloading"]
    if source_id in sources_list:
        sources_list.remove(source_id)
        save_config()
    return redirect(url_for('show_downloading_status'))


#auto-transcription management for channels and playlists
@app.route("/auto-transcription/show")
def show_auto_transcription_status():
    auto_transcription_str = subscribtion() + "<a> Subscribed channels and playlists with enabled auto-transcription </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id not in get_config()["sources_with_disabled_auto_transcription"]:
            auto_transcription_str+=f"<li><form action='/auto-transcription/disable/{source_id}' method='post'>[{source_name}] <input type='submit' value='Disable'></form></li>"
    auto_transcription_str += "<br/><a> Subscribed channels and playlists with disabled auto-transcription </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id in get_config()["sources_with_disabled_auto_transcription"]:
             auto_transcription_str+=f"<li><form action='/auto-transcription/enable/{source_id}' method='post'>[{source_name}] <input type='submit' value='Enable'></form></li>"
    return f"<ul>{auto_transcription_str}</ul><br />" + subscribtion()

@app.route("/auto-transcription/disable/<source_id>", methods=['POST'])
def disable_auto_transcription(source_id):
    sources_list = get_config()["sources_with_disabled_auto_transcription"]
    if source_id not in sources_list:
        sources_list.append(source_id)
        save_config()
    return redirect(url_for('show_auto_transcription_status'))

@app.route("/auto-transcription/enable/<source_id>", methods=['POST'])
def enable_auto_transcription(source_id):
    sources_list = get_config()["sources_with_disabled_auto_transcription"]
    if source_id in sources_list:
        sources_list.remove(source_id)
        save_config()
    return redirect(url_for('show_auto_transcription_status'))


### video transcription
@app.route("/transcribe/<engine>/<filename>")
def transcribe(engine, filename):
    if engine not in get_engine_map().keys():
        return f"Unknown transcription engine {engine}"
    video_list = load_source_list_from_file(f"{engine}.txt")
    if filename not in video_list:
        video_list.append(filename)
        save_source_list_to_file(f"{engine}.txt", video_list)
        log_path = f"yt-video/{filename}.log"
        if os.path.exists(log_path):
            os.remove(log_path)
        transcription_path = f"yt-video/{filename}.txt"
        if os.path.exists(transcription_path):
            os.remove(transcription_path)
        return f"Scheduled {get_engine_map()[engine]} for video {filename}."
    else:
        return f"{get_engine_map()[engine]} for video {filename} was already scheduled."


@app.route("/remove_transcription/<filename>")
def remove_transcription(filename):
    transcription_path = f"yt-video/{filename}.txt"
    log_path = f"yt-video/{filename}.log"
    if os.path.exists(transcription_path):
        os.remove(transcription_path)
    if os.path.exists(log_path):
        os.remove(log_path)
    return f"Transcription for video {filename} has been removed."



### atom feed generation
@app.route("/feed")
def yt_feed():
    global url_link
    return generate_atom_feed(url_link, False)
@app.route("/")
def index():
    global url_link
    return generate_atom_feed(url_link, False)
@app.route("/file/<path:filename>.mp4")
def download(filename):
    return return_file(filename)

if __name__ == "__main__":
    app.run(host=host, port=port)
