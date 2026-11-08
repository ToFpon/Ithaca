"""Fichiers audio de test générés par ffmpeg : étiquettes, pochette intégrée ou du dossier,
ReplayGain, fichier illisible."""

import os
import subprocess


def tone(path: str, seconds: float, freq: int = 440, tags: dict = None, cover: str = "", codec: str = ""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    codec = codec or {".mp3": "libmp3lame", ".flac": "flac", ".ogg": "libvorbis", ".opus": "libopus",
                      ".m4a": "aac", ".wav": "pcm_s16le"}[ext]
    cmd += ["-c:a", codec]
    if ext == ".mp3":
        cmd += ["-id3v2_version", "3"]
    for k, v in (tags or {}).items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd.append(path)
    subprocess.run(cmd, check=True)
    return path


def image(path: str, size: str = "300x300", source: str = "testsrc"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{source}=size={size}",
                    "-frames:v", "1", path], check=True)
    return path


def library(root: str) -> dict:
    """Un petit ensemble : un album à pochette intégrée, un album à pochette dans le dossier,
    un fichier sans étiquette, un faux fichier audio."""
    art = image(os.path.join(root, "src", "art.png"))
    a = os.path.join(root, "Musique", "Les Testeurs", "Premier album")
    b = os.path.join(root, "Musique", "Autre artiste", "Second album")
    files = {
        "a1": tone(os.path.join(a, "01 - Ouverture.mp3"), 2, 440,
                   {"title": "Ouverture", "artist": "Les Testeurs", "album": "Premier album", "track": "1",
                    "REPLAYGAIN_TRACK_GAIN": "-6.00 dB", "REPLAYGAIN_ALBUM_GAIN": "-3.00 dB",
                    "REPLAYGAIN_TRACK_PEAK": "0.500000"}, cover=art),
        "a2": tone(os.path.join(a, "02 - Traversée.mp3"), 2, 550,
                   {"title": "Traversée", "artist": "Les Testeurs", "album": "Premier album", "track": "2"}, cover=art),
        "a10": tone(os.path.join(a, "10 - Retour.mp3"), 2, 660,
                    {"title": "Retour", "artist": "Les Testeurs", "album": "Premier album", "track": "10"}, cover=art),
        "b1": tone(os.path.join(b, "01 - Matin.flac"), 2, 330,
                   {"TITLE": "Matin", "ARTIST": "Autre artiste", "ALBUM": "Second album"}),
        "b2": tone(os.path.join(b, "02 - Soir.ogg"), 2, 770,
                   {"TITLE": "Soir", "ARTIST": "Autre artiste", "ALBUM": "Second album"}),
        "bare": tone(os.path.join(root, "Musique", "sans étiquette.wav"), 1, 880),
    }
    image(os.path.join(b, "cover.jpg"), "200x200", "testsrc2")
    fake = os.path.join(root, "Musique", "faux.mp3")
    with open(fake, "w") as fh:
        fh.write("ce n'est pas du son")
    with open(os.path.join(root, "Musique", "notes.txt"), "w") as fh:
        fh.write("x")
    files["fake"] = fake
    files["root"] = os.path.join(root, "Musique")
    files["album_a"] = a
    files["album_b"] = b
    return files
