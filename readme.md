# ytrss

ytrss builds a single RSS 2.0 podcast feed from YouTube channels, YouTube
playlists, and existing RSS/podcast feeds. It can download YouTube videos for
use as local enclosures, collect subtitles, transcribe or summarize episodes,
extract readable text from linked articles, and present saved text in a simple
web page.

The project consists of a small Flask web application plus two polling workers:

- `ytrss.py` manages subscriptions and serves the generated feed, downloads,
  and transcriptions.
- `ytrss_upd.py` fetches subscribed sources, downloads eligible YouTube videos,
  writes episode metadata, schedules transcription, and removes expired files.
- `ytrss_transcribe.py` processes automatic and manually requested
  transcription jobs.

## Requirements

- Python 3.12 or newer (the source uses Python 3.12 f-string syntax)
- `ffmpeg` and `ffprobe`
- `yt-dlp` available on `PATH`
- Python packages from `requirements.txt`
- Optional API credentials for YouTube search and AI transcription providers

On Debian or Ubuntu, the system tools can be installed with:

```sh
sudo apt install ffmpeg yt-dlp python3-venv
```

## Installation

Clone the repository, then run the following commands from its root directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p yt-video
.venv/bin/python -c 'from config import save_config; save_config()'
```

The last command creates `ytrss_config.json` with the defaults from
`config.py`. Both `ytrss_config.json` and the contents of `yt-video/` are local
runtime data and are ignored by Git.

## Configuration

Edit `ytrss_config.json` before starting the application. The web interface can
change subscriptions and a subset of operational settings; other settings must
be edited directly in the JSON file.

Important settings include:

| Setting | Default | Purpose |
| --- | --- | --- |
| `host`, `port` | `127.0.0.1`, `5000` | Address used by the Flask server. |
| `url_link` | `http://127.0.0.1:5000` | Public base URL embedded in feed and download links. |
| `title` | `YTRSS feed` | Feed and web-page title. |
| `deliver_days` | `3` | Maximum item age included in the generated feed and processed from sources. |
| `max_days` | `30` | Retention time for files under `yt-video/`. |
| `wait_for_download_hours` | `3` | Time to wait for a new YouTube video/subtitles before continuing without them. |
| `auto_transcript_hours` | `12` | Automatically transcribe items newer than this many hours; set to `0` to disable scheduling. |
| `auto_transcript_engine` | `srt` | Default engine for YouTube items. |
| `auto_transcript_engine_rss` | `gemini_s` | Default engine for RSS items. |
| `yt-dlp-enabled` | `true` | Globally allow YouTube interaction through `yt-dlp`. |
| `yt-dlp-formats` | several fallbacks | Ordered download format arguments passed to `yt-dlp`. |
| `yt-dlp-options` | empty | Extra `yt-dlp` arguments for YouTube downloads and subtitles. |
| `yt-dlp-options-rss-podcasts` | empty | Extra `yt-dlp` arguments used when downloading podcast audio. |
| `proxies-youtube`, `proxies-rss` | `{}` | Proxy dictionaries passed to `requests`. |
| `delay-between-fetches` | `1` | Delay in seconds between source requests. |
| `duplicate_detection_threshold` | `100` | Fuzzy-match threshold used before downloading new items. |
| `duplicate_detection_threshold_transcription` | `100` | Fuzzy-match threshold used after transcription. |
| `re-transcription` | `false` | Show manual transcription/removal links in feed entries. |

Subscriptions, disabled-source lists, cached source names, request headers,
language prompts, and model names are also stored in this file. See
`default_config()` in `config.py` for the complete schema and current defaults.

### API credentials and transcription engines

Credentials are read from `ytrss_config.json`:

- `google_search_api_key` enables YouTube channel and playlist search through
  YouTube Data API v3.
- `gemini_api_key` enables `gemini_s` and `gemini_t`.
- `openai_api_key` enables `openai_s` and `openai_t`.
- `claude_api_key` enables `claude_s` and `claude_t`.
- `srt` is always available and downloads YouTube subtitles without using an AI
  provider.

The `_s` variants request a summary and the `_t` variants request a fuller
transcription. Provider model names and multilingual prompts are configurable
in the same file. Leaving a provider key empty hides its engines from the web
interface and causes an automatic queue configured for that engine to be
skipped.

Engine capabilities currently differ:

- `srt` only downloads YouTube subtitles.
- Claude requires subtitles and therefore currently works for YouTube items
  that have subtitles.
- OpenAI can use subtitles, local YouTube media, or a podcast enclosure.
- Gemini can use subtitles, a YouTube URL, a podcast enclosure, or URL context
  for an article page.

The default RSS engine is `gemini_s`; configure `gemini_api_key`, select another
provider that can process podcast media, disable automatic transcription for
the relevant RSS sources, or set `auto_transcript_hours` to `0`.

## Running

Start the server and workers from the repository root:

```sh
./start.sh
```

The launcher uses `.venv/bin/python` when it exists, otherwise `python3`. It
starts the Flask server once, runs the feed updater every 5 minutes, and runs the
transcription worker every minute. Worker output is written to
`ytrss_upd.log` and `ytrss_transcribe.log`.

Open [http://127.0.0.1:5000/subscription](http://127.0.0.1:5000/subscription)
with the default configuration.

`start.sh` launches background processes but does not install a service or
provide process supervision. For a permanent deployment, run the three Python
programs under your preferred service manager and reproduce the polling
intervals there.

Each program can also be run once on its own:

```sh
.venv/bin/python ytrss.py             # web server (long-running)
.venv/bin/python ytrss_upd.py         # one update and cleanup pass
.venv/bin/python ytrss_transcribe.py  # drain the current transcription queues
```

## Web interface and endpoints

The subscription pages provide navigation between:

- **YT channels** (`/show_channel_list`): add/remove channels by ID and, when a
  Google API key is configured, search for channels.
- **YT playlists** (`/show_playlist_list`): add/remove playlists by ID and
  optionally search for them.
- **RSS** (`/show_rss_list`): search podcasts with the iTunes API, search feeds
  with Feedly, discover feeds from a site URL, and unsubscribe from feeds.
- **Auto-downloading** (`/auto_download`): enable or disable downloads per
  YouTube source and configure retention, delivery age, and pre-download
  duplicate detection.
- **Auto-transcription** (`/auto_transcription`): enable or disable processing
  per source, select YouTube and RSS engines, and configure age, subtitle wait,
  and post-transcription duplicate detection.

The main output endpoints are:

- `/feed` serves the combined RSS 2.0 feed (`application/rss+xml`).
- `/read` shows stored transcriptions or extracted episode text.
- `/file/<episode>.<extension>` serves a locally downloaded enclosure.
- `/transcribe/<engine>/<episode>` queues a manual transcription.
- `/remove_transcription/<episode>` removes saved transcription output.

Add `http://127.0.0.1:5000/feed` (or the corresponding configured public URL)
to a feed or podcast reader. The generated feed has been used with RSS Reader
Offline on Android, QuiteRSS on Linux, and the Feedbro browser extension.

## How items are processed

For YouTube subscriptions, the updater reads YouTube's channel/playlist Atom
feeds, ignores Shorts and active/upcoming live streams, optionally downloads
videos using the configured format fallbacks, and creates local enclosure URLs.

For RSS subscriptions, existing remote enclosures are preserved. Entries
without enclosures first use a sufficiently long feed description as saved
text. The updater also fetches the linked HTML page and saves its readable text
when it is longer and looks like a complete article. An image is taken from
encoded feed content when the feed has no podcast image.

Duplicate detection compares fuzzy title/description text before processing and
transcription text after processing, considering only items within
`deliver_days`. A post-transcription duplicate is marked in its metadata and is
omitted from both `/feed` and `/read`. Items configured for automatic
transcription are withheld from `/feed` until a text file is produced.

Episode metadata is stored as `yt-video/*.desc`; downloads, subtitles,
transcriptions, and error details use the same episode ID with other extensions.
YouTube IDs are derived from the video URL. RSS episode IDs are SHA-256 hashes
of the source feed URL plus the entry link and/or enclosure URL, keeping IDs
stable while avoiding collisions between feeds. Text files in the repository
root are transient queues: `transcription.txt` and `transcription_rss.txt` hold
automatic jobs, while `<engine>.txt` files hold manual jobs. The transcription
worker clears a queue before processing it, and removes temporary MP3 files
after a pass.

## Security notes

The web interface has no authentication and includes state-changing routes.
Keep the default loopback binding unless access is protected by a trusted
reverse proxy or another authentication layer. Treat `ytrss_config.json` as a
secret because it can contain API credentials. Extra `yt-dlp` option fields are
passed to shell commands, so only trusted administrators should edit the
configuration.
