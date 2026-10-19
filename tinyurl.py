import os
import requests
from config import get_config

def shorten_url(long_url: str) -> str:
    response = requests.post(
        "https://api.tinyurl.com/create",
        headers={
            "Authorization": f"Bearer {get_config().get('tinyurl_key', '')}",
            "Accept": "application/json",
        },
        json={
            "url": long_url,
            "domain": "tinyurl.com",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"]["tiny_url"]

def get_share_url(fn: str, force = False) -> str:
    config = get_config()
    url_path = "yt-video/"+fn + ".url"
    if os.path.exists(url_path):
        with open(url_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    if not force:
        return None
    short_url = None
    if config.get("tinyurl_key"):
        try:
            short_url = shorten_url(f"{config['url_link']}/read/{fn}")
            if short_url:
                with open(url_path, "w", encoding="utf-8") as f:
                    f.write(short_url)
        except Exception as e:
            print(f"Error shortening URL: {e}")
    return short_url