# ytrss

ytrss is a lightweight utility for generating RSS feeds from YouTube channels and videos. It fetches YouTube content and exposes it in a simple RSS-compatible format for feed readers and automation workflows.

Additionally, ytrss supports video transcription, allowing users to obtain text transcripts of YouTube videos for accessibility, searchability, or further processing.

This utility uses yt-dlp to download videos from youtube and then uses them as enclosures for rss entries.


# How to start the web server

export OPENAI_API_KEY="..."

export ANTHROPIC_API_KEY="..."

export GOOGLE_API_KEY="..."

. start.sh

