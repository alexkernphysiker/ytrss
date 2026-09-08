import json
import os

def default_config():
    return {
        "host" : "127.0.0.1",
        "port" : 5000,
        "url_link" : "http://127.0.0.1:5000",
        "max_days" : 30,
        "auto_transcript_engine" : "srt",
        "auto_transcript_engine_rss": "gemini_s",
        "auto_transcript_hours" : 12,
        "wait_for_download_hours" : 3,
        "manual_transcript_days" : 7,
        "channel_subscriptions" : [],
        "playlist_subscriptions" : [],
        "rss_subscriptions": [],
        "sources_with_disabled_auto_transcription" : [],
        "sources_with_disabled_downloading" : [],
        "channel_names_dict": {},
        "playlist_names_dict": {},
        "rss_names_dict": {},
        "google_search_api_key": "",
        "gemini_api_key": "",
        "openai_api_key": "",
        "claude_api_key": "",
        "yt-dlp-enabled": True,
        "yt-dlp-options": "",
        "yt-dlp-options-rss-podcasts": "",
        "yt-dlp-formats": ["-S res:360", "-S res:240", "-S res:144", "-S res:480", "-x"],
        "yt-dlp-delay": 1,
        "proxies-youtube": {},
        "proxies-rss": {},
        "delay-between-fetches": 1,
        "re-transcription": False,
        "transcription-prompts": {
            "en": [
                "Please make from these subtitles, a text transcription of the video with correction of language mistakes and splitting the text into paragraphs and chapters. " + \
                    "At the beginning, write a summary of 2-3 sentences, who and what the conversation is about. ",
                "This is a video transcription. Please summarize it pointing who told the given statements and what sources they mentioned. ",
                ""
            ],
            "uk": [
                "Будь ласка, зроби з цих субтитрів текстову транскрипцію з повною вичиткою тексту та логічним розбиттям на абзаци та розділи. " + \
                    "Якщо можливо, також виділи репліки різних мовців. Уникай мовних помилок, росіянізмів та неправильного написання власних назв. " + \
                    "На початку напиши анотіцію від 2-3 речення, хто і про що говорять в цій розмові. ",
                "Це транскрипція відео. Напиши стислий переказ цієї розмови уточнюючи хто озвучив наведені твердження та на що послався. Уникай мовних помилок, росіянізмів та неправильного написання власних назв. ",
                ""
            ],
            "pl": [
                "Proszę, zrób z tych napisów tekstową transkrypcję filmu z pełnym sprawdzeniem pisowni oraz rozbiciem na akapity oraz rozdziały. " + \
                    "Jeśli to jest możliwe, poznacz interpunkcją słowa powiedzone przez róźnych mówców. " + \
                    "Na początku napisz streszczenie od 2-3 zdań, kto i o czym mówi w tej rozmowie. ",
                "To jest transkrypcja filmu. Zrób proszę streszczenie owej rozmowy wyjaśniając skąd się bierzą podawane twierdzenia (kto mówi, na co się odwołuje). " ,
                ""
            ]
        }
    }
config=default_config()

def get_config():
    global config
    config_file="ytrss_config.json"
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    return config

def save_config():
    config_file="ytrss_config.json"
    global config
    output = {}
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            output.update(json.load(f))
    output.update(config)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

