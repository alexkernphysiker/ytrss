import os
import subprocess
import re
from pathlib import Path
from openai import OpenAI
from lxml import etree
from utils import *

def convert_video_to_audio(video_file_path, audio_file_path):
    if os.path.exists(audio_file_path):
        print(f"Audio file {audio_file_path} already exists, skipping conversion.")
        return
    command = "ffmpeg -i {} -vn -ar 44100 -ac 1 -b:a 48k {}".format(video_file_path, audio_file_path)
    subprocess.call(command, shell=True)

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

def has_cyrillic(text):
    return bool(re.search('[а-яА-Я]', text))

def transcribe_video(filename):
    video_path = "yt-video/" + filename
    modTime = os.path.getmtime(video_path)
    transcription_path = "yt-video/" + filename + ".txt"
    description_path = "yt-video/" + filename + ".desc"
    audio_path = "yt-video/" + filename + ".mp3"
    
    if os.path.exists(transcription_path):
        print(f"Transcription for video {filename} already exists, skipping transcription.")
        os.utime(transcription_path, (modTime, modTime))
        return
    
    try:
        print(f"Transcribing video {filename}...")
        convert_video_to_audio(video_path, audio_path)
        os.utime(audio_path, (modTime, modTime))
        print(f"Audio file for video {filename} created, starting transcription...")
        if not os.path.exists(audio_path):
            print(f"Audio file {audio_path} does not exist, cannot transcribe video {filename}.")
            return

        lang=""
        if os.path.exists(description_path):
            description = open(description_path, "r").read()
            if has_cyrillic(description):
                #The only language with cyrillic I use is Ukrainian
                #I will set the language explicitly to decrease the number of transcription errors
                lang="uk"
        print(f"Language for video {filename}: {lang}")
        text = ""
        client = OpenAI()

        #Stage 1: Transcribe audio in chunks and concatenate text
        for chunk in split_mp3_file(audio_path):
            try:
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
            except Exception as e:
                print(f"An error occurred during transcription of chunk {chunk} for video {filename}: {str(e)}")
                text += f"\n[An error occurred during transcription of chunk {chunk}: {str(e)}]\n"
        
        #Stage 2: Eliminate 'transcription noise' and correct text if language is Ukrainian
        if lang == "uk" and text.strip() != "":
            # Unfortunately, gpt-4o-transcribe-diarize model makes a lot of mistakes for Ukrainian language, so we will try to correct them
            try:
                print(f"Correcting text in Ukrainian for video {filename}...")
                response = client.responses.create(
                    model="gpt-5.2",
                    input="Прибери 'шум' транскрипції. Зроби **повну вичитку всього тексту**. Якщо в тексті є якісь слова чи фрази російською, переклади їх українською та заміни. Ось текст:\n\n"+text
                )
                text = response.output[0].content[0].text
            except Exception as e:
                print(f"An error occurred during text correcting for video {filename}: {str(e)}")
                text += f"\n[An error occurred during text correcting: {str(e)}]\n"
        else:
            try:
                print(f"Eliminating 'transcription noise' for video {filename}...")
                response = client.responses.create(
                    model="gpt-5.2",
                    input="Please eliminate 'transcription noise' from the text below:\n\n"+text
                )
                text = response.output[0].content[0].text
            except Exception as e:
                print(f"An error occurred during text correcting for video {filename}: {str(e)}")
                text += f"\n[An error occurred during text correcting: {str(e)}]\n"

        #Stage 3: Split text into chapters and add titles to them
        if text.strip() != "":
            try:
                input="Split the text into the chapters and add titles to them. Preserve the text literally. "
                annotation_length = len(text.strip()) // 20
                if annotation_length >= 256:
                    input += f"Add short annotation for the whole text at the beginning (up to {annotation_length} characters). "
                input += "The text is:\n\n" + text
                print(f"Splitting text into chapters for video {filename}...")
                response = client.responses.create(
                    model="gpt-5.2",
                    input=input
                )
                text = response.output[0].content[0].text
            except Exception as e:
                print(f"An error occurred during text splitting for video {filename}: {str(e)}")
                text += f"\n[An error occurred during text splitting: {str(e)}]\n"

        # Finally, save the transcription to a file
        if text.strip() != "":
            with open(transcription_path, "w") as f:
                f.write(text)
            print(f"Transcription for video {filename} completed")
            os.utime(transcription_path, (modTime, modTime))
        else:
            print(f"Transcription for video {filename} is empty, not creating transcription file.")

    except Exception as e:
        print(f"An error occurred during transcription: {str(e)}")
        with open(transcription_path, "w") as f:
            f.write(f"An error occurred during transcription: {str(e)}")
        os.utime(transcription_path, (modTime, modTime))

if __name__ == "__main__":

    video_list = load_source_list_from_file("transcription.txt")
    save_source_list_to_file("transcription.txt", [])

    for fn in video_list:
        file_path = "yt-video/" + fn
        description_path = "yt-video/" + fn + ".desc"
        transcription_path = "yt-video/" + fn + ".txt"
        if not os.path.exists(transcription_path) and os.path.exists(file_path):
            if fn in video_list:
                print(f"Video {fn} is scheduled for transcription, adding link to description.")
                transcribe_video(fn)

    for mp3 in Path("yt-video").glob("*.mp3"):
        if mp3.is_file():
            print(f"Removing temporary mp3 file: {mp3}")
            mp3.unlink()
