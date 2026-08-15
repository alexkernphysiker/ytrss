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
    return {
            "gemini_s": "Summarize with Gemini", 
            #"openai_s": "Summarize with OpenAI", 
            "claude_s": "Summarize with Claude",
            "gemini_t": "Transcribe with Gemini", 
            #"openai_t": "Transcribe with OpenAI",
            "claude_t": "Transcribe with Claude",
            "srt":"Just download subtitles from YT",
            }

def download_subtitles(filename):
        description_path = "yt-video/" + filename + ".desc"
        link = get_video_link(description_path)
        srt_path = "yt-video/" + filename + ".srt"
        if os.path.exists(srt_path):
            print(f"Subtitles for video {filename} already exist, skipping download.")
            with open(srt_path, "r", encoding="utf-8") as f:
                return f.read()
        if link is None:
            return ""
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
        return ""

def save_subtitles(filename, text):
    description_path = "yt-video/" + filename + ".desc"
    srt_path = "yt-video/" + filename + ".srt"
    with open(srt_path, "+w") as file:
        file.write(text)
    modtime = os.path.getmtime(description_path)
    os.utime(srt_path, (modtime, modtime))

def filter_subs(long_text, max_length = 0):
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
                per_sentence = output.split(".\n")
                if len(per_sentence) > 1:
                    yield (".\n".join(per_sentence[:-1]) + ".\n").replace("? ", "?\n").replace("! ", "!\n").replace(">> ", "\n>> ")
                    output = per_sentence[-1]
                else:
                    yield output
                    output=""
        output += line.replace(". ", ".\n") + " "
    if output != "":
        yield output
def get_video_title_and_description(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title = entry.find("title").text
        description_element = entry.find("summary")
        descr = description_element.text if description_element.text is not None else ""
        return title,descr

def make_prompt(lang, summarize = False, title = "", description = ""):
            if summarize:
                if lang == "uk":
                    return "Це транскрипція відео. Напиши стислий переказ цієї розмови уточнюючи хто озвучив наведені твердження та на що послався. Уникай мовних помилок, росіянізмів та неправильного написання власних назв. " + \
                           (f"Назва відео: \"{title}\". " if title!="" else "") + \
                           (f"Опис відео: \"{description}\". " if description!="" else "")
                elif lang == "pl":
                    return "To jest transkrypcja filmu. Zrób proszę streszczenie owej rozmowy wyjaśniając skąd się bierzą podawane twierdzenia (kto mówi, na co się odwołuje). " + \
                           (f"Nazwa filmu: \"{title}\". " if title!="" else "") + \
                           (f"Opis filmu: \"{description}\". " if description!="" else "")
                else:
                    return "This is a video transcription. Please summarize it pointing who told the given statements and what sources they mentioned. " + \
                           (f"Video title: \"{title}\". " if title!="" else "") + \
                           (f"Video descriptions: \"{description}\". " if description!="" else "")
            else:
                if lang == "uk":
                    return "Будь ласка, зроби з цих субтитрів текстову транскрипцію з повною вичиткою тексту та логічним розбиттям на абзаци та розділи. Якщо можливо, також виділи репліки різних мовців. Уникай мовних помилок, росіянізмів та неправильного написання власних назв. " + \
                            (f"Назва відео: \"{title}\". " if title!="" else "") + \
                            (f"Опис відео: \"{description}\". " if description!="" else "")
                elif lang == "pl":
                    return "Proszę, zrób z tych napisów tekstową transkrypcję filmu z pełnym sprawdzeniem pisowni oraz rozbiciem na akapity oraz rozdziały. Jeśli to jest możliwe, poznacz interpunkcją słowa powiedzone przez róźnych mówców. " + \
                            (f"Nazwa filmu: \"{title}\". " if title!="" else "") + \
                            (f"Opis filmu: \"{description}\". " if description!="" else "")
                else:
                    return "Please make from these subtitles, a text transcription of the video with correction of language mistakes and splitting the text into paragraphs and chapters. " + \
                            (f"Video title: \"{title}\". " if title!="" else "") + \
                            (f"Video descriptions: \"{description}\". " if description!="" else "")


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

def get_enclosure_link(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        enclosure_element = entry.find("enclosure")
        if enclosure_element is not None:
            enclosure =  enclosure_element.get("url")
            if enclosure is not None:
                return enclosure
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
            link = link_element.get("href")
            if "youtube.com" in link:
                return link
    return None

def run_srt(filename):
    for chunk in filter_subs(download_subtitles(filename), max_length=0):
        return chunk

def run_openai(filename, summarize):
        from openai import OpenAI
        client = OpenAI()
        description_path = "yt-video/" + filename + ".desc"
        video_path = "yt-video/" + filename
        lang = detect_language(description_path) or "en"
        text = download_subtitles(filename)
        modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
        age = datetime.now() - modified_time

        if text=="":
            if age < timedelta(hours=get_config()["wait_for_subtitles_hours"]) and get_video_link(description_path)!="":
                return ""
            if os.path.exists(video_path):
                mp3_path = convert_video_to_audio(video_path)
            else:
                mp3_path = get_enclosure_link(filename)
            for chunk in split_mp3_file(mp3_path):
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
                text += ".\n"
            save_subtitles(filename=filename, text= "Transcribed from mp3 by OpenAI:\n"+ text)
        title, descr = get_video_title_and_description(filename)
        for chunk in filter_subs(text):
                response = client.responses.create(
                    model="gpt-5.6",
                    input= make_prompt(lang, summarize, title=title, description=descr) + ":\n\n" + text
                )
                text = ""
                for output_item in response.output:
                    for content_item in output_item.content:
                        text += content_item.text
        return text

def download_audio_file(url, filename):
    audio_file_path = "yt-video/" + filename + ".mp3"
    if os.path.exists(audio_file_path):
        print(f"Audio file {audio_file_path} already exists, skipping download.")
        return audio_file_path
    command = f"yt-dlp -x --audio-format mp3 -o '{audio_file_path}' {url.split('?')[0]}"
    subprocess.call(command, shell=True)
    if os.path.exists(audio_file_path):
        modTime = os.path.getmtime("yt-video/" + filename + ".desc")
        os.utime(audio_file_path, (modTime, modTime))
        return audio_file_path
    else:
        return None

def run_gemini(filename, summarize):
    gemini_model = "gemini-3.6-flash"
    from google import genai
    from google.genai import types
    client = genai.Client()
    description_path = "yt-video/" + filename + ".desc"
    lang = detect_language(description_path) or "en"
    srt = download_subtitles(filename)
    modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
    age = datetime.now() - modified_time
    title, description = get_video_title_and_description(filename)
    if srt == "":
        if lang == "uk":
            prompt = "Будь ласка, транскрибуй це відео."
        elif lang == "pl":
            prompt = "Proszę, przetranskrybuj ten film."
        else:        
            prompt = "Please transcribe the video."
        youtube_link = get_video_link(description_path)
        if youtube_link is None:
            audio_file_path = download_audio_file(get_enclosure_link(filename), filename)
            if audio_file_path is None or not os.path.exists(audio_file_path):
                return f"Audio file not found {audio_file_path}"    
            audio_file = client.files.upload(file=audio_file_path)
            try:
                response = client.models.generate_content(
                    model=gemini_model,
                    contents=[
                        audio_file, 
                        prompt
                    ]
                )
                srt = response.text
            except Exception as e:
                return f"Transcription error: {e}"
            finally:
                client.files.delete(name=audio_file.name)
        else:
            if age < timedelta(hours=get_config()["wait_for_subtitles_hours"]) and get_video_link(description_path)!="":
                return ""
            response = client.models.generate_content(
                model=gemini_model,
                contents=types.Content(
                    parts=[
                        types.Part(file_data=types.FileData(file_uri=youtube_link)),
                        types.Part(text=prompt)
                    ]
                )
            )
            srt = response.text 
        save_subtitles(filename=filename, text= "Transcribed from video link by Gemini:\n"+ srt)

    text = ""
    for chunk in filter_subs(srt):
            response = client.models.generate_content(
                model=gemini_model,
                contents=types.Content(
                    parts=[
                        types.Part(text=make_prompt(lang, summarize = summarize)),
                        types.Part(text="Title:\n" + title),
                        types.Part(text="Description:\n" + description),
                        types.Part(text="Subtitles:\n" + chunk),
                    ]
                )
            )
            text += response.text
    return text


def run_claude(filename, summarize=False):
    lang = detect_language("yt-video/" + filename + ".desc") or "en"
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Client()
    max_tokens = 100000
    description_path = "yt-video/" + filename + ".desc"
    modified_time = datetime.fromtimestamp(os.path.getmtime(description_path))
    age = datetime.now() - modified_time
    title, description = get_video_title_and_description(filename)
    output=""
    srt = download_subtitles(filename)
    if srt == "":
        return "Claude engine requires subtitles"
    for chunk_srt in filter_subs(srt):
        messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": make_prompt(lang, summarize=summarize)},
                        {"type": "text", "text": "Video title:\n"+title},
                        {"type": "text", "text": "Video description:\n"+description},
                        {"type": "text", "text": "Subtitles:\n"+chunk_srt},
                    ],
                }
        ]

        with client.messages.stream(
            model="claude-opus-5",
            max_tokens=max_tokens,
            messages=messages
        ) as stream:
            for text in stream.text_stream:
                output += text
    return output if output!="" else "Claude is able to work only with subtitles"

def transcribe_video(filename, engine):
    video_path = "yt-video/" + filename
    description_path = "yt-video/" + filename + ".desc"
    transcription_path = "yt-video/" + filename + ".txt"
    log_path = "yt-video/" + filename + ".log"

    if os.path.exists(transcription_path):
        print(f"Transcription for video {filename} already exists, skipping transcription.")
        return
    
    text = ""
    try:
        if engine == "openai_t":
            text = run_openai(filename, summarize=False)
        elif engine == "gemini_t":
            text = run_gemini(filename, summarize=False)
        elif engine == "claude_t":
            text = run_claude(filename, summarize=False)
        elif engine == "openai_s":
            text = run_openai(filename, summarize=True)
        elif engine == "gemini_s":
            text = run_gemini(filename, summarize=True)
        elif engine == "claude_s":
            text = run_claude(filename, summarize=True)
        elif engine == "srt":
            text = run_srt(filename)
        else:
            print(f"Unknown transcript engine: {engine}, skipping transcription.")

    except Exception as e:
        print(f"An error occurred during transcription of video {filename}: {str(e)}")
        with open(log_path, "w") as f:
            f.write(f"Transcription error: {str(e)}")
        modTime = os.path.getmtime(description_path)
        os.utime(log_path, (modTime, modTime))
        return

    if text is not None and text.strip() != "":
        with open(transcription_path, "w") as f:
            f.write(get_engine_map()[engine]+"\n")
            f.write(text)
        modTime = os.path.getmtime(description_path)
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
            transcription_path = "yt-video/" + fn + ".txt"
            if not os.path.exists(transcription_path):
                transcribe_video(fn, engine=engine)

    for mp3 in Path("yt-video").glob("*.mp3"):
        if mp3.is_file():
            print(f"Removing temporary mp3 file: {mp3}")
            mp3.unlink()
