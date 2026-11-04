# ytrss

YTRSS is a self-hosted YouTube and RSS aggregator with a web interface. It
collects subscriptions into one RSS 2.0 feed, saves media and article text,
creates transcripts or summaries, and groups saved items by their content.

## Requirements

- Linux with Python 3.12 or newer.
- FFmpeg, including ffprobe, available on PATH.
- Python packages from requirements.txt.
- Internet access to fetch feeds, media and the embedding model.
- Optional API keys for YouTube search and AI transcription providers.

Topic discovery and classification run locally on the CPU. The embedding model
downloads about 1.1 GB of weights on first use; allow additional disk space for
Python dependencies and downloaded media. A GPU is not required.

## Installation

On Debian or Ubuntu, install the system dependencies:

    sudo apt-get update
    sudo apt-get install python3-venv ffmpeg

Check that python3 is version 3.12 or newer. Clone this repository and run the
following commands from its root directory:

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -c 'from config import save_config; save_config()'

This creates ytrss_config.json with the defaults from config.py. The file
contains local settings and may contain API keys; it is ignored by Git.
Downloaded content is stored in yt-video/, which is also ignored by Git.

Keep the virtual environment activated when running the application. This
makes its installed yt-dlp executable available to subprocesses.

## First run

Start the application from the repository root:

    . .venv/bin/activate
    python start.py

On first startup, Sentence Transformers downloads the multilingual embedding
model into the user account's Hugging Face cache, outside the checkout.
Subsequent starts reuse it. Startup can take longer while it is downloaded
or loaded.

Open [http://127.0.0.1:5000/config](http://127.0.0.1:5000/config). Use the
navigation buttons to add YouTube channels, playlists or RSS subscriptions.
YouTube channel and playlist search requires a Google API key; subscriptions
can also be added by their IDs.

The news list is available at [http://127.0.0.1:5000/read](http://127.0.0.1:5000/read).
Add [http://127.0.0.1:5000/feed](http://127.0.0.1:5000/feed) to an RSS or podcast
reader. Use the corresponding address if the host or port is changed.

The launcher runs the web server, feed updater and transcriber together.
Keep one launcher process running; do not start the three modules as separate
services. Stop it with Ctrl+C or SIGTERM. An active download or provider request
may delay shutdown until it completes.

For unattended use, configure a service manager to run start.py with the
checkout as its working directory and the virtual environment's bin directory
first on PATH.

## Configuration

Edit ytrss_config.json to configure the installation. Missing settings fall back
to config.py defaults. Subscriptions, download settings, transcription settings
and groups can also be managed through the web interface.

Set url_link to the base URL that feed readers and media links should use.
For local use, the default is http://127.0.0.1:5000. For access through a reverse
proxy, use the external HTTPS URL. For example:

    {
      "host": "127.0.0.1",
      "port": 5000,
      "url_link": "https://ytrss.example.com",
      "title": "My YTRSS feed"
    }

Main settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| host, port | 127.0.0.1, 5000 | Web server bind address. |
| url_link | http://127.0.0.1:5000 | Base URL used in feed and media links. |
| title | YTRSS feed | Feed and page title. |
| max_days | 30 | Retention period for downloaded files, in days. |
| deliver_days | 3 | Item age limit for feed processing and output, in days. |
| delay-between-fetches | 10 | Delay between source requests, in seconds; minimum 10. |
| auto_transcript_hours | 12 | Automatically process recent items; 0 disables scheduling. |
| auto_transcript_engine | srt | Default engine for YouTube items. |
| auto_transcript_engine_rss | gemini_s | Default engine for RSS items. |
| wait_for_download_hours | 3 | How long to wait for new YouTube media or subtitles. |
| yt-dlp-enabled | true | Enable YouTube interaction through yt-dlp. |
| yt-dlp-formats | format fallbacks | Ordered download format arguments. |
| topics | {} | Group names mapped to their centroid vectors. |
| grouping_stop_words | Multilingual string in config.py | Space-separated words excluded from automatic group names. |

See default_config() in config.py for the complete schema, including proxy
settings, provider models, prompts, duplicate-detection thresholds and
source-specific options.

### Transcription providers

Add API keys only for the features to enable:

| Key | Feature |
| --- | --- |
| google_search_api_key | YouTube channel and playlist search. |
| gemini_api_key | Gemini summaries and transcripts. |
| openai_api_key | OpenAI summaries and transcripts. |
| claude_api_key | Claude summaries and transcripts. |
| tinyurl_key | Short sharing links for saved text. |

The srt engine downloads YouTube subtitles without an AI provider key.
For provider engines, the _s suffix requests a summary and _t requests a fuller
transcription. Configure provider models and language prompts in the same file.

An empty provider key hides its engines in the web interface and skips automatic
jobs assigned to those engines. The default RSS engine is gemini_s: configure
Gemini, choose another suitable engine under Auto-transcription, or disable
automatic transcription for those sources.

Capabilities differ by engine:

- Claude uses available YouTube subtitles.
- OpenAI can use subtitles, downloaded YouTube media or podcast audio.
- Gemini can use subtitles, a YouTube URL, podcast audio or article URL context.

## Grouping

Groups are empty by default. Collect some items before configuring them.
Items need current saved embeddings to participate in automatic grouping.
To generate or refresh embeddings for existing items, run:

    . .venv/bin/activate
    python classify_news.py

The script saves vectors in the items' .desc metadata files and reuses valid
existing vectors. It supports --data-dir and --batch-size.

Open Grouping in the configuration navigation, after Auto-downloading:

1. Enter the desired number of groups, from 0 to 10.
2. Select auto-config to discover groups and replace the existing group list.
3. Edit a group name and select change name to rename it.

The number field starts with the current group count. A value of 0 clears all
groups. Auto-config may take a minute and replaces existing names as well as
centroids. Renaming preserves the centroid and group order. Empty, duplicate
and reserved unclassified names are rejected.

Discovery uses K-means on existing news vectors. Automatic names are extracted
phrases ranked by similarity to each group's center, so they may need editing.
Groups with insufficient naming text receive a Group N label. Discovery skips
duplicate items and missing, stale or incompatible embeddings. If the requested
number exceeds the number of distinct usable vectors, the configuration is
left unchanged.

The grouping_stop_words setting is a single string with words separated by
spaces. It defaults to the multilingual list in config.py. Matching ignores
case; an empty string disables this filter. Edit the string in
ytrss_config.json before the next auto-config run to change the words excluded
from candidate names. This affects automatic naming only; news vectors and the
clustering calculation do not use this setting.

Classification assigns an item to its nearest configured centroid. Every item
with a compatible vector belongs to one group when groups exist. Other items
display unclassified. The news-list buttons show only configured groups.

### Topic feeds and OPML

The combined RSS feed at /feed includes each item's group in a standard
category element. Group names are not added to the item description.

Import /feed/categorized as an OPML subscription list in an RSS reader to add
the configured groups as separate feeds. The list contains one folder named
after the configured feed title. Its entries point to /feed/category/0,
/feed/category/1 and so on. Each topic feed contains only that group's items,
with the same article identifiers, descriptions and media as the combined feed.
Topic feed titles use "configured feed title - group name" in both RSS and OPML.

IDs are zero-based positions in the topics dictionary. Renaming a group through
Grouping preserves its position and address. Reordering topics or running
auto-config can change which group a number represents. A number outside the
current group list returns HTTP 404. With no configured groups, the OPML
contains an empty folder and no topic subscriptions.

For example, with the default local address, import
http://127.0.0.1:5000/feed/categorized using the reader's OPML import option.
Ordinary OPML import adds the current list once. Following later additions or
removals automatically requires a reader that supports OPML subscriptions.

### Group representation and Python API

The topics dictionary maps displayed names to raw 768-coordinate centroid
arrays. Keep centroids at their original lengths; classification uses Euclidean
distance, rather than normalizing them for cosine comparison.

The multilingual encoder is
sentence-transformers/paraphrase-multilingual-mpnet-base-v2. Input is the title
plus the saved annotation, falling back to the feed description. Longer text is
split into overlapping 128-token windows. Window vectors are normalized,
averaged with weights for newly covered tokens, then normalized again. The
separate full-subtitle file is not used for embedding input.

Python callers can use:

    from group_news import find_groups
    from classify_news import get_news_category, save_news_vector

    groups = find_groups(5)
    empty_groups = find_groups(0)
    vector = save_news_vector("episode-id")
    categories = get_news_category("episode-id")

find_groups returns a dictionary suitable for topics and does not change the
configuration or compute missing news embeddings. get_news_category returns
[group_name] or [] using the saved vector. Renaming or replacing groups does
not require recalculating news vectors.

Embeddings include the encoder signature and an input-text hash. Preserve the
model revision and pooling method when reusing vectors and centroids.
Classification results are cached in process memory and invalidated when
metadata or group definitions change.

To download the model before starting the server:

    .venv/bin/python -c 'import classify_news'

After the complete model is cached, Hugging Face network access can be disabled:

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python start.py

## Reading and managing content

- /config opens subscription and processing settings.
- /read lists saved news with categories and text search.
- /read/category filters by a configured group.
- /read/<episode> shows saved annotation or article text.
- /read/<episode>/full shows complete subtitles when available.
- /feed serves the combined RSS 2.0 feed.

YouTube items can include local media and subtitles. RSS items retain remote
enclosures; the updater can also extract readable article text. Duplicate items
are omitted from the feed and news list.

Manual transcription controls are available when re-transcription is enabled.
Sharing links require tinyurl_key and an externally reachable url_link.

## Firefox extension

The companion [Firefox extension](https://addons.mozilla.org/firefox/addon/ytrss-rss-youtube/)
opens YTRSS search pages for the current website, YouTube channel or playlist.
Configure the installation's base URL in its options.

When using Firefox on another device, including Android, provide an address
reachable from that device; 127.0.0.1 refers to the device itself. Optional Basic
Auth credentials stored by the extension are not encrypted.

## Logs and troubleshooting

The launcher writes ytrss.log, ytrss_upd.log and ytrss_transcribe.log in its
working directory. Consult these for startup failures, failed downloads and
transcription errors.

- If media processing fails, verify that ffmpeg, ffprobe and yt-dlp are on PATH.
- If a provider engine is absent, check its API key and configured model.
- If RSS transcription is skipped, check the selected engine and its credentials.
- If auto-config reports insufficient embeddings, collect more distinct items
  or run classify_news.py before trying again.
- After changing Python dependencies or application code, restart the launcher.

Pending transcription queues are held in memory. Automatic jobs can be
rediscovered on later updates; manual jobs may need to be queued again after
a restart.

## Access control

The application has no built-in authentication and exposes settings that change
its state. Keep the default loopback binding for local use. For remote access,
put it behind an authenticated reverse proxy or another trusted access layer.
Keep API keys and ytrss_config.json private.
