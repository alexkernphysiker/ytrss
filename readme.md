# ytrss

YTRSS is a self-hosted YouTube and RSS aggregator with a web interface. It
collects subscriptions into one RSS 2.0 feed, saves media and article text,
creates transcripts or summaries, and groups saved items by their content. For
Android, we recommend [RSS Reader Offline | Podcast](https://play.google.com/store/apps/details?id=com.vanniktech.rssreader)
because the YTRSS author tests this tool with that app.

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
reader running on the same machine as YTRSS.

For a phone or another device, use the reachable address of the YTRSS
installation, such as https://ytrss.example.com/feed. The address 127.0.0.1
refers to the device running the reader. Set url_link to the installation's
base URL and configure remote access as described in
[Configuration](#configuration) and [Access control](#access-control).

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
| duplicate_max_age_difference_days | 1 | Maximum absolute publication-time difference for duplicate candidates, in 24-hour days. |
| yt-dlp-enabled | true | Enable YouTube interaction through yt-dlp. |
| yt-dlp-formats | format fallbacks | Ordered download format arguments. |
| topics | {} | Automatic group phrases mapped to centroid vectors. |
| categories | {} | Category names mapped to lists of automatic group names. |
| grouping_stop_words | Multilingual string in config.py | Space-separated words excluded from automatic group names. |

See default_config() in config.py for the complete schema, including proxy
settings, provider models, prompts, duplicate-detection thresholds and
source-specific options.

Under Auto-transcription, **Maximum age difference for duplicates (days)** applies
to duplicate searches both during feed ingestion and after transcription. A pair
exactly at the configured limit remains eligible; pairs beyond it and items
without a usable publication date are skipped. Dates are compared in UTC, with
older offset-free dates interpreted as UTC. A value of 0 allows only identical
publication times. Existing duplicate markers are not recalculated automatically.

### Transcription providers

Add API keys only for the features to enable:

| Key | Feature |
| --- | --- |
| google_search_api_key | YouTube channel and playlist search. |
| thinking.gemini.api_key | Gemini summaries and transcripts. |
| thinking.openai.api_key | OpenAI summaries and transcripts. |
| thinking.claude.api_key | Claude summaries and transcripts. |
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

### Provider and thinking settings

The thinking dictionary in ytrss_config.json groups each provider's API key,
model names and reasoning settings. Its defaults are:

    {
      "thinking": {
        "gemini": {
          "api_key": "",
          "model": "gemini-3.8-flash",
          "thinking_level": "low"
        },
        "openai": {
          "api_key": "",
          "model": "gpt-5.6",
          "audio_model": "gpt-4o-transcribe-diarize",
          "effort": "none"
        },
        "claude": {
          "api_key": "",
          "model": "claude-opus-5",
          "type": "disabled",
          "effort": "low"
        }
      }
    }

Gemini uses model and thinking_level for text, YouTube, podcast audio and
article URL context. OpenAI uses model for text and audio_model for speech
recognition; effort applies only to text requests. Claude's type controls
thinking mode, while effort controls response effort.

Missing dictionaries and fields use their defaults. API keys default to empty,
which disables their providers. The old top-level provider keys and model names
are not used. JSON edits are read for subsequent jobs; an in-progress job keeps
its configuration snapshot. Accepted thinking values depend on the model.
After choosing another model, use thinking settings supported by that model.

## Grouping

The classification has two levels: automatically discovered groups and
administrator-defined categories. Both `topics` and `categories` are empty by
default. Readers see categories; automatic group names are used only in settings.

Items need current saved embeddings to participate in automatic grouping.
To generate or refresh embeddings for existing items, run:

    . .venv/bin/activate
    python classify_news.py

The script saves vectors in the items' .desc metadata files and reuses valid
existing vectors. It supports --data-dir and --batch-size.

Open Grouping in the configuration navigation, after Auto-downloading:

1. Enter the desired number of automatic groups, from 0 to 32, and select
   auto-config. The field starts with the current automatic group count.
2. Create categories in the left column. Each new category starts empty.
   Select a category to manage its groups, or use Rename and Delete beside it.
3. Select unassigned automatic groups in the right column and use Add selected.
   Assigned groups appear in the middle column; Remove selected releases them.

An automatic group can belong to only one category. Multiple automatic groups
can share a category. Removing a category releases all its groups. Category names
must be nonempty and unique; unclassified is reserved. Automatic group names
cannot be edited.

Every auto-config replaces the automatic groups and clears all category
memberships, even when some generated names remain the same. Category names
and their order are preserved. A group count of 0 clears all automatic groups
and memberships. Processing may take some time.

Discovery uses K-means on existing news vectors. Automatic names are extracted
phrases ranked by similarity to each group's center. Groups with insufficient
naming text receive a Group N label. Discovery skips duplicate items and
missing, stale or incompatible embeddings. If the requested number exceeds the
number of distinct usable vectors, the configuration is left unchanged.

The grouping_stop_words setting is a single string with words separated by
spaces. It defaults to the multilingual list in config.py. Matching ignores
case; an empty string disables this filter. Edit the string in
ytrss_config.json before the next auto-config run to change the words excluded
from candidate names. This affects automatic naming only; news vectors and the
clustering calculation do not use this setting.

Classification first finds the nearest automatic group, then looks up its
category. If that group has no category, the item is unclassified. It does not
fall back to another group. Items without a usable vector are also displayed
as unclassified. Navigation shows administrator-defined categories followed by
an unclassified button for all items without an assigned category.

### Category feeds and OPML

The combined RSS feed at /feed includes each item's category in a standard
category element, using unclassified for items without an assigned category.
Category names are not added to the item description.

Select Save OPML on the Grouping page to download ytrss.opml and import it into
an RSS reader. Alternatively, import /feed/categorized as an OPML subscription
list by URL to add the configured categories and unclassified as separate feeds.
The list contains one folder named after the configured feed title. Configured
category entries point to /feed/category/0, /feed/category/1 and so on, followed
by the built-in /feed/category/unclassified feed. Each feed contains only that
category's items, with the same article identifiers, descriptions and media as
the combined feed. Category feed titles use "configured feed title - category name"
in both RSS and OPML. The unclassified feed includes items without a usable
vector and items whose nearest automatic group has no assigned category. Its
address is stable when administrator-defined categories change.

IDs are zero-based positions in the categories dictionary. Renaming a category
through Grouping preserves its position and address. Deleting or reordering
categories can change which category a number represents. Auto-config preserves
category IDs but leaves their feeds empty until groups are assigned again.
A number outside the current category list returns HTTP 404. With no configured
categories, the OPML still contains the unclassified subscription. Unclassified
is built in and does not need an entry in the categories configuration.

For example, with the default local address, import
http://127.0.0.1:5000/feed/categorized using the reader's OPML import option.
Ordinary OPML import adds the current list once. Following later additions or
removals automatically requires a reader that supports OPML subscriptions.

### Group representation and Python API

The topics dictionary maps generated group phrases to raw 768-coordinate
centroid arrays. Keep centroids at their original lengths; classification uses
Euclidean distance, rather than normalizing them for cosine comparison.

The categories dictionary maps category names to lists of keys from topics.
For example, after discovery creates groups named "Space exploration" and
"Medical research", both can be assigned to one category:

    "categories": {
      "Science": ["Space exploration", "Medical research"],
      "Culture": []
    }

Every referenced group must exist in topics and can occur only once across
all category lists.

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

find_groups accepts a count from 0 to 32 and returns a dictionary suitable for
topics. It does not change the configuration or compute missing news embeddings.
get_news_category returns [category_name], or ["unclassified"] when the nearest
group is unassigned. It returns [] for missing or incompatible vectors, or
when no automatic groups exist. Reading pages and RSS display those items as
unclassified. Changing groups, categories or memberships does not require
recalculating news vectors.

Embeddings include the encoder signature and an input-text hash. Preserve the
model revision and pooling method when reusing vectors and centroids.
Classification results are cached in process memory and invalidated when
metadata, group definitions or category memberships change.

To download the model before starting the server:

    .venv/bin/python -c 'import classify_news'

After the complete model is cached, Hugging Face network access can be disabled:

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python start.py

## Reading and managing content

- /config opens subscription and processing settings.
- /read lists saved news with categories and text search.
- /read/category filters by an administrator-defined category or unclassified.
- /read/<episode> shows saved annotation or article text, with its category
  and automatic group below the title.
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

Transcription diagnostics also go to each material's yt-video/<id>.log. Entries
include the processing stage, provider/model, response completion status,
available token counters, request time and output length. Audio progress records
each part's number, duration and time range. OpenAI and Gemini use the same
audio splitter, with a target maximum of 1000 seconds per part (local files are
balanced into similar lengths). Gemini uploads and transcribes each podcast
audio part separately and deletes its provider-side upload afterwards. Results
are joined in source order; formatting and summarization start only after all
parts succeed. Temporary local segments are removed on success or failure.
Gemini's direct YouTube URL requests still use one part.
An empty response for an audio part of at least 30 seconds emits
audio.part.suspicious_empty. If duration is unavailable, the log records that
uncertainty instead. Empty or abnormally terminated responses are rejected
before a final result is published. A normal STOP is a technical completion
signal and does not prove that every spoken word was transcribed. Prompts,
transcript content and API keys are not included in these diagnostic entries.

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

