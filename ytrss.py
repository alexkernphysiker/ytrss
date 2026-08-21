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
port=5000
url_link=f"http://{host}:{port}"

app = Flask(__name__)


def buttons_on_top():
    return "<form action='/subscription' method='post'>" + \
           "<input type='submit' name='show_channel_list' value='YT channels'>" + \
           "<input type='submit' name='show_playlist_list' value='YT playlists'>" + \
           "<input type='submit' name='show_rss_list' value='RSS'>" + \
           "<input type='submit' name='auto_transcription' value='Auto-transcription'>" + \
           "<input type='submit' name='auto_download' value='Auto-downloading'>" + \
            "</form>"

@app.route("/subscription", methods=['GET','POST'])
def subscription():
    if request.method == 'POST':
        for action in request.form.keys():
            return redirect(url_for(action))
    else:
        return buttons_on_top()

##### channel subscriptions

@app.route("/show_channel_list")
def show_channel_list():
    chanlist_str = "<a> Subscribed channels </a><br/>"
    for channel_id in get_config()["channel_subscriptions"]:
        chanlist_str += f"<li> <form action='/unsubscribe/channel' method='post'>[{get_channel_name(channel_id)}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{channel_id}'><input type='submit' value='Unsubscribe'></form></li>"
    chanlist_str += "<a>Channel buttons_on_top by ID</a> <br/>" + \
              "<form action='/subscribe/channel' method='post'><input type='text' name='source_id'><input type='submit' value='Subscribe'></form>"
    chanlist_str += "<br/><a> Other known channels </a><br/>"
    for channel_id in get_config()["channel_names_dict"].keys():
        if  not channel_id in get_config()["channel_subscriptions"]:
                chanlist_str += f"<li> <form action='/subscribe/channel' method='post'>[{get_channel_name(channel_id)}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{channel_id}'><input type='submit' value='Subscribe'></form></li>"
    return buttons_on_top() + f"<ul>{chanlist_str}</ul>"

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

@app.route("/subscribe/channel", methods=['POST'])
def subscribe_channel():
    source_id = request.form['source_id']
    #if get_channel_name(source_id)=="":
    #    return buttons_on_top() + f"cannot obtain the channel {source_id}"
    sources_list = get_config()["channel_subscriptions"]
    if source_id not in sources_list:
        sources_list.append(source_id)
        save_config()
    return redirect(url_for('show_channel_list'))

@app.route("/unsubscribe/channel", methods=['POST'])
def unsubscribe_channel():
    source_id = request.form['source_id']
    sources_list = get_config()["channel_subscriptions"]
    if source_id in sources_list:
        sources_list.remove(source_id)
        save_config()
    return redirect(url_for('show_channel_list'))



#### playlist subscriptions
@app.route("/show_playlist_list")
def show_playlist_list():
    playlistlist_str = "<a> Subscribed playlists </a><br/>"
    for playlist_id in get_config()["playlist_subscriptions"]:
        playlistlist_str += f"<li><form action='/unsubscribe/playlist' method='post'>[{get_playlist_name(playlist_id)}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{playlist_id}'><input type='submit' value='Unsubscribe'></form></li>"
    playlistlist_str += "<a>Subscribe playlist by ID</a> <br/>" + \
              "<form action='/subscribe/playlist' method='post'><input type='text' name='source_id'><input type='submit' value='Subscribe'></form>"

    playlistlist_str += "<br/><a> Other known playlists </a><br/>"
    for playlist_id in get_config()["playlist_names_dict"].keys():
        if playlist_id in get_config()["playlist_subscriptions"]:
                continue
        playlistlist_str += f"<li><form action='/subscribe/playlist' method='post'>[{get_playlist_name(playlist_id)}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{playlist_id}'><input type='submit' value='Subscribe'></form></li>"
    return buttons_on_top() + f"<ul>{playlistlist_str}</ul><br />"

@app.route("/subscribe/playlist", methods=['POST'])
def subscribe_playlist():
    source_id = request.form['source_id']
    #if get_playlist_name(source_id)=="":
    #    return buttons_on_top() + f"cannot obtain the playlist {source_id}"
    playlists_list = get_config()["playlist_subscriptions"]
    if source_id not in playlists_list:
        playlists_list.append(source_id)
        save_config()
    return redirect(url_for('show_playlist_list'))

@app.route("/unsubscribe/playlist", methods=['POST'])
def unsubscribe_playlist():
    source_id = request.form['source_id']
    playlists_list = get_config()["playlist_subscriptions"]
    if source_id in playlists_list:
        playlists_list.remove(source_id)
        save_config()
    return redirect(url_for('show_playlist_list'))


#auto-downloading management for channels and playlists
@app.route("/auto_download")
def auto_download():
    downloading_str = "<a> Subscribed channels and playlists with enabled auto-downloading </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id not in get_config()["sources_with_disabled_downloading"]:
            downloading_str+=f"<li><form action='/downloading/disable' method='post'>[{source_name}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{source_id}'><input type='submit' value='Disable'></form></li>"
    downloading_str += "<br/><a> Subscribed channels and playlists with disabled auto-downloading </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id in get_config()["sources_with_disabled_downloading"]:
             downloading_str+=f"<li><form action='/downloading/enable' method='post'>[{source_name}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{source_id}'><input type='submit' value='Enable'></form></li>"
    return buttons_on_top() + f"<ul>" + \
           "<form action='/download-cfg' method='post'>" + \
           f"<label for='max_days'>Keep downloaded items (days):</label><input type='number' id='max_days' name='max_days' min='7' max='90' value='{get_config()["max_days"]}' /><br />" + \
            "<input type='submit' value='Save config'></form>" + \
           f"{downloading_str}</ul><br />"

@app.route("/downloading/disable", methods=['POST'])
def disable_downloading():
    source_id = request.form['source_id']
    sources_list = get_config()["sources_with_disabled_downloading"]
    if source_id not in sources_list:
        sources_list.append(source_id)
        save_config()
    return redirect(url_for('auto_download'))

@app.route("/downloading/enable", methods=['POST'])
def enable_downloading():
    source_id = request.form['source_id']
    sources_list = get_config()["sources_with_disabled_downloading"]
    if source_id in sources_list:
        sources_list.remove(source_id)
        save_config()
    return redirect(url_for('auto_download'))
@app.route("/download-cfg", methods=['POST'])
def download_cfg():
    cfg=get_config()
    cfg["max_days"] = int(request.form['max_days'])
    save_config()
    return redirect(url_for('auto_download'))


#auto-transcription management for channels and playlists
@app.route("/auto_transcription")
def auto_transcription():
    auto_transcription_str = "<a> Subscribed sources with enabled auto-transcription </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"] + get_config()["rss_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id) if source_id in get_config()["playlist_subscriptions"] else get_rss_name(source_id)
        if source_id not in get_config()["sources_with_disabled_auto_transcription"]:
            auto_transcription_str+=f"<li><form action='/auto-transcription/disable' method='post'>[{source_name}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{source_id}'> <input type='submit' value='Disable'></form></li>"
    auto_transcription_str += "<br/><a> Subscribed sources with disabled auto-transcription </a><br/>"
    for source_id in get_config()["channel_subscriptions"] + get_config()["playlist_subscriptions"] + get_config()["rss_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in get_config()["channel_subscriptions"] else get_playlist_name(source_id) if source_id in get_config()["playlist_subscriptions"] else get_rss_name(source_id)
        if source_id in get_config()["sources_with_disabled_auto_transcription"]:
             auto_transcription_str+=f"<li><form action='/auto-transcription/enable' method='post'>[{source_name}]<input type='hidden' name='source_id' class='form-control' id='source_id' value='{source_id}'> <input type='submit' value='Enable'></form></li>"
    engines_str = ""
    for engine,engine_text in get_engine_map().items():
        engines_str += f"<option value='{engine}' {"selected" if engine==get_config()["auto_transcript_engine"] else ""}>{engine_text}</option>"

    return buttons_on_top() + "<ul>" + \
           "<form action='/auto-transcription-cfg' method='post'>" + \
           f"<label for='default_engine'>default transcription engine:</label><select id='default_engine' name='default_engine'>{engines_str}</select><br />" + \
           f"<label for='auto_transcript_hours'>auto-transcript items not older than (Hr):</label><input type='number' id='auto_transcript_hours' name='auto_transcript_hours' min='3' max='24' value='{get_config()["auto_transcript_hours"]}' /><br />" + \
           f"<label for='manual_transcript_days'>show transcriptions not older than (days):</label><input type='number' id='manual_transcript_days' name='manual_transcript_days' min='1' max='{get_config()["max_days"]}' value='{get_config()["manual_transcript_days"]}' /><br />" + \
           f"<label for='wait_for_subtitles_hours'>wait for subtitles (Hr):</label><input type='number' id='wait_for_subtitles_hours' name='wait_for_subtitles_hours' min='0' max='6' value='{get_config()["wait_for_subtitles_hours"]}' /><br />" + \
            "<input type='submit' value='Save config'></form>" + \
           f"{auto_transcription_str}</ul><br />"

@app.route("/auto-transcription/disable", methods=['POST'])
def disable_auto_transcription():
    source_id = request.form['source_id']
    sources_list = get_config()["sources_with_disabled_auto_transcription"]
    if source_id not in sources_list:
        sources_list.append(source_id)
        save_config()
    return redirect(url_for('auto_transcription'))

@app.route("/auto-transcription/enable", methods=['POST'])
def enable_auto_transcription():
    source_id = request.form['source_id']
    sources_list = get_config()["sources_with_disabled_auto_transcription"]
    if source_id in sources_list:
        sources_list.remove(source_id)
        save_config()
    return redirect(url_for('auto_transcription'))

@app.route("/auto-transcription-cfg", methods=['POST'])
def auto_transcription_cfg():
    cfg=get_config()
    cfg["auto_transcript_engine"] = request.form['default_engine']
    cfg["auto_transcript_hours"] = int(request.form['auto_transcript_hours'])
    cfg["manual_transcript_days"] = int(request.form['manual_transcript_days'])
    cfg["wait_for_subtitles_hours"] = int(request.form['wait_for_subtitles_hours'])
    save_config()
    return redirect(url_for('auto_transcription'))


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



##### rss subscriptions
@app.route("/show_rss_list")
def show_rss_list():
    list_str = "<a> Subscribed RSS podcasts </a><br/>"
    for link in get_config()["rss_subscriptions"]:
        list_str += f"<li> <form action='/unsubscribe/rss' method='post'>[{get_rss_name(link)}]<input type='hidden' name='rss_link' class='form-control' id='rss_link' value='{link}'> <input type='submit' value='Unsubscribe'></form></li>"
    list_str +="<a>Input podcast RSS link</a> <br/>" + \
              "<form action='/subscribe/rss' method='post'><input type='text' name='rss_link' class='form-control' id='rss_link'><input type='submit' value='Subscribe'></form>"
    list_str += "<br/><a> Other known podcasts </a><br/>"
    for link in get_config()["rss_names_dict"].keys():
        if  not link in get_config()["rss_subscriptions"]:
                list_str += f"<li> <form action='/subscribe/rss' method='post'>[{get_rss_name(link)}]<input type='hidden' name='rss_link' class='form-control' id='rss_link' value='{link}'> <input type='submit' value='Subscribe'></form></li>"
    return buttons_on_top() + f"<ul>{list_str}</ul><br />"

@app.route("/subscribe/rss", methods=['POST'])
def subscribe_rss():
    link = request.form['rss_link']
    if get_rss_name(link)=="":
        return buttons_on_top() + f"cannot parse the rss {link}"
    sources_list = get_config()["rss_subscriptions"]
    if link not in sources_list:
        sources_list.append(link)
        save_config()
    return redirect(url_for('show_rss_list'))

@app.route("/unsubscribe/rss", methods=['POST'])
def unsubscribe_rss():
    link = request.form['rss_link']
    sources_list = get_config()["rss_subscriptions"]
    if link in sources_list:
        sources_list.remove(link)
        save_config()
    return redirect(url_for('show_rss_list'))


### atom feed generation
@app.route("/feed")
def yt_feed():
    global url_link
    return generate_atom_feed(url_link, False)

@app.route("/file/<path:filename>.mp4")
def download(filename):
    return return_file(filename)

@app.route("/read")
def read_transcriptions():
    global url_link
    return generate_transcriptions_page(url_link)

if __name__ == "__main__":
    app.run(host=host, port=port)
