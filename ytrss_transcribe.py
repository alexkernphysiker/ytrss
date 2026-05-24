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

def get_engine_map():
    return {"srt":"srt","gemini": "Gemini(link)", "openai": "OpenAI(mp3)", "claude": "Claude(srt)"}

def download_subtitles(link, filename):
        description_path = "yt-video/" + filename + ".desc"
        srt_path = "yt-video/" + filename + ".srt"
        if os.path.exists(srt_path):
            print(f"Subtitles for video {filename} already exist, skipping download.")
            with open(srt_path, "r", encoding="utf-8") as f:
                return f.read()
        lang = detect_language(description_path) or "en"
        proc = subprocess.run(f"yt-dlp --skip-download --write-auto-subs --write-subs --sub-lang {lang} {link}", shell=True, capture_output=True)
        for line in proc.stdout.decode().splitlines():
            if line.strip().startswith("[download] Destination: "):
                srtname = line.strip().split("[download] Destination: ")[-1]
                if os.path.exists(srtname):
                    with open(srtname, "r", encoding="utf-8") as f:
                        content = f.read()
                    os.rename(srtname, srt_path)
                    modtime = os.path.getmtime(description_path)
                    os.utime(srt_path, (modtime, modtime))
                    return content
        print(f"Failed to download subtitles for video {filename} with language {lang}. yt-dlp output: {proc.stderr.decode()}")
        if lang != "en":
            print(f"Retrying to download English subtitles for video {filename}...")
            proc = subprocess.run(f"yt-dlp --skip-download --write-auto-subs --write-subs --sub-lang en {link}", shell=True, capture_output=True)
            for line in proc.stdout.decode().splitlines():
                if line.strip().startswith("[download] Destination: "):
                    srtname = line.strip().split("[download] Destination: ")[-1]
                    if os.path.exists(srtname):
                        with open(srtname, "r", encoding="utf-8") as f:
                            content = f.read()
                        os.rename(srtname, srt_path)
                        modtime = os.path.getmtime(description_path)
                        os.utime(srt_path, (modtime, modtime))
                        return content
            print(f"Failed to download English subtitles for video {filename} as well. yt-dlp output: {proc.stderr.decode()}")
        return ""

def filter_subs(long_text, max_length = 30000):
    output=""
    prev_line=""
    for line in long_text.splitlines():
        if line.strip() == "":
            continue
        if "-->" in line and "align:" in line and "position:" in line:
            continue
        if "<c>" in line or "</c>" in line:
            continue
        if line.strip()==prev_line:
            continue

        prev_line = line.strip()
        if max_length > 0:
            if len(output) + len(line) + 1 > max_length:
                yield output
                output=""
        output += line + "\n"
    if output != "":
        yield output

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

def get_video_link(description_path):
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        link_element = entry.find("link")
        if link_element is not None:
            return link_element.get("href")
    return None

def run_srt(filename):
    description_path = "yt-video/" + filename + ".desc"
    youtube_link = get_video_link(description_path)
    output = ""
    for chunk in filter_subs(download_subtitles(youtube_link, filename), max_length=0):
        return chunk

def run_openai(filename):
        from openai import OpenAI
        client = OpenAI()
        description_path = "yt-video/" + filename + ".desc"
        text = ""
        video_path = "yt-video/" + filename
        lang = detect_language(description_path) or "en"
        #Stage 1: Transcribe audio in chunks and concatenate text
        for chunk in split_mp3_file(convert_video_to_audio(video_path)):
                print(f"Transcribing chunk {chunk} for video {filename}...")
                audio_file= open(chunk, "rb")
                sleep(3) # to avoid rate limits
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
def run_gemini(filename):
    youtube_link = get_video_link("yt-video/" + filename + ".desc")
    from google import genai
    from google.genai import types
    client = genai.Client()
    
    lang = detect_language("yt-video/" + filename + ".desc") or "en"
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

def run_claude(filename):
    youtube_link = get_video_link("yt-video/" + filename + ".desc")
    lang = detect_language("yt-video/" + filename + ".desc") or "en"
    srt = download_subtitles(youtube_link, filename)
    if srt.strip() == "":
        print(f"No subtitles found for video {filename}, skipping Claude transcription.")
        return f"No subtitles found for this video for language {lang}, so Claude transcription was skipped."
    import anthropic
    client = anthropic.Client()
    if lang == "uk":
        prompt = "Будь ласка, зроби з цих субтитрів текстову транскрипцію відео з повною вичиткою тексту"
    elif lang == "pl":
        prompt = "Proszę, zrób z tych napisów tekstową transkrypcję filmu"
    else:        
        prompt = "Please make from these subtitles a text transcription of the video"

    output=""
    for chunk_srt in filter_subs(srt):
        sleep(3) # to avoid rate limits
        response = client.beta.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": chunk_srt},
                    ],
                }
            ],
        )
        for item in response.content:
            if item.type == "text":
                output += item.text
    
    return output

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
    elif engine == "srt":
        print(f"Preparing filtered subtitles")
    else:
        print(f"Unknown transcript engine: {engine}, skipping transcription.")

    text = ""
    try:
        if engine == "openai":
            text = run_openai(filename)
        elif engine == "gemini":
            text = run_gemini(filename)
        elif engine == "claude":
            text = run_claude(filename)
        elif engine == "srt":
            text = run_srt(filename)
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


if __name__ == "__main__":

    video_list_auto = load_source_list_from_file("transcription.txt")
    save_source_list_to_file("transcription.txt", [])
    
    video_list_map = {}
    for engine, engine_name in get_engine_map().items():
        video_list_map[engine] = load_source_list_from_file(engine + ".txt")
        save_source_list_to_file(engine + ".txt", [])

    auto_engine = get_config()["auto_transcript_engine"]
    if auto_engine in get_engine_map().keys():
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
