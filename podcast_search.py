# podcast_search.py
import requests
from typing import List, Tuple

def search_podcast_itunes_api(query: str) -> List[Tuple[str, str]]:
    """
    Шукає подкасти за назвою через Apple iTunes API.
    
    Args:
        query (str): Пошуковий запит (наприклад, назва подкасту).
        
    Returns:
        List[Tuple[str, str]]: Список знайдених результатів, 
                               де кожен елемент - це кортеж (назва_подкасту, URL_стрічки_RSS).
                               Якщо нічого не знайдено або сталася помилка, повертає порожній список.
    """
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "entity": "podcast",
        # Можна додати параметр "limit": 10, якщо хочете обмежити кількість результатів
    }
    
    try:
        # Встановлюємо таймаут, щоб скрипт не завис у разі проблем з мережею
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("results", []):
            title = item.get("collectionName")
            feed_url = item.get("feedUrl")
            
            # Додаємо лише ті результати, де є і назва, і посилання на RSS
            if title and feed_url:
                results.append((title, feed_url))
                
        return results
        
    except requests.exceptions.RequestException as e:
        print(f"Помилка з'єднання з iTunes API: {e}")
        return []
    except ValueError:
        print("Помилка обробки відповіді (очікувався JSON).")
        return []

def search_youtube_channel_api(query: str, api_key: str = "YOUR_YOUTUBE_API_KEY") -> List[Tuple[str, str]]:
    """
    Пошук ID каналів через офіційний YouTube Data API v3.
    Вимагає API-ключ від Google Cloud.
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
            
            # Повертаємо назву та чистий ID
            if title and channel_id:
                results.append((title, channel_id))
                
        return results
    except Exception as e:
        print(f"Помилка YouTube API: {e}")
        return []

def search_youtube_playlist_api(query: str, api_key: str = "YOUR_YOUTUBE_API_KEY") -> List[Tuple[str, str]]:
    """
    Пошук ID плейлістів через офіційний YouTube Data API v3.
    Вимагає API-ключ від Google Cloud.
    """
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "type": "playlist",  # Вказуємо, що шукаємо саме плейлісти
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
            # Для плейлістів ідентифікатор зберігається у блоці 'id' під ключем 'playlistId'
            playlist_id = item.get("id", {}).get("playlistId")
            
            if title and playlist_id:
                results.append((title, playlist_id))
                
        return results
    except Exception as e:
        print(f"Помилка YouTube API: {e}")
        return []