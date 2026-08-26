import json
import os

def default_config():
    return {
        "max_days" : 30,
        "auto_transcript_engine" : "srt",
        "auto_transcript_hours" : 12,
        "wait_for_subtitles_hours" : 3,
        "manual_transcript_days" : 7,
        "channel_subscriptions" : [],
        "playlist_subscriptions" : [],
        "rss_subscriptions": [],
        "sources_with_disabled_auto_transcription" : [],
        "sources_with_disabled_downloading" : [],
        "channel_names_dict": {},
        "playlist_names_dict": {},
        "rss_names_dict": {},
        "enable_gemini": True,
        "enable_openai": True,
        "enable_claude": True
    }
config=default_config()

def get_config():
    global config
    config_file="ytrss_config.json"
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            config.update(json.load(f))
    return config

def save_config():
    config_file="ytrss_config.json"
    global config
    output = {}
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            output.update(json.load(f))
    output.update(config)
    with open(config_file, "w") as f:
        json.dump(output, f, indent=2)

