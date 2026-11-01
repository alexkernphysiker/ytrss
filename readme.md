# ytrss

YTRSS is a self-hosted YouTube and RSS archiving pipeline that turns subscribed
channels, playlists, and podcast feeds into a single local RSS 2.0 catalog.
The current implementation combines a Flask web interface with periodic updater
and transcription workers to fetch eligible media, keep local copies with
metadata, preserve subtitles and readable article text, queue AI-powered
summaries or transcripts, and classify saved news into configurable topics.

The project now consists of a small Flask web app plus two background workers:

- `ytrss.py` manages subscriptions, source settings, and the generated feed,
  downloads, and transcription views.
- `ytrss_upd.py` polls subscribed sources, downloads eligible media, writes
  episode metadata, schedules transcription, and cleans up expired files.
- `ytrss_transcribe.py` processes automatic and manual transcription jobs from
  local queues and provider-specific engines.

The supporting `classify_news.py` module computes local multilingual embeddings
and resolves categories for the news list. See [Topic categories](#topic-categories)
for configuration and backfilling existing episodes.

## Firefox extension

[ytrss — RSS and YouTube on Mozilla Add-ons](https://addons.mozilla.org/firefox/addon/ytrss-rss-youtube/)
is the companion extension for desktop Firefox 140+ and Firefox for Android 142+.

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
sudo apt-get install ffmpeg yt-dlp python3-venv
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

## Minimal config example

If you want the smallest useful config, keep just the values you actually need
and leave the rest to the defaults from `default_config()` in `config.py`.
Set `url_link` to the public base URL that feed readers and enclosure links must
use. If the service is reachable only from your own machine, keep the default
`http://127.0.0.1:5000`; for a public deployment, replace it with your HTTPS URL.

```json
{
  "host": "127.0.0.1",
  "port": 5000,
  "url_link": "https://ytrss.example.com",
  "title": "YTRSS feed",
  "google_search_api_key": "",
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "openai_api_key": "YOUR_OPENAI_API_KEY",
  "claude_api_key": "YOUR_CLAUDE_API_KEY"
}
```

Leave a provider key empty if you do not want to enable that engine. Empty keys
hide the corresponding options from the web interface. After saving the file, run
`start.py` and then add subscriptions from the browser UI.

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
| `tinyurl_key` | empty | TinyURL API key used to create shareable links for transcription pages. |
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
| `delay-between-fetches` | `10` | Delay in seconds between source requests; values below 10 are clamped to 10. |
| `duplicate_detection_threshold` | `100` | Fuzzy-match threshold used before downloading new items. |
| `duplicate_detection_threshold_transcription` | `100` | Fuzzy-match threshold used after transcription. |
| `re-transcription` | `false` | Show manual transcription/removal links in feed entries. |
| `topics` | `{}` | Category names and multilingual prototype descriptions; see [Topic categories](#topic-categories). |

Subscriptions, disabled-source lists, cached source names, request headers,
language prompts, and model names are also stored in this file. See
`default_config()` in `config.py` for the complete schema and current defaults.

### API credentials and transcription engines

Credentials are read from `ytrss_config.json`:

- `google_search_api_key` enables YouTube channel and playlist search through
  YouTube Data API v3.
- `tinyurl_key` enables creation of shareable links for transcription pages
  through the TinyURL API. Leave it empty to disable this feature.
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

## Topic categories

News is classified locally with `intfloat/multilingual-e5-small` through
Sentence Transformers. Its multilingual embeddings allow texts in different
languages to share a category. Classification uses each episode's content;
there are no channel-specific category overrides or paid embedding API calls.

### Configuring topics

The `topics` object in `ytrss_config.json` maps category names to lists of
prototypes. Each prototype is a list of one or more descriptions of the same
subject, optionally in different languages. The default is `{}`, which
disables category assignment.

For example, merge a fragment like this into the existing configuration:

```json
{
  "category_threshold": 0.86,
  "max_news_categories": 3,
  "topics": {
    "culture": [
      [
        "Cinema, theatre, music, literature, fashion and design."
      ],
      [
        "Sports, competitions, football and athletes."
      ]
    ],
    "humanities": [
      [
        "History, linguistics, etymology and language learning."
      ]
    ],
    "IT": [
      [
        "The IT industry, computers, programming and mobile technology."
      ],
      [
        "Artificial intelligence, machine learning, drones and autonomous systems."
      ]
    ]
  }
}
```

The model embeds the descriptions, averages and normalizes the vectors within
each prototype, and compares the news vector with those prototypes using cosine
similarity. A category's score is its best prototype score. Categories with
scores strictly greater than `category_threshold` (default `0.86`) are ranked
in descending score order, and at most `max_news_categories` (default `3`)
are assigned to the item. Both settings are read from `ytrss_config.json`
on each lookup, so edits take effect without restarting or recomputing news
embeddings. Equal scores preserve configuration order. Names are display labels,
so describe all the subjects that belong together. For example, sport needs a
prototype under culture if it should share that category with cinema and music.

The comparison is strict: with the default threshold, a score of exactly
`0.86` does not qualify. An item can belong to up to three categories by default
or none. Missing, invalid, or incompatible embeddings, an empty topic
configuration, and scores below or equal to the threshold all produce an empty
category list. The reader labels these items
`unclassified` and adds a matching filter after all configured category buttons.
This is a built-in reader category; reserve the name `unclassified` rather than
adding it as a topic description.

These scores measure semantic similarity, not classification probabilities.
Changing topic names or descriptions updates classification on the next lookup
without recomputing news embeddings.

### Computing and storing embeddings

The input is the episode title, with a leading bracketed source name removed,
plus the saved annotation or transcript in `yt-video/<fn>.txt`. If that file is
missing, empty, or unreadable, the classifier uses the `<summary>` from
`yt-video/<fn>.desc`. It removes HTML markup and reads up to 50,000 characters
of the body. The encoder then uses only the first **512 tokens** of the combined
input, including its prefix and title; longer annotations are truncated.
The classifier does not read the separate full-subtitle `.srt` file.

Each news embedding contains 384 normalized floating-point coordinates and is
independent of the configured categories. It is stored in an `<embedding>`
element inside the episode's `.desc` XML, together with the model signature
and a hash of the input text. Metadata refreshes in the updater preserve that
element.

To populate or refresh embeddings for existing episodes, run from the repository
root:

```sh
.venv/bin/python classify_news.py
```

The script scans `yt-video/*.desc`, reuses embeddings whose model signature
and input hash still match, and writes updated embeddings atomically while
preserving other metadata and file modification times. The available options
are `--data-dir` and `--batch-size` (default: `8`):

```sh
.venv/bin/python classify_news.py --data-dir /path/to/yt-video --batch-size 8
```

The same module provides two functions. Pass an episode ID without a directory
or extension:

```python
from classify_news import get_news_category, save_news_vector

save_news_vector("episode-id")
categories = get_news_category("episode-id")
```

`save_news_vector(fn)` creates or refreshes the saved embedding and returns its
coordinates. `get_news_category(fn)` uses the existing embedding and returns a
list containing at most `max_news_categories` configured category names whose
scores exceed `category_threshold`, ordered from highest to lowest score.
The defaults are three categories and a threshold of `0.86`. It returns `[]`
when no category qualifies, metadata or a usable vector is unavailable, or
`topics` is empty. Category lookup does not compute a new news embedding.

After writing a nonempty annotation/transcript, `transcribe_video()` calls
`save_news_vector()` automatically. Episodes whose text already exists,
including articles extracted by the RSS updater, may need the backfill command
above. Manually editing a `.txt` file also requires calling
`save_news_vector(fn)` or rerunning the script to refresh its embedding.

Prototype vectors and category results are cached in memory. The result cache
holds up to 8,192 entries and checks XML file metadata, the topic configuration,
the similarity threshold, the category limit, and the encoder identity/signature
before reuse.
Returned lists are independent copies, so modifying one does not change cached
results. Files changed within the last second are not added to the result cache,
avoiding stale results after rapid writes with identical filesystem timestamps. These caches are rebuilt after a
process restart.

### Model download and runtime

The model is loaded once when `classify_news` is imported and shared by the
application workers. Inference runs on the CPU with one PyTorch thread; a GPU
is not required. The CPU PyTorch build, Sentence Transformers, and Transformers
are included in `requirements.txt` and must be installed in the interpreter
environment used by the service.

On first use, the library downloads the weights automatically. The weights
occupy about 471 MB, in addition to tokenizer/configuration files and the Python
dependencies. They normally live in `~/.cache/huggingface/hub/` for the account
running the application; on the current root-run service this is
`/root/.cache/huggingface/hub/`. Keep the model cache outside the repository.

Startup can take longer while the model is downloaded or initialized. Subsequent
runs reuse the cached files. To fill the cache before starting the application:

```sh
.venv/bin/python -c 'import classify_news'
```

Once all model files are cached, the application can run without Hugging Face
network access:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python start.py
```

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
- `/read` lists saved news with category badges, category buttons, and text search.
- `/read/category` accepts POST form field `category` and lists only that
  category, including items that also belong to other categories. Buttons above
  the search box use names from `topics`; `unclassified` is the last button and
  selects items with an empty category list. **All news** returns to the complete
  list. Each card shows every matching category, or `unclassified` when none
  match.
- `/read/search` accepts POST form field `search_request` and searches titles
  and descriptions.
- `/read/<episode>` shows the saved annotation/transcript or extracted text.
  The summary and full-text pages display category badges below the title,
  ordered by relevance, or `unclassified` when no category matches.
- `/read/<episode>/full` shows the full subtitle text when available.
- `/read_ext/<episode>/share` creates a TinyURL for an episode's transcription
  page and redirects back to that page.
- `/file/<episode>.<extension>` serves a locally downloaded enclosure.
- `/transcribe/<engine>/<episode>` queues a manual transcription.
- `/remove_transcription/<episode>` removes saved transcription output.

Add `http://127.0.0.1:5000/feed` (or the corresponding configured public URL)
to a feed or podcast reader. The generated feed has been used with RSS Reader
Offline on Android, QuiteRSS on Linux, and the Feedbro browser extension.

When `tinyurl_key` is configured, transcription pages show a **Share** button
if a short link already exists, or a **Get shareable link** link otherwise.
Creating a link uses the public `url_link` setting; the generated TinyURL is
saved alongside the episode under `yt-video/` and reused on later visits. The
Share button copies the episode title and short link to the clipboard.

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

All test suites, including the earlier tests, are stored outside the application
repository in `/root/ytrss-tests/` on the server. Run them from that directory:

```sh
cd /root/ytrss-tests
PYTHONDONTWRITEBYTECODE=1 /root/ytrss/.venv/bin/python -B run_tests.py
```

To run only the review regressions, add `--pattern test_review_fixes.py`.
Add `--summary` for compact JSON results. Category-related suites can be selected
with `--pattern test_topic_classification.py`, `--pattern test_category_cache.py`,
`--pattern test_news_categories.py`, `--pattern test_category_buttons.py`, or
`--pattern test_embedding_preservation.py`.

The runner uses disposable configuration and runtime directories, blocks network
connections and listening sockets, and skips tests that start an application or
HTTP server. It does not contact the running service, download real media, or
call paid transcription APIs. The runner enables offline model loading; the
service's Python dependencies and an already populated Hugging Face model cache
are required because the classifier initializes on import. Add future tests to
`/root/ytrss-tests/`.

## Security notes

The web interface has no authentication and includes state-changing routes.
Keep the default loopback binding unless access is protected by a trusted
reverse proxy or another authentication layer. Treat `ytrss_config.json` as a
secret because it can contain API credentials. Extra `yt-dlp` option fields are
parsed into individual subprocess arguments. Only trusted administrators should
edit them because downloader options can control external programs and local
file access.

Install the updated requirements to enable safe RSS HTML formatting with Bleach.
Without Bleach, untrusted HTML is escaped and displayed as plain text. Ordinary
transcript and subtitle text is always escaped.
