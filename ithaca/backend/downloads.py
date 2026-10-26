"""Téléchargements : liste, état persistant, pilotage d'aria2.

Fils : aria2 n'est appelé que depuis le fil du moteur (Engine), qui exécute les commandes dans
l'ordre puis relève l'état toutes les POLL secondes. Les objets Download ne sont lus et modifiés
que dans la boucle GLib (résultats renvoyés par GLib.idle_add) : l'interface n'a jamais besoin
de verrou.

Fichiers : aria2 tourne avec --allow-overwrite=false et --auto-file-renaming=false ; un nom déjà
pris arrête le téléchargement (erreur 13) et on le relance sous « nom (2).ext », comme Nautilus.
Un fichier partiel n'est supprimé (annulation) que s'il a son fichier de contrôle « .aria2 » à côté :
c'est la preuve qu'aria2 l'écrit pour nous ; tout autre fichier n'est jamais touché.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.parse
import uuid
from typing import Callable, Optional

from gi.repository import GLib

from . import aria2
from ..settings import CACHE_DIR, DATA_DIR, settings

STATE_FILE = os.path.join(DATA_DIR, "downloads.json")
POLL = 0.5
STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "connections",
               "errorCode", "errorMessage", "files", "dir"]

# États : waiting (en file), active, paused, complete, error
UNFINISHED = ("waiting", "active", "paused")
# Erreurs passagères d'aria2 (délai, réseau, en-tête inattendu, serveur surchargé) : relance
# automatique en reprenant le partiel, avec deux fois moins de connexions à chaque fois — beaucoup
# de serveurs coupent ou répondent de travers au-delà de quelques connexions par adresse IP.
TRANSIENT = ("2", "6", "22", "29")
AUTO_RETRIES = 3
AUTO_RETRY_DELAY = 3000         # ms
# Serveur qui rationne (HTTP 429 Too Many Requests, 503) : proof.ovh.net refusait les requêtes
# suivantes au-delà de quelques-unes par adresse IP. Relance avec UNE connexion (une seule requête)
# après une attente croissante, et ce serveur garde une connexion pour la suite de la session.
RATE_LIMITED = (429, 503)
RATE_LIMIT_DELAYS = (5000, 15000, 45000)      # ms


def http_status(message: str) -> int:
    """Code HTTP d'un message d'erreur d'aria2 (« The response status is not successful. status=429 »)."""
    m = re.search(r"status=(\d{3})", message or "", re.IGNORECASE)
    return int(m.group(1)) if m else 0


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
DOUBLE_EXTS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4", ".tar.br")
SUPPORTED_SCHEMES = ("http", "https", "ftp", "sftp")


def _post(fn, *args):
    """Exécute fn(*args) dans la boucle GLib (une seule fois)."""
    GLib.idle_add(lambda: (fn(*args), False)[1])


def is_supported_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in SUPPORTED_SCHEMES and bool(parts.netloc)


def name_from_url(url: str) -> str:
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return url
    name = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1])
    return name or url


def split_ext(name: str) -> tuple[str, str]:
    low = name.lower()
    for ext in DOUBLE_EXTS:
        if low.endswith(ext) and len(name) > len(ext):
            return name[:-len(ext)], name[-len(ext):]
    base, ext = os.path.splitext(name)
    return (base, ext) if base else (name, "")


def free_name(folder: str, name: str, taken: set = frozenset()) -> str:
    """« nom (2).ext », « nom (3).ext »… : premier nom libre dans le dossier (fichier et contrôle .aria2)."""
    base, ext = split_ext(name)
    n = 2
    candidate = name
    while (candidate in taken or os.path.lexists(os.path.join(folder, candidate))
           or os.path.lexists(os.path.join(folder, candidate + ".aria2"))):
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


def remove_partial(path: str) -> bool:
    """Supprime un fichier partiel d'aria2 et son contrôle, seulement si le contrôle existe."""
    if not path or not os.path.exists(path + ".aria2"):
        return False
    for p in (path, path + ".aria2"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError:
            return False
    return True


class Download:
    def __init__(self, url: str, folder: str, name: str = "", did: str = ""):
        self.id = did or uuid.uuid4().hex
        self.kind = "http"
        self.url = url
        self.dir = folder
        self.name = name
        self.name_fixed = bool(name)        # nom imposé à aria2 (out=) : reprise au même endroit
        self.gid = ""
        self.state = "waiting"
        self.total = 0
        self.done = 0
        self.speed = 0
        self.connections = 0
        self.error = ""
        self.error_code = ""
        self.added = time.time()
        self.finished = 0.0
        self.cancelled = False
        self.intent_until = 0.0             # pause / reprise demandée : aria2 n'en tient pas encore compte
        self.auto_retries = 0               # relances automatiques après une erreur passagère
        self.max_connections = 0            # 0 = préférences ; réduit par les relances automatiques
        # vidéos (yt-dlp) : choix de la fenêtre (mode, qualité, format…), playlist en cours
        self.options: dict = {}
        self.phase = ""                     # start, download, process (assemblage / conversion)
        self.item_index = 0
        self.item_count = 0
        self.item_title = ""

    @property
    def path(self) -> str:
        return os.path.join(self.dir, self.name) if self.name else ""

    @property
    def display_name(self) -> str:
        if self.kind == "video" and not (self.state == "complete" and self.name):
            return self.options.get("title") or self.name or self.url
        return self.name or name_from_url(self.url)

    @property
    def fraction(self) -> float:
        if self.state == "complete":
            return 1.0
        part = min(1.0, self.done / self.total) if self.total > 0 else 0.0
        if self.kind == "video" and self.item_count > 1 and self.item_index > 0:
            return min(1.0, (self.item_index - 1 + part) / self.item_count)
        return part

    @property
    def eta(self) -> float:
        """Secondes restantes, -1 si inconnu."""
        if self.speed <= 0 or self.total <= 0:
            return -1
        return max(0, self.total - self.done) / self.speed

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "url": self.url, "dir": self.dir, "name": self.name,
                "name_fixed": self.name_fixed, "state": self.state, "total": self.total, "done": self.done,
                "error": self.error, "error_code": self.error_code, "added": self.added,
                "finished": self.finished, "options": self.options}

    @classmethod
    def from_json(cls, data: dict) -> "Download":
        d = cls(data["url"], data["dir"], data.get("name", ""), data.get("id", ""))
        d.kind = data.get("kind", "http")
        d.name_fixed = bool(data.get("name_fixed", d.name_fixed))
        d.state = data.get("state", "waiting")
        d.total = int(data.get("total", 0))
        d.done = int(data.get("done", 0))
        d.error = data.get("error", "")
        d.error_code = data.get("error_code", "")
        d.added = float(data.get("added", time.time()))
        d.finished = float(data.get("finished", 0))
        d.options = dict(data.get("options") or {})
        return d


def global_options() -> dict:
    limit = settings["speed_limit"] if settings["speed_limit_enabled"] else 0
    return {"max-concurrent-downloads": str(max(1, int(settings["simultaneous"]))),
            "max-overall-download-limit": str(max(0, int(limit)) * 1000)}     # Ko/s → octets/s


def start_options() -> dict:
    opts = {
        "allow-overwrite": "false",
        "auto-file-renaming": "false",
        "file-allocation": "none",
        "auto-save-interval": "10",           # fichiers de contrôle à jour : reprise après un plantage
        "content-disposition-default-utf8": "true",
        "max-download-result": "1000",
        "max-tries": "5",
        "retry-wait": "3",
        "min-split-size": "1M",
        "disk-cache": "32M",
        "follow-torrent": "false",            # torrents : libtorrent, pas aria2
        "follow-metalink": "false",
        "enable-dht": "false",
        "enable-dht6": "false",
        "bt-enable-lpd": "false",
        "enable-peer-exchange": "false",
    }
    opts.update(global_options())
    return opts


def download_options(d: Download, single: bool = False) -> dict:
    """Options d'aria2 propres à un téléchargement, d'après les préférences du moment.
    single : une seule connexion (serveur qui rationne les requêtes)."""
    seg = 1 if single else max(1, int(settings["segments"]))
    if d.max_connections:
        seg = min(seg, d.max_connections)
    opts = {"dir": d.dir, "split": str(seg), "max-connection-per-server": str(min(seg, 16))}
    if d.name_fixed and d.name:
        opts["out"] = d.name
    if settings["auth_enabled"] and settings["auth_user"]:
        scheme = urllib.parse.urlsplit(d.url).scheme.lower()
        prefix = "ftp" if scheme in ("ftp", "sftp") else "http"
        opts[f"{prefix}-user"] = settings["auth_user"]
        opts[f"{prefix}-passwd"] = settings["auth_password"]
    cookies = settings["cookies_file"]
    if cookies and os.path.isfile(cookies):
        opts["load-cookies"] = cookies
    return opts


class Engine(threading.Thread):
    """Fil du moteur : seul à parler à aria2."""

    def __init__(self, manager: "Manager"):
        super().__init__(name="ithaca-aria2", daemon=True)
        self.manager = manager
        self.rpc = aria2.Aria2()
        self.commands: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.failed = ""

    def submit(self, fn: Callable[[aria2.Aria2], None]):
        self.commands.put(fn)

    def run(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            self.rpc.start(start_options(), log_file=os.path.join(CACHE_DIR, "aria2.log"))
        except (aria2.RPCError, OSError) as e:
            self.failed = str(e) or "aria2"
            _post(self.manager._engine_failed, self.failed)
            self.ready.set()
            return
        self.ready.set()
        _post(self.manager._engine_ready)
        last = 0.0
        while not self.stopping.is_set():
            try:
                fn = self.commands.get(timeout=max(0.01, POLL - (time.monotonic() - last)))
                self._run(fn)
                while True:                    # toutes les commandes en attente avant le relevé
                    self._run(self.commands.get_nowait())
            except queue.Empty:
                pass
            if time.monotonic() - last >= POLL and not self.stopping.is_set():
                last = time.monotonic()
                self._poll()
        while True:                            # commandes envoyées juste avant l'arrêt
            try:
                self._run(self.commands.get_nowait())
            except queue.Empty:
                break
        self.rpc.stop()

    def _run(self, fn):
        try:
            fn(self.rpc)
        except aria2.RPCError:
            pass
        except Exception:                       # une commande fautive ne doit pas tuer le moteur
            import traceback
            traceback.print_exc()

    def _poll(self):
        try:
            active, waiting, stopped, stat = self.rpc.multicall([
                ("aria2.tellActive", STATUS_KEYS),
                ("aria2.tellWaiting", 0, 1000, STATUS_KEYS),
                ("aria2.tellStopped", 0, 1000, STATUS_KEYS),
                ("aria2.getGlobalStat",),
            ])
        except aria2.RPCError:
            return
        statuses = {}
        for group in (active, waiting, stopped):
            if isinstance(group, list):
                for s in group:
                    statuses[s["gid"]] = s
        speed = int(stat.get("downloadSpeed", 0)) if isinstance(stat, dict) else 0
        _post(self.manager._apply, statuses, speed)

    def stop(self):
        self.stopping.set()


class Manager:
    """Liste des téléchargements, côté boucle GLib. Événements envoyés aux écouteurs :
    added, changed, removed, completed, failed, engine (moteur prêt ou en échec), stats."""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.downloads: list[Download] = []
        self._by_gid: dict[str, Download] = {}
        self.rationed_hosts: set[str] = set()   # serveurs qui ont répondu 429 / 503 (cette session)
        from .videos import VideoRunner
        self.videos = VideoRunner(self)
        self._listeners: list[Callable] = []
        self.engine: Optional[Engine] = None
        self.engine_error = ""
        self.engine_ready = False
        self.total_speed = 0
        self._save_timer = 0
        self._last_progress_save = 0.0
        settings.connect(self._on_setting)

    # -- écouteurs ---------------------------------------------------------------
    def connect(self, cb: Callable[[str, Optional[Download]], None]):
        self._listeners.append(cb)

    def disconnect(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _emit(self, event: str, d: Optional[Download] = None):
        for cb in list(self._listeners):
            cb(event, d)

    # -- démarrage / arrêt ---------------------------------------------------------
    def start(self):
        self._load()
        self.engine = Engine(self)
        self.engine.start()
        for d in self.downloads:
            if d.kind == "video" and d.state in ("waiting", "active"):
                self.videos.start(d)

    def _engine_ready(self):
        self.engine_ready = True
        for d in self.downloads:
            if d.kind == "http" and d.state in UNFINISHED:
                self._submit_add(d, paused=d.state == "paused")
        self._emit("engine")

    def _engine_failed(self, message: str):
        self.engine_error = message
        for d in self.downloads:
            if d.kind == "http" and d.state in ("waiting", "active"):
                d.state = "paused"
        self._emit("engine")

    def shutdown(self):
        """Quitter : l'état est enregistré tel quel, les téléchargements reprendront au lancement."""
        self.save()
        self.videos.shutdown()
        if self.engine is not None:
            self.engine.stop()
            self.engine.join(6)
            self.engine = None
        settings.disconnect(self._on_setting)

    def _on_setting(self, key, _value):
        if key in ("simultaneous", "speed_limit", "speed_limit_enabled") and self.engine:
            opts = global_options()
            self.engine.submit(lambda rpc: rpc.call("aria2.changeGlobalOption", opts))

    # -- persistance ---------------------------------------------------------------
    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        for item in data.get("downloads", []) if isinstance(data, dict) else []:
            try:
                d = Download.from_json(item)
            except (KeyError, TypeError, ValueError):
                continue
            if d.state == "active":
                d.state = "waiting"
            self.downloads.append(d)

    def save(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "downloads": [d.to_json() for d in self.downloads]}, fh,
                          indent=1, ensure_ascii=False)
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def save_soon(self):
        if not self._save_timer:
            self._save_timer = GLib.timeout_add(500, self._save_timeout)

    def _save_timeout(self):
        self._save_timer = 0
        self.save()
        return False

    # -- actions -------------------------------------------------------------------
    def add(self, url: str, folder: str = "", name: str = "", paused: bool = False) -> Download:
        from ..settings import download_dir
        d = Download(url.strip(), folder or download_dir(), name)
        d.state = "paused" if paused else "waiting"
        self.downloads.append(d)
        self._emit("added", d)
        if self.engine_ready:
            self._submit_add(d, paused)
        self.save_soon()
        return d

    def add_video(self, url: str, options: dict, folder: str = "") -> Download:
        """Vidéo / audio / playlist avec yt-dlp (options : voir videos.format_args)."""
        from ..settings import download_dir
        d = Download(url.strip(), folder or download_dir())
        d.kind = "video"
        d.options = dict(options)
        self.downloads.append(d)
        self._emit("added", d)
        self.videos.start(d)
        self.save_soon()
        return d

    def _submit_add(self, d: Download, paused: bool = False):
        if self.engine is None:
            return
        opts = download_options(d, single=host_of(d.url) in self.rationed_hosts)
        if paused:
            opts["pause"] = "true"
        url = d.url

        def cmd(rpc):
            try:
                gid = rpc.call("aria2.addUri", [url], opts)
            except aria2.RPCError as e:
                _post(self._add_failed, d, str(e))
                return
            _post(self._got_gid, d, gid)
        self.engine.submit(cmd)

    def _got_gid(self, d: Download, gid: str):
        if d.cancelled or d not in self.downloads:
            self._submit_remove(gid, "")
            return
        d.gid = gid
        self._by_gid[gid] = d
        if d.state == "paused":
            self.engine.submit(lambda rpc: rpc.call("aria2.pause", gid))

    def _add_failed(self, d: Download, message: str):
        if d not in self.downloads:
            return
        d.state, d.error, d.speed = "error", message, 0
        self._emit("changed", d)
        self._emit("failed", d)
        self.save_soon()

    def pause(self, d: Download):
        if d.state not in ("waiting", "active"):
            return
        d.state, d.speed = "paused", 0
        d.intent_until = time.monotonic() + 1.5
        if d.kind == "video":
            self.videos.pause(d)
        elif d.gid and self.engine:
            gid = d.gid
            self.engine.submit(lambda rpc: rpc.call("aria2.forcePause", gid))
        self._emit("changed", d)
        self.save_soon()

    def resume(self, d: Download):
        if d.state != "paused":
            return
        d.state = "waiting"
        d.intent_until = time.monotonic() + 1.5
        if d.kind == "video":
            self.videos.start(d)
        elif d.gid and self.engine:
            gid = d.gid
            self.engine.submit(lambda rpc: rpc.call("aria2.unpause", gid))
        elif self.engine_ready:
            self._submit_add(d)
        self._emit("changed", d)
        self.save_soon()

    def pause_all(self):
        for d in self.downloads:
            self.pause(d)

    def resume_all(self):
        for d in self.downloads:
            self.resume(d)

    def retry(self, d: Download):
        """Échec : relance au même endroit (aria2 reprend le fichier partiel s'il existe)."""
        if d.state != "error":
            return
        if d.gid:
            self._by_gid.pop(d.gid, None)
        d.gid, d.state, d.error, d.error_code, d.speed = "", "waiting", "", "", 0
        d.auto_retries = 0
        if d.kind == "video":
            self.videos.start(d)
        elif self.engine_ready:
            self._submit_add(d)
        self._emit("changed", d)
        self.save_soon()

    def _auto_retry(self, d: Download):
        if d in self.downloads and d.state == "waiting" and not d.gid and self.engine_ready:
            self._submit_add(d)
        return False

    def remove(self, d: Download):
        """Retire de la liste. Inachevé : téléchargement annulé et fichier partiel supprimé.
        Terminé : le fichier reste."""
        if d not in self.downloads:
            return
        self.downloads.remove(d)
        d.cancelled = True
        gid = d.gid
        if gid:
            self._by_gid.pop(gid, None)
        if d.state != "complete":
            if d.kind == "video":
                self.videos.cancel(d)
            else:
                self._submit_remove(gid, d.path)
        self._emit("removed", d)
        self.save_soon()

    def _submit_remove(self, gid: str, known_path: str):
        if self.engine is None:
            if known_path:
                remove_partial(known_path)
            return

        def cmd(rpc):
            path = known_path
            if gid:
                try:
                    st = rpc.call("aria2.tellStatus", gid, ["files", "status"])
                    files = st.get("files") or []
                    if files and files[0].get("path"):
                        path = files[0]["path"]
                    if st.get("status") in ("active", "waiting", "paused"):
                        rpc.call("aria2.forceRemove", gid)
                        # attendre qu'aria2 lâche le fichier avant de le supprimer
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            if rpc.call("aria2.tellStatus", gid, ["status"]).get("status") == "removed":
                                break
                            time.sleep(0.05)
                    rpc.call("aria2.removeDownloadResult", gid)
                except aria2.RPCError:
                    pass
            remove_partial(path)
        self.engine.submit(cmd)

    def clear_finished(self):
        for d in [d for d in self.downloads if d.state == "complete"]:
            self.remove(d)

    def cancel_all(self):
        for d in [d for d in self.downloads if d.state in UNFINISHED]:
            self.remove(d)

    # -- relevé d'aria2 ------------------------------------------------------------
    def _apply(self, statuses: dict, speed: int):
        self.total_speed = speed
        progressed = False
        for gid, s in statuses.items():
            d = self._by_gid.get(gid)
            if d is None:
                continue
            if self._apply_one(d, s):
                progressed = True
        self._emit("stats")
        now = time.monotonic()
        if progressed and now - self._last_progress_save > 10:
            self._last_progress_save = now
            self.save_soon()

    def _apply_one(self, d: Download, s: dict) -> bool:
        status = s.get("status", "")
        files = s.get("files") or []
        path = files[0].get("path", "") if files else ""
        if path and not d.name_fixed:
            d.name = os.path.basename(path)
            d.name_fixed = True              # reprise (relance d'Ithaca, Réessayer) sous ce nom
        d.total = int(s.get("totalLength", 0) or 0)
        d.done = int(s.get("completedLength", 0) or 0)
        d.connections = int(s.get("connections", 0) or 0)
        d.speed = int(s.get("downloadSpeed", 0) or 0) if status == "active" else 0
        before = d.state
        if status == "complete":
            d.state = "complete"
            d.finished = time.time()
            self._forget(d)
            self._emit("changed", d)
            self._emit("completed", d)
            self.save_soon()
            return True
        if status == "error":
            self._forget(d)
            if s.get("errorCode") == aria2.ERR_FILE_EXISTS:
                # nom déjà pris : « nom (2).ext », comme Nautilus (aria2 n'écrase jamais)
                taken = {x.name for x in self.downloads if x is not d and x.dir == d.dir}
                d.name = free_name(d.dir, d.name or name_from_url(d.url), taken)
                d.name_fixed = True
                d.gid, d.state = "", "waiting"
                self._submit_add(d)
                self._emit("changed", d)
                return True
            if http_status(s.get("errorMessage", "")) in RATE_LIMITED and d.auto_retries < AUTO_RETRIES:
                self.rationed_hosts.add(host_of(d.url))
                delay = RATE_LIMIT_DELAYS[min(d.auto_retries, len(RATE_LIMIT_DELAYS) - 1)]
                d.auto_retries += 1
                d.max_connections = 1
                d.gid, d.state, d.speed = "", "waiting", 0
                GLib.timeout_add(delay, self._auto_retry, d)
                self._emit("changed", d)
                return True
            if s.get("errorCode") in TRANSIENT and d.auto_retries < AUTO_RETRIES:
                d.auto_retries += 1
                used = d.max_connections or max(1, int(settings["segments"]))
                d.max_connections = max(1, used // 2)
                d.gid, d.state, d.speed = "", "waiting", 0
                GLib.timeout_add(AUTO_RETRY_DELAY, self._auto_retry, d)
                self._emit("changed", d)
                return True
            d.state = "error"
            d.error = s.get("errorMessage", "") or ""
            d.error_code = s.get("errorCode", "") or ""
            self._emit("changed", d)
            self._emit("failed", d)
            self.save_soon()
            return True
        if status == "removed":
            self._forget(d)
            return False
        if time.monotonic() < d.intent_until:
            # relevé fait avant la pause / reprise demandée : on garde l'état voulu
            pass
        elif status in ("active", "waiting", "paused"):
            d.state = status
        self._emit("changed", d)
        if d.state != before:
            self.save_soon()
        return status == "active"

    def _forget(self, d: Download):
        gid = d.gid
        if gid:
            self._by_gid.pop(gid, None)
            if self.engine:
                self.engine.submit(lambda rpc: rpc.call("aria2.removeDownloadResult", gid))

    # -- statistiques -----------------------------------------------------------------
    def count(self, kind: str) -> int:
        return sum(1 for d in self.downloads if matches(d, kind))

    def any_running(self) -> bool:
        return any(d.state in ("waiting", "active") for d in self.downloads)


def matches(d: Download, filt: str) -> bool:
    """Filtres de la barre latérale : all, progress, complete, failed."""
    if filt == "progress":
        return d.state in UNFINISHED
    if filt == "complete":
        return d.state == "complete"
    if filt == "failed":
        return d.state == "error"
    return True
