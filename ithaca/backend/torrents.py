"""Torrents avec libtorrent (python3-libtorrent 2.0.10 d'Ubuntu 24.04).

Une session libtorrent, créée au premier torrent (aucun port ouvert tant qu'on ne s'en sert pas).
Tout se passe dans la boucle GLib : libtorrent a ses propres fils réseau et disque, ses appels ne
bloquent pas ; un relevé toutes les 0,5 s lit les alertes et l'état de chaque torrent.

Ajout en deux temps (fenêtre de choix) : `prepare_*` donne un `Pending` — pour un lien magnet, le
torrent est ajouté en « upload mode » (rien n'est écrit sur le disque) le temps de recevoir ses
informations ; `start` applique ensuite le dossier, les fichiers voulus et les noms choisis.

Fichiers : le dossier racine (ou le fichier unique) reçoit un nom libre (« Album (2) ») avant que
libtorrent n'écrive quoi que ce soit — un fichier existant de l'utilisateur n'est jamais repris ni
complété. Annuler un torrent inachevé supprime ses fichiers (delete_files, seulement les siens).

Reprise : données de reprise de libtorrent (avec les informations du torrent) dans
~/.local/share/ithaca/torrents/<id>.resume, écrites à la pause, régulièrement et en quittant.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from gi.repository import GLib

from .. import VERSION
from ..settings import DATA_DIR, settings

TORRENT_DIR = os.path.join(DATA_DIR, "torrents")
POLL_MS = 500
RESUME_EVERY = 30.0         # secondes entre deux sauvegardes de reprise d'un torrent actif

try:
    import libtorrent as lt
except ImportError:          # python3-libtorrent absent : la fonctionnalité est signalée manquante
    lt = None


class TorrentError(Exception):
    pass


def available() -> bool:
    return lt is not None


def is_magnet(url: str) -> bool:
    return url.strip().lower().startswith("magnet:?")


@dataclass
class TorrentFile:
    index: int
    path: str               # chemin dans le torrent (« Album/CD1/01.flac »)
    size: int


@dataclass
class Pending:
    """Torrent en cours de choix (fenêtre)."""
    source: str                                     # magnet ou chemin / adresse du .torrent
    ti: object = None                               # lt.torrent_info, quand on l'a
    handle: object = None                           # magnet : ajouté en upload mode pour les informations
    on_ready: Optional[Callable] = None
    on_error: Optional[Callable] = None
    files: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.ti.name() if self.ti is not None else ""

    @property
    def multi(self) -> bool:
        return bool(self.files) and any("/" in f.path for f in self.files)

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)


def files_of(ti) -> list[TorrentFile]:
    """Fichiers du torrent, sans les fichiers de bourrage (« .pad/… », libtorrent 2)."""
    fs = ti.files()
    pad = getattr(lt.file_storage, "flag_pad_file", 1)
    return [TorrentFile(i, fs.file_path(i), fs.file_size(i))
            for i in range(fs.num_files()) if not (fs.file_flags(i) & pad)]


def info_from_bytes(data: bytes):
    try:
        return lt.torrent_info(lt.bdecode(data))
    except (RuntimeError, TypeError, ValueError) as e:
        raise TorrentError(str(e)) from e


def renamed_paths(ti, root: str, names: dict[int, str]) -> dict[int, str]:
    """Nouveaux chemins : dossier racine renommé (plusieurs fichiers) et noms de fichiers changés
    (names : index → nouveau nom de fichier, sans dossier). Fichier unique : names[0] est son nom."""
    fs = ti.files()
    out = {}
    for i in range(fs.num_files()):
        path = fs.file_path(i)
        parts = path.split("/")
        if len(parts) > 1 and root:
            parts[0] = root
        if i in names and names[i]:
            parts[-1] = names[i]
        new = "/".join(parts)
        if new != path:
            out[i] = new
    return out


def _settings_pack() -> dict:
    enc = lt.enc_policy.forced if settings["torrent_encryption"] else lt.enc_policy.enabled
    dht = bool(settings["torrent_dht"])
    upnp = bool(settings["torrent_upnp"])
    port = int(settings["torrent_port"])
    listen = os.environ.get("ITHACA_TORRENT_LISTEN") or f"0.0.0.0:{port},[::]:{port}"
    limit = settings["speed_limit"] * 1000 if settings["speed_limit_enabled"] else 0
    up = settings["torrent_upload_limit"] * 1000 if settings["torrent_upload_limit"] else 0
    cat = lt.alert.category_t
    test = bool(os.environ.get("ITHACA_TORRENT_LISTEN"))
    pack = {
        "listen_interfaces": listen,
        "enable_dht": dht and not os.environ.get("ITHACA_TORRENT_LISTEN"),
        "enable_lsd": dht and not os.environ.get("ITHACA_TORRENT_LISTEN"),
        "enable_upnp": upnp and not os.environ.get("ITHACA_TORRENT_LISTEN"),
        "enable_natpmp": upnp and not os.environ.get("ITHACA_TORRENT_LISTEN"),
        "out_enc_policy": int(enc),
        "in_enc_policy": int(enc),
        "allowed_enc_level": int(lt.enc_level.both),
        "prefer_rc4": bool(settings["torrent_encryption"]),
        "download_rate_limit": int(limit),
        "upload_rate_limit": int(up),
        "active_downloads": max(1, int(settings["simultaneous"])),
        "active_seeds": 8,
        "active_limit": 50,
        "allow_multiple_connections_per_ip": bool(os.environ.get("ITHACA_TORRENT_LISTEN")),
        "user_agent": f"Ithaca/{VERSION} libtorrent/{lt.__version__}",
        "alert_mask": int(cat.status_notification | cat.error_notification | cat.storage_notification),
    }
    if test:
        pack["min_reconnect_time"] = 1           # tests : un pair quitté à la pause est recontacté tout de suite
    return pack


class TorrentEngine:
    """Torrents de la liste, côté boucle GLib."""

    def __init__(self, manager):
        self.manager = manager
        self.session = None
        self.handles: dict[str, object] = {}         # id du Download → torrent_handle
        self.pending: list[Pending] = []
        self._timer = 0
        self._last_resume: dict[str, float] = {}
        self._saving: set[str] = set()

    # -- session ------------------------------------------------------------------------
    def ensure_session(self):
        if self.session is not None:
            return self.session
        if lt is None:
            raise TorrentError("python3-libtorrent")
        if not settings["torrent_port"]:
            settings["torrent_port"] = random.randint(49160, 65000)     # stable, pour l'ouverture du port
        self.session = lt.session(_settings_pack())
        self._timer = GLib.timeout_add(POLL_MS, self._poll)
        settings.connect(self._on_setting)
        return self.session

    def _on_setting(self, key, _value):
        if self.session is not None and (key.startswith("torrent_") or key in
                                         ("speed_limit", "speed_limit_enabled", "simultaneous")):
            self.session.apply_settings(_settings_pack())

    def shutdown(self, timeout: float = 5.0):
        """Quitter : données de reprise écrites pour chaque torrent, puis session fermée."""
        if self.session is None:
            return
        for p in list(self.pending):
            self.discard(p)
        waiting = set()
        for did, h in self.handles.items():
            if h.is_valid() and h.status().has_metadata:
                h.save_resume_data(lt.save_resume_flags_t.save_info_dict)
                waiting.add(did)
        self._saving |= waiting
        deadline = time.monotonic() + timeout
        while self._saving & waiting and time.monotonic() < deadline:
            self._alerts()
            time.sleep(0.05)
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        settings.disconnect(self._on_setting)
        self.session.pause()
        self.session = None
        self.handles.clear()

    # -- ajout --------------------------------------------------------------------------
    def prepare_magnet(self, uri: str, on_ready: Callable, on_error: Callable) -> Pending:
        s = self.ensure_session()
        try:
            atp = lt.parse_magnet_uri(uri.strip())
        except (RuntimeError, ValueError) as e:
            raise TorrentError(str(e)) from e
        for did, h in self.handles.items():
            if h.is_valid() and h.info_hashes() == atp.info_hashes:
                raise TorrentError("duplicate")
        atp.save_path = os.path.join(DATA_DIR, "torrents")
        os.makedirs(atp.save_path, exist_ok=True)
        atp.flags |= lt.torrent_flags.upload_mode       # informations seulement, rien sur le disque
        atp.flags &= ~lt.torrent_flags.auto_managed
        atp.flags &= ~lt.torrent_flags.paused
        p = Pending(uri, on_ready=on_ready, on_error=on_error)
        p.handle = s.add_torrent(atp)
        self.pending.append(p)
        return p

    def prepare_file(self, data: bytes, source: str = "") -> Pending:
        ti = info_from_bytes(data)
        p = Pending(source, ti=ti)
        p.files = files_of(ti)
        return p

    def discard(self, p: Pending):
        if p in self.pending:
            self.pending.remove(p)
        if p.handle is not None and self.session is not None and p.handle.is_valid():
            self.session.remove_torrent(p.handle)
        p.handle = None

    def duplicate_of(self, p: Pending):
        """Download déjà dans la liste pour ce torrent (même empreinte), sinon None."""
        if p.ti is None:
            return None
        key = str(p.ti.info_hashes().v1)
        for d in self.manager.downloads:
            if d.kind == "torrent" and d.options.get("info_hash") == key:
                return d
        return None

    def start(self, d, p: Pending, wanted: set[int], root: str, names: dict[int, str]):
        """Lance le téléchargement choisi : dossier d.dir, fichiers voulus, noms choisis."""
        s = self.ensure_session()
        ti = p.ti
        prios = [4 if i in wanted else 0 for i in range(ti.files().num_files())]
        renamed = renamed_paths(ti, root, names)
        if p.handle is not None and p.handle.is_valid():
            h = p.handle
            if p in self.pending:
                self.pending.remove(p)
            h.prioritize_files(prios)
            for i, path in renamed.items():
                h.rename_file(i, path)
            h.move_storage(d.dir)
            h.unset_flags(lt.torrent_flags.upload_mode)
            h.set_flags(lt.torrent_flags.auto_managed)
        else:
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = d.dir
            atp.file_priorities = prios
            atp.renamed_files = renamed
            h = s.add_torrent(atp)
        d.options["info_hash"] = str(ti.info_hashes().v1)
        self.handles[d.id] = h
        h.save_resume_data(lt.save_resume_flags_t.save_info_dict)

    def restore(self, d) -> bool:
        """Relance un torrent de la liste depuis ses données de reprise (lancement d'Ithaca)."""
        try:
            with open(self._resume_path(d), "rb") as fh:
                data = fh.read()
        except OSError:
            return False
        try:
            s = self.ensure_session()
            atp = lt.read_resume_data(data)
        except (RuntimeError, TorrentError):
            return False
        if d.state == "paused":
            atp.flags |= lt.torrent_flags.paused
            atp.flags &= ~lt.torrent_flags.auto_managed
        else:
            atp.flags &= ~lt.torrent_flags.paused
            atp.flags |= lt.torrent_flags.auto_managed
        atp.flags &= ~lt.torrent_flags.upload_mode
        self.handles[d.id] = s.add_torrent(atp)
        return True

    # -- actions ------------------------------------------------------------------------
    def pause(self, d):
        h = self.handles.get(d.id)
        if h is not None and h.is_valid():
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause()
            h.save_resume_data(lt.save_resume_flags_t.save_info_dict)

    def resume(self, d):
        h = self.handles.get(d.id)
        if h is None or not h.is_valid():
            if not self.restore(d):
                self.manager._torrent_failed(d, "resume")
            return
        h.set_flags(lt.torrent_flags.auto_managed)
        h.resume()

    def remove(self, d, delete_files: bool):
        """Retire le torrent ; delete_files : ses fichiers (inachevé), jamais ceux de l'utilisateur."""
        h = self.handles.pop(d.id, None)
        if h is not None and h.is_valid() and self.session is not None:
            self.session.remove_torrent(h, lt.session.delete_files if delete_files else 0)
        try:
            os.remove(self._resume_path(d))
        except OSError:
            pass

    def stop_seeding(self, d):
        """Partage fini (ratio atteint, ou arrêté) : torrent retiré de la session, fichiers gardés."""
        self.remove(d, delete_files=False)

    def connect_peer(self, target, host: str, port: int):
        """Ajoute un pair connu (tests, pairs locaux) : Download de la liste ou Pending."""
        h = target.handle if isinstance(target, Pending) else self.handles.get(target.id)
        if h is not None and h.is_valid():
            h.connect_peer((host, port))

    # -- relevé -------------------------------------------------------------------------
    def _resume_path(self, d) -> str:
        return os.path.join(TORRENT_DIR, d.id + ".resume")

    def _poll(self):
        if self.session is None:
            return False
        self._alerts()
        now = time.monotonic()
        by_handle = {}
        for did, h in self.handles.items():
            by_handle[did] = h
        for d in list(self.manager.downloads):
            h = by_handle.get(d.id)
            if d.kind != "torrent" or h is None or not h.is_valid():
                continue
            st = h.status()
            self.manager._torrent_status(d, st)
            if st.need_save_resume and st.has_metadata and now - self._last_resume.get(d.id, 0) > RESUME_EVERY:
                self._last_resume[d.id] = now
                h.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        return True

    def _alerts(self):
        for a in self.session.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                self._write_resume(a)
            elif isinstance(a, lt.save_resume_data_failed_alert):
                did = self._id_of(a.handle)
                self._saving.discard(did)
            elif isinstance(a, lt.metadata_received_alert):
                self._metadata(a.handle)
            elif isinstance(a, lt.metadata_failed_alert):
                for p in self.pending:
                    if p.handle == a.handle and p.on_error:
                        p.on_error(a.message())

    def _id_of(self, handle) -> str:
        for did, h in self.handles.items():
            if h == handle:
                return did
        return ""

    def _write_resume(self, a):
        did = self._id_of(a.handle)
        self._saving.discard(did)
        if not did:
            return
        os.makedirs(TORRENT_DIR, exist_ok=True)
        path = os.path.join(TORRENT_DIR, did + ".resume")
        try:
            data = lt.write_resume_data_buf(a.params)
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except (OSError, RuntimeError):
            pass

    def _metadata(self, handle):
        for p in list(self.pending):
            if p.handle == handle and p.ti is None:
                p.ti = handle.torrent_file()
                p.files = files_of(p.ti)
                if p.on_ready:
                    p.on_ready(p)


def ratio(d) -> float:
    base = max(d.total, 1)
    return d.uploaded / base
