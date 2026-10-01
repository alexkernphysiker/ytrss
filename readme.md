# ytrss

YTRSS is a self-hosted podcast and media archiver for YouTube channels,
playlists, and RSS feeds. It combines subscribed sources into one RSS 2.0 feed,
auto-downloads eligible video/audio items for local enclosure use, preserves
subtitles and article text, and can queue AI-based summarization or
transcription jobs for saved episodes.

The project currently consists of a small Flask web app plus two background
workers:

- `ytrss.py` manages subscriptions, source settings, and the generated feed,
  downloads, and transcription views.
- `ytrss_upd.py` polls subscribed sources, downloads eligible media, writes
  episode metadata, schedules transcription, and cleans up expired files.
- `ytrss_transcribe.py` processes automatic and manual transcription jobs from
  local queues and provider-specific engines.

## Firefox extension

[ytrss — RSS і YouTube on Mozilla Add-ons](https://addons.mozilla.org/firefox/addon/ytrss-rss-youtube/)
is the companion extension for desktop Firefox 140+ and Firefox for Android 142+.
Version 1.2.1 was submitted for public distribution on September 30, 2026 and is
awaiting Mozilla review; installation from this page becomes available after
approval.

Its button sends the current site's root URL, YouTube channel ID/name, or
playlist ID to the corresponding ytrss search page in a new tab. When no search
target is available, it opens `/config` instead.

Set the ytrss server address in the extension's options. The default is
`http://127.0.0.1`; for the server's default port, use `http://127.0.0.1:5000`.
On Android, use a server address reachable from the phone, since `127.0.0.1`
refers to the phone itself. Optional HTTP Basic credentials can be included in
the server URL; they are stored locally without encryption.

## Requirements

- Python 3.12 or newer (the source uses Python 3.12 f-string syntax)
- `ffmpeg` and `ffprobe`
- `yt-dlp` available on `PATH`
- Python packages from `requirements.txt`
- Optional API credentials for YouTube search and AI transcription providers

On Debian or Ubuntu, the system tools can be installed with:

```sh
pkexec /usr/bin/apt-get install ffmpeg yt-dlp python3-venv
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

All threads share one configuration store. `get_config()` returns a deep copy
for reading. Code that changes settings must use `with edit_config() as cfg:`;
the entire edit, including changes to nested lists and dictionaries, is protected
by a lock and saved by atomically replacing the JSON file. Failed edits leave
the previous settings intact. Keep these transactions short: perform network
requests and other slow work outside them. Direct JSON edits are reloaded on
the next access when the file changes; stop the application before editing it
manually to avoid racing an application write.

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
.venv/bin/python start.py
```

Use `python3 start.py` if the dependencies are installed for the system Python.
The launcher runs one process with three independent worker threads: the Flask
web server, feed updater, and transcriber. Both periodic workers run immediately
and wait 60 seconds after each completed or failed pass, matching the former
`start.sh`. HTTP requests can use additional server request threads. Worker
output is appended separately to `ytrss_upd.log` and `ytrss_transcribe.log`,
preserving previous passes and application runs. Before each pass, each log is
trimmed to its last 20,000 lines if necessary. Output added during a pass can
exceed this limit until the next pass starts; the worker and its synchronous
subprocesses are not writing during trimming. An empty pass keeps the existing
log, applying the same line limit at its start.
The transcriber's `subprocess.call` commands send both stdout and stderr to its
log. Commands whose captured output is parsed by the application keep their
existing output handling.

When launched with `start.py`, application and web-server messages are also
appended to `ytrss.log` in the working directory, while remaining visible in the
terminal. This includes stdout, stderr, HTTP request logs, Flask exception
tracebacks, and unhandled startup errors. Previous application runs are preserved.
At startup and before every updater or transcriber pass (including empty passes),
`ytrss.log` is trimmed to its last 20,000 lines if necessary. Between these checks
it can temporarily exceed the limit. Pending output is flushed before trimming,
and trimming shares the same lock as writes from all server threads. Updater and
transcriber output continues to use the two worker logs described above.

Open [http://127.0.0.1:5000/subscription](http://127.0.0.1:5000/subscription)
with the default configuration.

The launcher remains in the foreground. Ctrl+C or SIGTERM stops the web server,
interrupts interval waits, and joins both background workers after their current
passes finish. An active download or API call can delay shutdown until it returns
or times out. An exception in a periodic pass is logged; the next pass still runs.
For a permanent deployment, run this single launcher under a service manager.
Do not launch the three modules as separate services or run multiple application
processes: in-memory queues and configuration locks are shared only inside one
process. The `run_update()` and `run_transcription()` functions remain available
for calling individual passes within that process.

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
stable while avoiding collisions between feeds. Automatic YouTube jobs,
automatic RSS jobs, and manual jobs for each engine are held in separate
in-memory queues. Adding a pending job is atomic and suppresses duplicates
within that queue. The worker takes all relevant queues in one atomic batch,
then processes it without holding the queue lock. Jobs added during processing
remain for the next pass. Engine order, manual-before-automatic order, skipping
existing transcripts, and cleanup of temporary MP3 files are preserved. As
before, automatic jobs configured for an unavailable engine are consumed and
skipped; manual jobs for a disabled engine remain pending until it is enabled.

Queues are intentionally not persistent: stopping the process loses pending
jobs. Eligible automatic jobs are rediscovered on subsequent feed updates;
manual jobs must be scheduled again. Legacy `transcription.txt`,
`transcription_rss.txt`, and `<engine>.txt` queue files are no longer read or
written. Before switching an existing deployment, let the old workers finish
their queues and stop all three old processes. Saved media, metadata, subtitles,
and transcript files under `yt-video/` retain their existing format.

## Tests

Run the offline regression and concurrency tests with the dependencies installed:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary runtime directories, local HTTP requests, and fixture feeds;
they do not download real media or call paid transcription APIs.

## Security notes

The web interface has no authentication and includes state-changing routes.
Keep the default loopback binding unless access is protected by a trusted
reverse proxy or another authentication layer. Treat `ytrss_config.json` as a
secret because it can contain API credentials. Extra `yt-dlp` option fields are
passed to shell commands, so only trusted administrators should edit the
configuration.
