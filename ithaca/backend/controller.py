"""Le lecteur et sa file ensemble (boucle GLib, sans GTK : testable seul).

Événements envoyés aux écouteurs : song (morceau courant changé), state (lecture / pause),
position, queue (contenu ou ordre de la file), modes (aléatoire, répétition), error.
"""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import GLib

from .player import Player
from .playqueue import PlayQueue, load_saved, restore
from .song import Song, load_paths
from ..settings import settings

RESTART_AFTER = 3.0          # « Précédent » après 3 s : retour au début du morceau
SAVE_EVERY_MS = 5000


class Controller:
    def __init__(self, queue_file: str = ""):
        self.queue = PlayQueue()
        self.queue.repeat = settings["repeat"]
        self.queue.shuffled = bool(settings["shuffle"])
        self.queue.connect(self._on_queue)
        self.player = Player()
        self.player.set_volume(settings["volume"], settings["muted"])
        self.player.set_replaygain(settings["replaygain"])
        self.player.on_state = lambda playing: self._emit("state")
        self.player.on_position = lambda pos: self._emit("position", pos)
        self.player.on_song_started = self._on_song_started
        self.player.on_ended = self._on_ended
        self.player.on_error = self._on_error
        self.queue_file = queue_file
        self.loading = 0
        self._errors = 0
        self._listeners: list[Callable] = []
        self._save_timer = 0
        self._autosave = GLib.timeout_add(SAVE_EVERY_MS, self._periodic_save)
        settings.connect(self._on_setting)

    # -- écouteurs --------------------------------------------------------------------------
    def connect(self, cb: Callable):
        self._listeners.append(cb)

    def disconnect(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _emit(self, event: str, *args):
        for cb in list(self._listeners):
            cb(event, *args)

    @property
    def song(self) -> Optional[Song]:
        return self.queue.current

    @property
    def playing(self) -> bool:
        return self.player.playing

    # -- ajout ------------------------------------------------------------------------------
    def add_paths(self, paths: list[str], play: bool = False, on_done: Optional[Callable] = None):
        """Ajoute fichiers et dossiers (lus dans un fil). play : lancer le premier morceau ajouté."""
        state = {"first": None}
        self.loading += 1
        self._emit("loading")

        def got(songs):
            new = self.queue.add(songs)
            if new and state["first"] is None:
                state["first"] = new[0]
                if play or (self.queue.current is None and not self.player.playing):
                    self.play_song(new[0], play=play)

        def done(read, skipped):
            self.loading -= 1
            self._emit("loading")
            self.save_soon()
            if on_done:
                on_done(read, skipped)
        load_paths(paths, got, done)

    def restore_saved(self, on_done: Optional[Callable] = None) -> bool:
        """Remet la file enregistrée, sur le morceau et à la position où on l'avait laissée (en pause)."""
        data = load_saved(self.queue_file) if self.queue_file else load_saved()
        if data is None:
            return False
        read: list[Song] = []
        self.loading += 1
        self._emit("loading")

        def got(songs):
            read.extend(songs)

        def done(_n, _skipped):
            self.loading -= 1
            restore(self.queue, data, read)
            settings["shuffle"] = self.queue.shuffled
            settings["repeat"] = self.queue.repeat
            if self.queue.current is not None:
                self.player.load(self.queue.current, play=False, position=float(data.get("position") or 0))
                self._prepare_next()
            self._emit("loading")
            if on_done:
                on_done()
        load_paths(data["songs"], got, done)
        return True

    # -- commandes --------------------------------------------------------------------------
    def play_song(self, song: Song, play: bool = True):
        if song not in self.queue.items:
            return
        self.queue.set_current(self.queue.items.index(song))
        self.player.load(song, play=play)
        self._prepare_next()

    def play_index(self, index: int):
        if 0 <= index < len(self.queue):
            self.play_song(self.queue.items[index])

    def toggle(self):
        if self.player.song is None:
            if self.queue.current is not None:
                self.play_song(self.queue.current)
            elif len(self.queue):
                self.play_index(0)
            return
        self.player.toggle()

    def play(self):
        if not self.player.playing:
            self.toggle()

    def pause(self):
        if self.player.playing:
            self.player.pause()

    def next(self):
        i = self.queue.next_index()
        if self.queue.repeat == "one" and len(self.queue) > 1:       # « Suivant » change de morceau
            i = (self.queue.pos + 1) % len(self.queue)
        if i is not None:
            self.play_song(self.queue.items[i], play=self.player.playing or self.player.song is None)

    def previous(self):
        if self.player.position() > RESTART_AFTER:
            self.player.seek(0)
            return
        i = self.queue.previous_index()
        if i is not None:
            self.play_song(self.queue.items[i], play=self.player.playing or self.player.song is None)

    def seek(self, seconds: float):
        self.player.seek(seconds)

    def remove(self, songs: list[Song]):
        was_current = self.queue.current
        self.queue.remove(songs)
        if was_current is not None and was_current in songs:
            if self.queue.current is not None:
                self.player.load(self.queue.current, play=self.player.playing)
            else:
                self.player.stop()
        self.save_soon()

    def clear(self):
        self.player.stop()
        self.queue.clear()
        self.save_soon()

    def set_shuffled(self, value: bool):
        self.queue.set_shuffled(value)
        settings["shuffle"] = value

    def cycle_repeat(self) -> str:
        mode = self.queue.cycle_repeat()
        settings["repeat"] = mode
        return mode

    def set_volume(self, cubic: float):
        settings["volume"] = round(max(0.0, min(1.0, cubic)), 3)

    def set_muted(self, muted: bool):
        settings["muted"] = muted

    # -- réactions --------------------------------------------------------------------------
    def _on_setting(self, key, value):
        if key in ("volume", "muted"):
            self.player.set_volume(settings["volume"], settings["muted"])
            self._emit("volume")
        elif key == "replaygain":
            self.player.set_replaygain(value)

    def _on_queue(self, what: str):
        if what == "items":
            self._emit("queue")
            self._prepare_next()
            self.save_soon()
        elif what == "current":
            self._emit("song")
        elif what == "modes":
            self._prepare_next()
            self._emit("modes")

    def _prepare_next(self):
        """Morceau suivant donné d'avance au lecteur, pour l'enchaîner sans blanc."""
        i = self.queue.next_index()
        self.player.next_song = self.queue.items[i] if i is not None and self.queue.current else None

    def _on_song_started(self, song: Song):
        self._errors = 0
        if song in self.queue.items:
            self.queue.set_current(self.queue.items.index(song))
        self._prepare_next()

    def _on_ended(self):
        """Fin de la file (aucun suivant) : arrêt, retour au début du dernier morceau."""
        self._emit("position", 0.0)
        self._emit("ended")

    def _on_error(self, song: Optional[Song], message: str):
        self._emit("error", song, message)
        self._errors += 1
        if self._errors > len(self.queue):         # tout est illisible : on s'arrête
            return
        if song is not None and song in self.queue.items:
            self.queue.set_current(self.queue.items.index(song))
        i = self.queue.next_index()
        if i is not None and self.queue.items[i] is not song:
            self.play_song(self.queue.items[i])

    # -- enregistrement ---------------------------------------------------------------------
    def save(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        args = (self.player.position(),) + ((self.queue_file,) if self.queue_file else ())
        self.queue.save(*args)

    def save_soon(self):
        if not self._save_timer:
            self._save_timer = GLib.timeout_add(700, self._save_timeout)

    def _save_timeout(self):
        self._save_timer = 0
        self.save()
        return False

    def _periodic_save(self):
        if self.player.playing:
            self.save()
        return True

    def shutdown(self):
        self.save()
        if self._autosave:
            GLib.source_remove(self._autosave)
            self._autosave = 0
        settings.disconnect(self._on_setting)
        self.player.shutdown()
