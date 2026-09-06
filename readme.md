# ytrss

ytrss is a lightweight utility for generating RSS feeds from YouTube channels and videos. It fetches YouTube content and exposes it in a simple RSS-compatible format for feed readers and automation workflows.

Additionally, ytrss supports video transcription, allowing users to obtain text transcripts of YouTube videos for accessibility, searchability, or further processing.

This utility uses yt-dlp to download videos from youtube and then uses them as enclosures for rss entries.

On Android, the generated RSS feed was tested with the application 'RSS reader offline | Podcasts' https://play.google.com/store/apps/details?id=com.vanniktech.rssreader. That is the best RSS application for Android I've ever seen.

On desktop Linux, it was tested with  QuiteRSS and on firefox extension FeedBro


# Requirements

- Python 3
- `ffmpeg` (used to extract audio for transcription)
- API keys for any transcription or summarization providers you want to use

On Debian or Ubuntu, install `ffmpeg` and `yt-dlp` with:

```sh
sudo apt install ffmpeg yt-dlp
```

# Installation

From the project directory, create a virtual environment and install the Python dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

# Configuration

The application reads settings from `ytrss_config.json`. After the first run, the tool creates the file with default configuration that can be editted then either via tool's web-interface (some parameters) or manually in the file (all other parameters).

# Running

Start the web server and background workers from the project directory:

```sh
. start.sh
```

The web interface is available at [http://127.0.0.1:5000/subscription](http://127.0.0.1:5000/subscription). The launcher starts:

- the Flask web server (`ytrss.py`)
- the RSS update worker (`ytrss_upd.py`), which runs every 10 minutes
- the transcription worker (`ytrss_transcribe.py`), which runs every minute

# Web interfaces

The web application provides a simple HTML interface with navigation buttons at the top of each management page.

## Subscriptions

- **YT channels** (`/show_channel_list`) lists subscribed YouTube channels, allows channels to be added or removed by ID, and can search for channels when `google_search_api_key` is configured.
- **YT playlists** (`/show_playlist_list`) lists subscribed YouTube playlists and allows playlists to be added or removed by ID. Playlist search also uses the configured Google search API key.
- **RSS** (`/show_rss_list`) searches podcasts through the iTunes API and allows RSS podcast feeds to be subscribed or removed.

Changes made through these pages are saved to `ytrss_config.json`.

## Downloading and transcription

- **Auto-downloading** (`/auto_download`) enables or disables downloading for each subscribed YouTube channel or playlist and sets how many days downloaded items are kept.
- **Auto-transcription** (`/auto_transcription`) enables or disables automatic transcription for subscribed sources and selects separate transcription engines for YouTube and RSS items. It also controls transcription age limits and the wait time for YouTube subtitles.

The available transcription engines depend on the API keys configured (directly in `ytrss_config.json`). YouTube items can also expose links for manually scheduling transcription with a selected engine. A completed transcription can be removed through its **Remove this transcription** link. These links are enabled directly in `ytrss_config.json` as well.

## RSS feed and transcripts

- **RSS feed** (`/feed`) returns the generated Atom feed. Add this URL to an RSS or podcast reader.
- **Transcriptions** (`/read`) displays saved transcription text in a browser.
- Downloaded MP4 files are available through `/file/<filename>.mp4` when they exist (used for downloading the episodes' enclosures).
