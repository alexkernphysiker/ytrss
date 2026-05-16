#!/bin/bash
python3 ytrss.py &
python3 ytrss_pub.py &
while [ 1 ]; do
	python3 ytrss_upd.py > ytrss_upd.log 2>&1
	sleep 10m
done &
while [ 1 ]; do
	if [ "$(cat transcription.txt)" != "" ]; then
		python3 ytrss_transcribe.py > ytrss_transcribe.log 2>&1
	fi
	sleep 1m
done &
