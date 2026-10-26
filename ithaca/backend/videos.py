"""Vidéos et audio avec yt-dlp (binaire géré par Ithaca, voir tools.py) et ffmpeg d'apt.

Fichiers : yt-dlp travaille dans un dossier caché propre au téléchargement (« .ithaca-<id> » dans le
dossier de destination, même disque : rangement par simple renommage). Une fois fini, Ithaca range
le résultat sous un nom libre (« titre (2).mp4 », playlist : son sous-dossier) — jamais
d'écrasement, et yt-dlp ne voit jamais les fichiers de l'utilisateur. Annuler supprime ce dossier
caché, rien d'autre. Pause : SIGINT au groupe de processus (yt-dlp et ffmpeg), les « .part »
restent ; reprendre relance la même commande, yt-dlp continue là où il en était.

Fils : un fil par téléchargement lit la sortie de yt-dlp ; les Download ne sont modifiés que dans
la boucle GLib (_post), comme pour aria2.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
from typing import Optional

from gi.repository import GLib

from . import tools
from .fileops import place_new
from ..settings import settings

WORK_PREFIX = ".ithaca-"

# Sites vidéo courants : « Télécharger » y propose directement le choix vidéo / audio.
VIDEO_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "dailymotion.com", "dai.ly",
               "twitch.tv", "soundcloud.com", "bandcamp.com", "tiktok.com", "instagram.com", "facebook.com",
               "fb.watch", "x.com", "twitter.com", "reddit.com", "v.redd.it", "arte.tv", "france.tv",
               "rumble.com", "odysee.com", "bilibili.com", "nicovideo.jp", "mixcloud.com", "peertube.fr")
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
CONTAINERS = ("mp4", "mkv")


class VideoError(Exception):
    pass


def _post(fn, *args):
    GLib.idle_add(lambda: (fn(*args), False)[1])


def looks_like_video(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


def ffmpeg() -> str:
    return shutil.which("ffmpeg") or ""


def base_args() -> list[str]:
    """Options communes : configuration de l'utilisateur ignorée (comportement prévisible), Deno pour
    YouTube, cookies, ffmpeg d'apt."""
    args = ["--ignore-config", "--color", "never", "--no-update"]
    deno = tools.path("deno")
    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]
    browser = settings["browser_cookies"]
    cookies = settings["cookies_file"]
    if browser in BROWSERS:
        args += ["--cookies-from-browser", browser]
    elif cookies and os.path.isfile(cookies):
        args += ["--cookies", cookies]
    if ffmpeg():
        args += ["--ffmpeg-location", ffmpeg()]
    return args


def _last_error(text: str) -> str:
    errors = [line[len("ERROR:"):].strip() for line in text.splitlines() if line.startswith("ERROR:")]
    if errors:
        return errors[-1]
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def probe(url: str, timeout: float = 120) -> dict:
    """Informations de la vidéo ou de la playlist (yt-dlp -J). Bloquant : à appeler dans un fil."""
    exe = tools.path("yt-dlp")
    if not exe:
        raise VideoError("yt-dlp")
    cmd = [exe, *base_args(), "-J", "--flat-playlist", "--no-warnings", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise VideoError("timeout") from e
    except OSError as e:
        raise VideoError(str(e)) from e
    if p.returncode != 0 or not p.stdout.strip():
        raise VideoError(_last_error(p.stderr) or f"yt-dlp : code {p.returncode}")
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise VideoError(str(e)) from e


def summarize(info: dict) -> dict:
    """Ce que montre la fenêtre de choix : titre, durée, qualités, sous-titres, playlist."""
    playlist = info.get("_type") == "playlist"
    formats = info.get("formats") or []
    heights = sorted({int(f["height"]) for f in formats
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    if not heights:
        heights = sorted({int(f["height"]) for f in formats if f.get("height")}, reverse=True)
    has_video = playlist or any(f.get("vcodec") not in ("none",) for f in formats) or not formats
    if formats and all(f.get("vcodec") == "none" for f in formats):
        has_video = False
    subs = {}
    for lang, tracks in (info.get("subtitles") or {}).items():
        if lang == "live_chat":
            continue
        name = (tracks[0].get("name") if tracks and isinstance(tracks[0], dict) else "") or lang
        subs[lang] = name
    entries = info.get("entries") or []
    return {
        "title": info.get("title") or info.get("id") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": _first_thumbnail(info),
        "playlist": playlist,
        "count": info.get("playlist_count") or len(entries),
        "heights": heights,
        "has_video": has_video,
        "subtitles": subs,
    }


def _first_thumbnail(info: dict) -> str:
    """Miniature en JPEG ou PNG de préférence (GTK 4.14 ne lit pas toujours le WebP de YouTube)."""
    candidates = [t.get("url", "") for t in (info.get("thumbnails") or [])]
    candidates.append(info.get("thumbnail") or "")
    for e in info.get("entries") or []:
        candidates += [t.get("url", "") for t in (e.get("thumbnails") or [])]
    candidates = [u for u in candidates if u.startswith("http")]
    for u in reversed(candidates):
        if u.split("?")[0].lower().endswith((".jpg", ".jpeg", ".png")):
            return u
    return candidates[-1] if candidates else ""


def default_options() -> dict:
    return {"mode": settings["video_mode"], "height": int(settings["video_height"]),
            "container": settings["video_container"], "audio_format": settings["audio_format"],
            "subs": [], "playlist": False, "title": ""}


def format_args(opts: dict) -> list[str]:
    """Options yt-dlp de format d'après les choix de la fenêtre."""
    if opts.get("mode") == "audio":
        fmt = opts.get("audio_format") or "mp3"
        args = ["-f", "ba/b", "-x", "--audio-quality", "0"]
        if fmt in AUDIO_FORMATS:
            args += ["--audio-format", fmt]
        return args + ["--embed-metadata"]
    h = int(opts.get("height") or 0)
    lim = f"[height<={h}]" if h else ""
    container = opts.get("container") or "mp4"
    if container == "mp4":
        # pistes compatibles MP4 en priorité (lecture partout), sinon la meilleure, convertie à l'assemblage
        fmt = f"bv*{lim}[ext=mp4]+ba[ext=m4a]/b{lim}[ext=mp4]/bv*{lim}+ba/b{lim}/bv*+ba/b"
    else:
        fmt = f"bv*{lim}+ba/b{lim}/bv*+ba/b"
    args = ["-f", fmt, "--merge-output-format", container, "--embed-metadata"]
    subs = [s for s in opts.get("subs") or [] if s]
    if subs:
        args += ["--write-subs", "--sub-langs", ",".join(subs), "--embed-subs"]
    return args


def output_template(opts: dict) -> str:
    if opts.get("playlist"):
        return "%(playlist_title,playlist,title)s/%(playlist_index)03d - %(title)s.%(ext)s"
    return "%(title)s.%(ext)s"


PROGRESS = ("download:ITHACA-P %(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
            "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s")


def command(d) -> list[str]:
    exe = tools.path("yt-dlp")
    opts = d.options
    cmd = [exe, *base_args(), *format_args(opts),
           "-P", workdir(d), "-o", output_template(opts),
           "--no-simulate", "--progress", "--newline", "--progress-template", PROGRESS,
           "--print", "before_dl:ITHACA-V %(playlist_index|)s|%(playlist_count|)s|%(title)s",
           "--print", "after_move:ITHACA-F %(filepath)s",
           "--no-mtime", "--continue", "--part"]
    cmd += ["--yes-playlist", "--ignore-errors"] if opts.get("playlist") else ["--no-playlist"]
    return cmd + ["--", d.url]


def workdir(d) -> str:
    return os.path.join(d.dir, WORK_PREFIX + d.id)


def remove_workdir(d) -> bool:
    """Supprime le dossier caché de ce téléchargement — et seulement lui (nom attendu, vrai dossier)."""
    w = workdir(d)
    if not os.path.basename(w).startswith(WORK_PREFIX) or os.path.islink(w) or not os.path.isdir(w):
        return False
    shutil.rmtree(w, ignore_errors=True)
    return not os.path.exists(w)


def _num(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


class Job(threading.Thread):
    """Un yt-dlp en cours ; les nouvelles vont à runner._update dans la boucle GLib."""

    def __init__(self, runner: "VideoRunner", d):
        super().__init__(name="ithaca-ytdlp", daemon=True)
        self.runner = runner
        self.d = d
        self.cmd = command(d)
        self.proc: Optional[subprocess.Popen] = None
        self.stopping = ""              # « pause » ou « cancel » quand on l'arrête nous-mêmes
        self.files: list[str] = []
        self.tail: list[str] = []

    def run(self):
        os.makedirs(workdir(self.d), exist_ok=True)
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
                                         start_new_session=True,
                                         env=dict(os.environ, LC_ALL="C.UTF-8", PYTHONIOENCODING="utf-8"))
        except OSError as e:
            _post(self.runner._finished, self, 127, str(e))
            return
        with self.proc.stdout:
            for line in self.proc.stdout:
                self._line(line.rstrip("\n"))
        code = self.proc.wait()
        _post(self.runner._finished, self, code, _last_error("\n".join(self.tail)))

    def _line(self, line: str):
        if line.startswith("ITHACA-P "):
            parts = line[9:].split("|")
            if len(parts) >= 6:
                status, done, total, estimate, speed, eta = parts[:6]
                _post(self.runner._progress, self.d, status, int(_num(done)),
                      int(_num(total) or _num(estimate)), int(_num(speed)), _num(eta))
        elif line.startswith("ITHACA-V "):
            idx, count, title = (line[9:].split("|", 2) + ["", ""])[:3]
            _post(self.runner._item, self.d, int(_num(idx)), int(_num(count)), title)
        elif line.startswith("ITHACA-F "):
            self.files.append(line[9:])
        elif line.strip():
            self.tail = (self.tail + [line])[-40:]

    def signal(self, how: str):
        self.stopping = how
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGINT)
            except OSError:
                pass

    def kill(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass


class VideoRunner:
    """Téléchargements vidéo, côté boucle GLib : au plus « simultaneous » yt-dlp à la fois."""

    def __init__(self, manager):
        self.manager = manager
        self.jobs: dict[str, Job] = {}
        self.queue: list = []

    def start(self, d):
        if d.id in self.jobs or d in self.queue:
            return
        self.queue.append(d)
        self._pump()

    def _pump(self):
        limit = max(1, int(settings["simultaneous"]))
        while self.queue and len(self.jobs) < limit:
            d = self.queue.pop(0)
            if not tools.installed("yt-dlp"):
                self._fail(d, "yt-dlp")
                continue
            d.state, d.phase = "active", "start"
            job = Job(self, d)
            self.jobs[d.id] = job
            job.start()
            self.manager._emit("changed", d)

    def pause(self, d):
        if d in self.queue:
            self.queue.remove(d)
        job = self.jobs.get(d.id)
        if job is not None:
            job.signal("pause")

    def cancel(self, d):
        """Arrête et supprime le dossier caché (une fois yt-dlp arrêté)."""
        if d in self.queue:
            self.queue.remove(d)
        job = self.jobs.get(d.id)
        if job is not None:
            job.signal("cancel")
            GLib.timeout_add(4000, lambda: (job.kill(), False)[1])
        else:
            remove_workdir(d)

    def shutdown(self, timeout: float = 5.0):
        for job in list(self.jobs.values()):
            job.signal("quit")
        for job in list(self.jobs.values()):
            job.join(timeout)
            job.kill()

    # -- nouvelles du fil -----------------------------------------------------------
    def _progress(self, d, status, done, total, speed, eta):
        if d.id not in self.jobs:
            return
        if status == "finished":
            d.phase = "process"
            d.speed = 0
        else:
            d.phase = "download"
            d.done, d.total, d.speed = done, max(total, done), speed
        self.manager._emit("changed", d)

    def _item(self, d, index, count, title):
        d.item_index, d.item_count, d.item_title = index, count, title
        if title and not d.options.get("playlist") and not d.options.get("title"):
            d.options["title"] = title
        self.manager._emit("changed", d)

    def _finished(self, job: Job, code: int, error: str):
        d = job.d
        self.jobs.pop(d.id, None)
        d.speed = 0
        if job.stopping == "cancel":
            remove_workdir(d)
        elif job.stopping in ("pause", "quit"):
            pass                                    # .part gardés : reprise plus tard
        elif code == 0 or (d.options.get("playlist") and self._has_results(d)):
            try:
                self._collect(d)
            except OSError as e:
                self._fail(d, str(e))
            else:
                d.state, d.phase = "complete", ""
                d.finished = time.time()
                self.manager._emit("changed", d)
                self.manager._emit("completed", d)
        else:
            self._fail(d, error or f"yt-dlp : code {code}")
        self.manager.save_soon()
        self._pump()

    def _fail(self, d, message: str):
        d.state, d.phase, d.error, d.error_code = "error", "", message, ""
        self.manager._emit("changed", d)
        self.manager._emit("failed", d)

    def _has_results(self, d) -> bool:
        w = workdir(d)
        for _root, _dirs, files in os.walk(w):
            if any(not f.endswith((".part", ".ytdl")) and ".part-Frag" not in f for f in files):
                return True
        return False

    def _collect(self, d):
        """Range le contenu du dossier caché dans le dossier de destination, sans rien écraser."""
        w = workdir(d)
        entries = [e for e in os.listdir(w) if not e.endswith((".part", ".ytdl"))]
        placed = []
        for e in sorted(entries):
            placed.append(place_new(os.path.join(w, e), d.dir, e))
        size = 0
        for name in placed:
            p = os.path.join(d.dir, name)
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    size += sum(os.path.getsize(os.path.join(root, f)) for f in files)
            else:
                size += os.path.getsize(p)
        if placed:
            # nom affiché : le dossier de la playlist, sinon le plus gros fichier (la vidéo, pas les sous-titres)
            main = max(placed, key=lambda n: (os.path.isdir(os.path.join(d.dir, n)),
                                              os.path.getsize(os.path.join(d.dir, n))))
            d.name, d.name_fixed = main, True
        d.total = d.done = size
        remove_workdir(d)
