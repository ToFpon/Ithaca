"""Listes de lecture : lire .m3u / .m3u8 / .xspf / .pls, écrire .m3u / .m3u8 / .xspf, compléter une
liste existante. Repris du module d'Odysseus (backend/playlist.py) :

- Chemins RELATIFS au dossier de la liste : copiée avec la musique (clé, téléphone, autre PC), elle
  marche toujours. En lecture : relatifs, absolus, file://, chemins Windows (« \\ »), adresses de flux
  (webradios) gardées telles quelles.
- M3U étendu (#EXTM3U, #EXTINF:durée,Artiste - Titre) d'après les étiquettes déjà lues par Ithaca ;
  .m3u et .m3u8 écrits en UTF-8 (comme VLC). XSPF : titre, artiste, album, durée (ms), adresse
  relative encodée.
- Écriture sûre : copie complète sous un nom caché dans le même dossier, mise sur disque, puis
  remplacement (l'ancienne liste reste si ça échoue). Compléter une liste garde son contenu tel quel
  et ignore les morceaux déjà présents (comparés par chemin réel).
"""

from __future__ import annotations

import os
import re
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

READ_EXTS = (".m3u", ".m3u8", ".xspf", ".pls")
WRITE_EXTS = (".m3u", ".m3u8", ".xspf")
XSPF_NS = "http://xspf.org/ns/0/"


@dataclass
class Entry:
    location: str               # chemin local absolu, ou adresse de flux
    title: str = ""             # titre donné par la liste (#EXTINF, <title>, TitleN)
    duration: float = 0.0


def is_playlist(path: str) -> bool:
    return path.lower().endswith(READ_EXTS)


def is_stream(location: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", location)) and not location.startswith("file://")


# -- lecture ----------------------------------------------------------------------------------
def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(20_000_000)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):          # vieux .m3u de Windows : cp1252
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _resolve(location: str, folder: str) -> str:
    location = location.strip()
    if location.startswith("file://"):
        from gi.repository import Gio
        return Gio.File.new_for_uri(location).get_path() or ""
    if is_stream(location):
        return location
    location = location.replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", location):                  # C:/Musique/… : rien de tel ici
        return ""
    return os.path.normpath(os.path.join(folder, location))


def parse(path: str) -> list[Entry]:
    """Entrées d'une liste (ordre de la liste). Liste illisible : []."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if path.lower().endswith(".xspf"):
            return _parse_xspf(path, folder)
        text = _read_text(path)
    except (OSError, ET.ParseError):
        return []
    if path.lower().endswith(".pls"):
        return _parse_pls(text, folder)
    return _parse_m3u(text, folder)


def _parse_m3u(text: str, folder: str) -> list[Entry]:
    out, title, duration = [], "", 0.0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            head, _, title = line[8:].partition(",")
            try:
                duration = max(0.0, float(head.split()[0])) if head.strip() else 0.0
            except ValueError:
                duration = 0.0
            title = title.strip()
            continue
        if line.startswith("#"):
            continue
        location = _resolve(line, folder)
        if location:
            out.append(Entry(location, title, duration))
        title, duration = "", 0.0
    return out


def _parse_pls(text: str, folder: str) -> list[Entry]:
    files, titles, lengths = {}, {}, {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        m = re.match(r"^(File|Title|Length)(\d+)$", key.strip(), re.IGNORECASE)
        if not m:
            continue
        kind, n = m.group(1).lower(), int(m.group(2))
        {"file": files, "title": titles, "length": lengths}[kind][n] = value.strip()
    out = []
    for n in sorted(files):
        location = _resolve(files[n], folder)
        if not location:
            continue
        try:
            duration = max(0.0, float(lengths.get(n, 0)))
        except ValueError:
            duration = 0.0
        out.append(Entry(location, titles.get(n, ""), duration))
    return out


def _parse_xspf(path: str, folder: str) -> list[Entry]:
    root = ET.parse(path).getroot()
    ns = {"x": XSPF_NS} if root.tag.startswith("{") else {}
    q = (lambda tag: f"x:{tag}") if ns else (lambda tag: tag)
    base = "file://" + urllib.parse.quote(folder.rstrip("/") + "/")
    out = []
    for track in root.iterfind(f"{q('trackList')}/{q('track')}", ns):
        loc = track.findtext(q("location"), default="", namespaces=ns).strip()
        if not loc:
            continue
        if not is_stream(loc):
            loc = urllib.parse.urljoin(base, loc)         # adresse relative au fichier de la liste
        location = _resolve(loc, folder)
        if not location:
            continue
        title = track.findtext(q("title"), default="", namespaces=ns).strip()
        creator = track.findtext(q("creator"), default="", namespaces=ns).strip()
        try:
            duration = int(track.findtext(q("duration"), default="0", namespaces=ns)) / 1000
        except ValueError:
            duration = 0.0
        out.append(Entry(location, f"{creator} - {title}" if creator and title else title, duration))
    return out


# -- écriture -----------------------------------------------------------------------------------
def _entry_path(location: str, folder: str) -> str:
    return location if is_stream(location) else os.path.relpath(location, folder)


def _label(song) -> str:
    title = song.title or os.path.splitext(os.path.basename(song.path))[0]
    label = f"{song.artist} - {title}" if song.artist and song.title else title
    return " ".join(label.split())                          # jamais de retour à la ligne


def _m3u_lines(songs, folder: str) -> list[str]:
    out = []
    for s in songs:
        seconds = int(round(s.duration)) if s.duration else -1
        out += [f"#EXTINF:{seconds},{_label(s)}", _entry_path(s.path, folder)]
    return out


def _xspf_track(s, folder: str) -> ET.Element:
    t = ET.Element("track")
    loc = s.path if is_stream(s.path) else urllib.parse.quote(_entry_path(s.path, folder))
    ET.SubElement(t, "location").text = loc
    if s.title or s.stream:
        ET.SubElement(t, "title").text = s.title or s.display_title
    if s.artist:
        ET.SubElement(t, "creator").text = s.artist
    if s.album:
        ET.SubElement(t, "album").text = s.album
    if s.duration:
        ET.SubElement(t, "duration").text = str(int(s.duration * 1000))
    return t


def _xspf_text(root: ET.Element) -> str:
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def render(songs, path: str) -> str:
    """Texte d'une nouvelle liste (format d'après l'extension de path)."""
    folder = os.path.dirname(os.path.abspath(path))
    if path.lower().endswith(".xspf"):
        root = ET.Element("playlist", {"version": "1", "xmlns": XSPF_NS})
        tracks = ET.SubElement(root, "trackList")
        for s in songs:
            tracks.append(_xspf_track(s, folder))
        return _xspf_text(root)
    return "#EXTM3U\n" + "\n".join(_m3u_lines(songs, folder)) + "\n"


def with_extension(path: str) -> str:
    """Nom choisi sans extension de liste connue : « .m3u » ajouté."""
    return path if path.lower().endswith(WRITE_EXTS) else path + ".m3u"


def _write_safely(path: str, text: str):
    """Copie complète sous un nom caché, mise sur disque, puis remplacement (et dossier sur disque)."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".ithaca-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            import shutil
            shutil.copymode(path, tmp)
        except OSError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        try:
            dfd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def free_name(path: str) -> str:
    """« nom (2).m3u », « nom (3).m3u »… : premier nom libre."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def save(songs, path: str) -> str:
    """Nouvelle liste ; renvoie son chemin. Remplacer un fichier n'arrive que si la fenêtre
    d'enregistrement l'a fait confirmer sous CE nom : l'extension ajoutée par nous (« Ma liste » →
    « Ma liste.m3u ») ne doit jamais écraser un fichier existant."""
    full = with_extension(path)
    if full != path:
        full = free_name(full)
    _write_safely(full, render(songs, full))
    return full


def _key(location: str) -> str:
    return location if is_stream(location) else os.path.realpath(location)


def append(songs, path: str) -> tuple[int, int]:
    """Ajoute à la fin de la liste les morceaux qui n'y sont pas déjà ; renvoie (ajoutés, déjà là).
    Le contenu existant est gardé tel quel (commentaires, flux, chemins absolus)."""
    present = {_key(e.location) for e in parse(path)}
    new = [s for s in songs if _key(s.path) not in present]
    if not new:
        return 0, len(songs)
    folder = os.path.dirname(os.path.abspath(path))
    if path.lower().endswith(".xspf"):
        tree = ET.parse(path)
        root = tree.getroot()
        prefix = "{%s}" % XSPF_NS if root.tag.startswith("{") else ""
        if prefix:
            ET.register_namespace("", XSPF_NS)
        tracks = root.find(f"{prefix}trackList")
        if tracks is None:
            tracks = ET.SubElement(root, f"{prefix}trackList")
        for s in new:
            t = _xspf_track(s, folder)
            if prefix:
                for el in t.iter():
                    el.tag = prefix + el.tag
            tracks.append(t)
        text = _xspf_text(root)
    else:
        old = _read_text(path)
        if not old.strip():
            text = render(new, path)
        else:
            extended = old.lstrip("\ufeff").startswith("#EXTM3U")
            lines = _m3u_lines(new, folder) if extended else [_entry_path(s.path, folder) for s in new]
            text = old + ("" if old.endswith("\n") else "\n") + "\n".join(lines) + "\n"
    _write_safely(path, text)
    return len(new), len(songs) - len(new)


def title_for_stream(location: str, hint: str = "") -> str:
    """Nom affiché d'une webradio : celui de la liste, sinon le nom du serveur."""
    if hint:
        return hint
    host = urllib.parse.urlsplit(location).hostname or location
    return host

