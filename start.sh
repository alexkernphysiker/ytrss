#!/bin/bash

python3 ytrss.py &
sleep 1m
python3 ytrss_upd.py &
while [ 1 ]; do
	python3 ytrss_transcribe.py
	sleep 2m
done &
