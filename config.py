import json
import os

def default_config():
    return {
        "port" : 5000,
        "port_public" : 2000,
        "host_public" : "",
        "max_days" : 30,
        "recheck_size_days" : 7,
        "auto_transcript_engine" : "gemini",
        "auto_transcript_hours" : 10,
        "manual_transcript_days" : 7,
        "channel_subscriptions" : [],
        "playlist_subscriptions" : [],
        "channel_names_dict": {},
        "playlist_names_dict": {}
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

