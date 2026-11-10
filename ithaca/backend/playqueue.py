"""File de lecture : morceaux dans l'ordre d'ajout, ordre de lecture (mélangé ou non), morceau
courant, mode de répétition. Pure logique (pas de GStreamer, pas de GTK), enregistrée entre deux
lancements dans ~/.local/share/ithaca/queue.json.

L'ordre affiché est l'ordre de lecture : mélanger réordonne la liste, le morceau courant passe en
tête et le reste est tiré au hasard ; démélanger rend l'ordre d'ajout.
"""

from __future__ import annotations

import json
import os
import random
from typing import Callable, Optional

from .song import Song
from ..settings import DATA_DIR

QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
REPEAT_MODES = ("consecutive", "all", "one")


class PlayQueue:
    def __init__(self, rng: Optional[random.Random] = None):
        self.original: list[Song] = []      # ordre d'ajout
        self.items: list[Song] = []         # ordre de lecture (= affichage)
        self.pos = -1                       # morceau courant dans items, -1 : aucun
        self.repeat = "consecutive"
        self.shuffled = False
        self.rng = rng or random.Random()
        self._listeners: list[Callable[[str], None]] = []

    # -- écouteurs : « items » (liste changée), « current » (morceau courant), « modes » --------
    def connect(self, cb):
        self._listeners.append(cb)

    def _emit(self, what: str):
        for cb in list(self._listeners):
            cb(what)

    # -- état ---------------------------------------------------------------------------
    @property
    def current(self) -> Optional[Song]:
        return self.items[self.pos] if 0 <= self.pos < len(self.items) else None

    def __len__(self):
        return len(self.items)

    def total_duration(self) -> float:
        return sum(s.duration for s in self.items)

    # -- contenu ---------------------------------------------------------------------------
    def add(self, songs: list[Song]):
        """Ajoute à la fin (mélangé : dispersés au hasard après le morceau courant)."""
        known = {s.path for s in self.original}
        new = []
        for s in songs:
            if s.path not in known:          # un morceau n'est qu'une fois dans la file, comme Amberol
                known.add(s.path)
                new.append(s)
        if not new:
            return []
        self.original.extend(new)
        if self.shuffled:
            for s in new:
                self.items.insert(self.rng.randint(self.pos + 1, len(self.items)), s)
        else:
            self.items.extend(new)
        self._emit("items")
        return new

    def remove(self, songs: list[Song]):
        """Retire ; si le morceau courant part, le suivant prend sa place (sans changer de position)."""
        gone = {id(s) for s in songs}
        current = self.current
        current_gone = current is not None and id(current) in gone
        before = sum(1 for s in self.items[:max(self.pos, 0)] if id(s) in gone)
        self.original = [s for s in self.original if id(s) not in gone]
        self.items = [s for s in self.items if id(s) not in gone]
        if current is None:
            self.pos = -1
        elif current_gone:
            self.pos = min(self.pos - before, len(self.items) - 1)
            if not self.items:
                self.pos = -1
        else:
            self.pos = self.items.index(current)
        self._emit("items")
        if current_gone:
            self._emit("current")

    def clear(self):
        self.original, self.items, self.pos = [], [], -1
        self._emit("items")
        self._emit("current")

    def move(self, song: Song, new_index: int):
        """Déplace un morceau dans l'ordre de lecture (glisser dans la liste)."""
        current = self.current
        self.items.remove(song)
        self.items.insert(max(0, min(new_index, len(self.items))), song)
        if not self.shuffled:
            self.original = list(self.items)
        self.pos = self.items.index(current) if current is not None else -1
        self._emit("items")

    # -- déplacement -----------------------------------------------------------------------
    def set_current(self, index: int):
        self.pos = index if 0 <= index < len(self.items) else -1
        self._emit("current")

    def next_index(self) -> Optional[int]:
        """Morceau suivant selon le mode de répétition (None : fin de la file)."""
        if not self.items:
            return None
        if self.pos < 0:
            return 0
        if self.repeat == "one":
            return self.pos
        if self.pos + 1 < len(self.items):
            return self.pos + 1
        return 0 if self.repeat == "all" else None

    def previous_index(self) -> Optional[int]:
        if not self.items:
            return None
        if self.pos > 0:
            return self.pos - 1
        return len(self.items) - 1 if self.repeat == "all" else 0

    def advance(self) -> Optional[Song]:
        i = self.next_index()
        if i is None:
            return None
        self.set_current(i)
        return self.current

    def back(self) -> Optional[Song]:
        i = self.previous_index()
        if i is None:
            return None
        self.set_current(i)
        return self.current

    # -- modes --------------------------------------------------------------------------
    def set_repeat(self, mode: str):
        if mode in REPEAT_MODES and mode != self.repeat:
            self.repeat = mode
            self._emit("modes")

    def cycle_repeat(self) -> str:
        self.set_repeat(REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)])
        return self.repeat

    def set_shuffled(self, value: bool):
        if value == self.shuffled:
            return
        self.shuffled = value
        current = self.current
        if value:
            rest = [s for s in self.items if s is not current]
            self.rng.shuffle(rest)
            self.items = ([current] if current is not None else []) + rest
            self.pos = 0 if current is not None else -1
        else:
            self.items = list(self.original)
            self.pos = self.items.index(current) if current is not None else -1
        self._emit("items")
        self._emit("modes")

    # -- persistance ---------------------------------------------------------------------
    def save(self, position: float = 0.0, path: str = QUEUE_FILE):
        data = {"version": 1, "songs": [s.path for s in self.original],
                "titles": {s.path: s.title for s in self.original if s.stream and s.title},
                "logos": {s.path: s.logo for s in self.original if s.stream and s.logo},
                "sites": {s.path: s.site for s in self.original if s.stream and s.site},
                "order": [self.original.index(s) for s in self.items] if self.shuffled else None,
                "current": self.current.path if self.current else "", "position": position,
                "repeat": self.repeat, "shuffled": self.shuffled}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass


def load_saved(path: str = QUEUE_FILE) -> Optional[dict]:
    """File enregistrée (chemins encore présents seulement), None s'il n'y en a pas."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("songs"):
        return None
    data["songs"] = [p for p in data["songs"] if isinstance(p, str) and ("://" in p or os.path.isfile(p))]
    return data if data["songs"] else None


def restore(queue: PlayQueue, data: dict, songs: list[Song]):
    """Remet la file enregistrée (songs : morceaux relus, dans l'ordre de data["songs"])."""
    by_path = {s.path: s for s in songs}
    original = [by_path[p] for p in data["songs"] if p in by_path]
    queue.original = original
    queue.shuffled = bool(data.get("shuffled"))
    order = data.get("order")
    if queue.shuffled and isinstance(order, list) and sorted(order) == list(range(len(data["songs"]))):
        paths = [data["songs"][i] for i in order]
        queue.items = [by_path[p] for p in paths if p in by_path]
    else:
        queue.items = list(original)
    queue.repeat = data.get("repeat") if data.get("repeat") in REPEAT_MODES else "consecutive"
    cur = data.get("current")
    queue.pos = next((i for i, s in enumerate(queue.items) if s.path == cur), -1)
    queue._emit("items")
    queue._emit("modes")
    queue._emit("current")
