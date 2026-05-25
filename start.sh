#!/bin/bash
python3 ytrss.py &
python3 ytrss_pub.py &
while [ 1 ]; do
	python3 ytrss_upd.py > ytrss_upd.log 2>&1
	sleep 10m
done &
while [ 1 ]; do
	# AI API keys are transferred as environment variables
	# export OPENAI_API_KEY="..."
	# export ANTHROPIC_API_KEY="..."
	# export GOOGLE_API_KEY="..."
	# . start.sh
	python3 ytrss_transcribe.py
	sleep 1m
done &
