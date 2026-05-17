from email.mime import text
from http import client
from http import client
import os
import subprocess
import re
from pathlib import Path
from urllib import response
from lxml import etree
from utils import *
import json

def convert_video_to_audio(video_file_path):
    audio_file_path = video_file_path + ".mp3"
    if os.path.exists(audio_file_path):
        print(f"Audio file {audio_file_path} already exists, skipping conversion.")
        return
    command = "ffmpeg -i {} -vn -ar 44100 -ac 1 -b:a 48k {}".format(video_file_path, audio_file_path)
    subprocess.call(command, shell=True)
    if os.path.exists(audio_file_path):
        modTime = os.path.getmtime(video_file_path)
        os.utime(audio_file_path, (modTime, modTime))
        return audio_file_path
    else:
        return None

def split_mp3_file(mp3_file_path, chunk_length_s=1000):
    for file in sorted(Path("yt-video").glob("chunk.*.mp3")):
        if file.is_file():
            print(f"Removing old mp3 chunk file: {file}")
            file.unlink()
    command = "ffmpeg -i {} -f segment -segment_time {} -c copy yt-video/chunk.%05d.mp3".format(mp3_file_path, chunk_length_s)
    subprocess.call(command, shell=True)
    for file in sorted(Path("yt-video").glob("chunk.*.mp3")):
        if file.is_file():
            print(f"mp3 chunk file: {file}")
            yield(f"{file}")

def detect_language(description_path):
    if os.path.exists(description_path):
        file=open(description_path, "r", encoding="utf-8")
        text=file.read()
        if bool(re.search('[а-яА-ЯЇЄїєҐґ]', text)):
            return "uk"
        elif bool(re.search('[ąęłżĄĘŁŻ]', text)):
            return "pl"
        else:
            return ""
    else:
        return ""

def get_video_link(description_path):
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        link_element = entry.find("link")
        if link_element is not None:
            return link_element.get("href")
    return None

def run_openai(filename, mp3_list, lang=""):
        from openai import OpenAI
        client = OpenAI()
        text = ""
        #Stage 1: Transcribe audio in chunks and concatenate text
        for chunk in mp3_list:
                print(f"Transcribing chunk {chunk} for video {filename}...")
                audio_file= open(chunk, "rb")
                transcription = client.audio.transcriptions.create(
                    model="gpt-4o-transcribe-diarize",
                    file=audio_file,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                    language=lang
                )
                prev_speaker = None
                for segment in transcription.segments:
                    if segment.speaker != prev_speaker:
                        text += f"\n  - {segment.text}"
                        prev_speaker = segment.speaker
                    else:
                        text += " "+segment.text
                text += "\n"
        
        #Stage 2: Eliminate 'transcription noise' and correct text if language is Ukrainian
        if text is not None and text.strip() != "":
            if lang == "uk":
                    # Unfortunately, gpt-4o-transcribe-diarize model makes a lot of mistakes for Ukrainian language, so we will try to correct them
                    print(f"Correcting text in Ukrainian for video {filename}...")
                    response = client.responses.create(
                        model="gpt-5.2",
                        input="Прибери 'шум' транскрипції. Зроби **повну вичитку всього тексту**. Якщо в тексті є якісь слова чи фрази російською, переклади їх українською та заміни. Ось текст:\n\n"+text
                    )
                    text = response.output[0].content[0].text
            elif lang == "pl":
                    # For Polish language, we will also try to correct text, but with a different prompt
                    print(f"Correcting text in Polish for video {filename}...")
                    response = client.responses.create(
                        model="gpt-5.2",
                        input="Proszę usunąć 'szum' transkrypcji. Wykonaj **pełne sprawdzenie pisowni całego tekstu**. Oto tekst:\n\n"+text
                    )
                    text = response.output[0].content[0].text
            else:
                    print(f"Eliminating 'transcription noise' for video {filename}...")
                    response = client.responses.create(
                        model="gpt-5.2",
                        input="Please eliminate 'transcription noise' from the text below:\n\n"+text
                    )
                    text = response.output[0].content[0].text

        #Stage 3: Split text into chapters and add titles to them
        if text is not None and text.strip() != "":
                input="Split the text into the chapters and add titles to them. Preserve the text literally. "
                #annotation_length = len(text.strip()) // 20
                #if annotation_length >= 256:
                #    input += f"Add short annotation for the whole text at the beginning (up to {annotation_length} characters). "
                input += "The text is:\n\n" + text
                print(f"Splitting text into chapters for video {filename}...")
                response = client.responses.create(
                    model="gpt-5.2",
                    input=input
                )
                text = response.output[0].content[0].text
        return text

def run_gemini(filename, youtube_link, lang="en"):
    from google import genai
    from google.genai import types
    client = genai.Client()
    if lang == "uk":
        prompt = "Будь ласка, транскрибуй це відео."
    elif lang == "pl":
        prompt = "Proszę, przetranskrybuj ten film."
    else:        
        prompt = "Please transcribe the video."

    response = client.models.generate_content(
        model='gemini-3-flash-preview',
        contents=types.Content(
            parts=[
                types.Part(
                    file_data=types.FileData(file_uri=youtube_link)
                ),
                types.Part(text=prompt)
            ]
        )
    )
    return response.text

def run_claude(filename, link, lang="en"):
    #not working for now
    import anthropic
    client = anthropic.Anthropic()
    if lang == "uk":
        prompt = "Будь ласка, транскрибуй це відео."
    elif lang == "pl":
        prompt = "Proszę, przetranskrybuj to wideo."
    else:
        prompt = "Please transcribe this video."

    response = client.beta.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": link},
                ],
            }
        ],
        betas=["files-api-2025-04-14"],
    )
    return response.content[0].text

def transcribe_video(filename, engine):
    video_path = "yt-video/" + filename
    transcription_path = "yt-video/" + filename + ".txt"
    log_path = "yt-video/" + filename + ".log"
    description_path = "yt-video/" + filename + ".desc"

    if os.path.exists(transcription_path):
        print(f"Transcription for video {filename} already exists, skipping transcription.")
        return
    
    if engine == "openai":
        print(f"Transcribing video {filename} with OpenAI...")
    elif engine == "gemini":
        print(f"Transcribing video {filename} with Gemini...")
    elif engine == "claude":
        print(f"Transcribing video {filename} with Claude...")
    else:
        print(f"Unknown transcript engine: {engine}, skipping transcription.")

    text = ""
    try:
        if engine == "openai":
            if os.path.exists(video_path):
                text = run_openai(filename, list(split_mp3_file(convert_video_to_audio(video_path))), detect_language(description_path))
            else:
                print(f"Video file {video_path} does not exist, skipping transcription.")
                text = ""
        elif engine == "gemini":
            text = run_gemini(filename, get_video_link(description_path), detect_language(description_path))
        elif engine == "claude":
            text = run_claude(filename, get_video_link(description_path), detect_language(description_path))
        else:
            print(f"Unknown transcript engine: {engine}, skipping transcription.")

    except Exception as e:
        print(f"An error occurred during transcription of video {filename}: {str(e)}")
        with open(log_path, "w") as f:
            f.write(f"Transcription error: {str(e)}")
        modTime = os.path.getmtime(video_path)
        os.utime(log_path, (modTime, modTime))
        return

    if text is not None and text.strip() != "":
        with open(transcription_path, "w") as f:
            f.write(text)
        modTime = os.path.getmtime(video_path)
        os.utime(transcription_path, (modTime, modTime))
        print(f"Transcription for video {filename} completed")
    else:
        print(f"Transcription for video {filename} is empty, not creating transcription file.")

def get_engine_list():
    return ["gemini", "openai"]

if __name__ == "__main__":

    video_list_auto = load_source_list_from_file("transcription.txt")
    save_source_list_to_file("transcription.txt", [])
    
    video_list_map = {}
    for engine in get_engine_list():
        video_list_map[engine] = load_source_list_from_file(engine + ".txt")
        save_source_list_to_file(engine + ".txt", [])

    auto_engine = get_config()["auto_transcript_engine"]
    if auto_engine in get_engine_list():
        video_list_map[auto_engine] += video_list_auto
    else:
        print(f"Unknown auto transcript engine: {auto_engine}, skipping auto transcription.")

    for engine, video_list in video_list_map.items():
        for fn in video_list:
            file_path = "yt-video/" + fn
            description_path = "yt-video/" + fn + ".desc"
            transcription_path = "yt-video/" + fn + ".txt"
            if not os.path.exists(transcription_path) and os.path.exists(file_path):
                transcribe_video(fn, engine=engine)

    for mp3 in Path("yt-video").glob("*.mp3"):
        if mp3.is_file():
            print(f"Removing temporary mp3 file: {mp3}")
            mp3.unlink()
