#!/bin/bash

#Public interface is read only: you cannot change subscriptions or transcribe videos from it, but you can watch the feed and transcribed videos.
#You should give public IP address as an argument to the script, otherwise public interface will not run
python3 ytrss_pub.py $@ &

#All other components
python3 ytrss.py &
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
