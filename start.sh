#!/bin/bash

# AI API keys are transferred as environment variables
# export OPENAI_API_KEY="..."
# export ANTHROPIC_API_KEY="..."
# export GOOGLE_API_KEY="..."
# . start.sh

python_bin="python3"
if [ -x ".venv/bin/python" ]; then
	python_bin=".venv/bin/python"
fi

"$python_bin" ytrss.py &
while [ 1 ]; do
	"$python_bin" ytrss_upd.py > ytrss_upd.log 2>&1
	sleep 10m
done &
while [ 1 ]; do
	"$python_bin" ytrss_transcribe.py > ytrss_transcribe.log 2>&1
	sleep 1m
done &
