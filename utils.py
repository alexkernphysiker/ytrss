import os
import subprocess
import re
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

def get_channel_name(channel_id):
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

