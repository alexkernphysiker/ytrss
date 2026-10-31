# podcast_search.py
import requests
from typing import List, Tuple

def search_podcast_itunes_api(query: str) -> List[Tuple[str, str, str]]:
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "entity": "podcast",
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("results", []):
            title = item.get("collectionName")
            feed_url = item.get("feedUrl")
            description = item.get("collectionExplicitness")
            link = item.get("collectionViewUrl")
            if title and feed_url:
                results.append((title, feed_url, description, link))
        return results
    except requests.exceptions.RequestException as e:
        print(f"iTunes API connection error: {e}")
        return []
    except ValueError:
        print("Could not parse the response (expected JSON).")
        return []

def search_rss_feeds(query: str) -> List[Tuple[str, str, str, str]]:
    url = "https://api.feedly.com/v3/search/feeds"

    params = {
        "query": query,
        "count": 20,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        results = []

        for item in response.json().get("results", []):
            title = item.get("title")
            feed_id = item.get("feedId", "")
            website = item.get("website", "")

            feed_url = (
                feed_id.removeprefix("feed/")
                if feed_id.startswith("feed/")
                else ""
            )

            if title and feed_url:
                results.append((
                    title,
                    feed_url,
                    "",        # The description may be absent.
                    website,
                ))

        return results

    except (requests.RequestException, ValueError) as e:
        print(f"RSS search error: {e}")
        return []

def search_youtube_channel_api(query: str, api_key: str = "YOUR_YOUTUBE_API_KEY") -> List[Tuple[str, str, str]]:
    """
    Find channel IDs using the official YouTube Data API v3.
    Requires a Google Cloud API key.
    """
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "type": "channel",
        "q": query,
        "key": api_key,
        "maxResults": 5
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("items", []):
            title = item.get("snippet", {}).get("channelTitle")
            channel_id = item.get("snippet", {}).get("channelId")
            description = item.get("snippet", {}).get("description", "")
            # Return the name and the plain channel ID.
            if title and channel_id:
                results.append((title, channel_id, description))
                
        return results
    except Exception as e:
        print(f"YouTube API error: {e}")
        return []

def search_youtube_playlist_api(query: str, api_key: str = "YOUR_YOUTUBE_API_KEY") -> List[Tuple[str, str, str]]:
    """
    Find playlist IDs using the official YouTube Data API v3.
    Requires a Google Cloud API key.
    """
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "type": "playlist",  # Search for playlists.
        "q": query,
        "key": api_key,
        "maxResults": 5
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("items", []):
            title = item.get("snippet", {}).get("title")
            playlist_id = item.get("id", {}).get("playlistId")
            channel_name = item.get("snippet", {}).get("channelTitle")
            description = item.get("snippet", {}).get("description", "")
            if title and playlist_id:
                results.append((title, playlist_id, f"Playlist in [{channel_name}]: {description}"))
                
        return results
    except Exception as e:
        print(f"YouTube API error: {e}")
        return []