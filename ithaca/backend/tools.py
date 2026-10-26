"""Programmes gérés par Ithaca : yt-dlp (binaire officiel) et Deno (moteur JavaScript que yt-dlp
utilise pour YouTube). Celui d'apt est périmé et casse vite face à YouTube : Ithaca installe les
binaires publiés sur GitHub dans ~/.local/share/ithaca/bin et les tient à jour.

Sécurité : chaque fichier téléchargé est comparé à la somme SHA-256 publiée avec la version
(SHA2-256SUMS de yt-dlp, .sha256sum de Deno) avant d'être installé ; sinon rien ne change.
Mise à jour : l'empreinte publiée diffère de celle installée (fichier « nom.sha256 » à côté du
programme) — pas besoin de l'API GitHub, limitée en nombre de requêtes.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

from ..settings import DATA_DIR

BIN_DIR = os.environ.get("ITHACA_BIN_DIR") or os.path.join(DATA_DIR, "bin")
CHUNK = 256 * 1024


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    id: str
    name: str               # nom affiché
    url: str                # fichier publié (binaire ou archive zip)
    sums: str               # fichier des sommes publiées
    asset: str              # nom du fichier publié dans les sommes
    member: str = ""        # programme dans l'archive zip (vide : le fichier est le programme)
    size_hint: int = 0      # taille approximative, pour le message d'installation


def _arch() -> str:
    m = platform.machine().lower()
    return "aarch64" if m in ("aarch64", "arm64") else "x86_64"


def _tools() -> dict[str, Tool]:
    arch = _arch()
    yt_asset = "yt-dlp_linux" if arch == "x86_64" else "yt-dlp_linux_aarch64"
    deno_asset = f"deno-{arch}-unknown-linux-gnu.zip"
    yt = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
    deno = "https://github.com/denoland/deno/releases/latest/download/"
    return {
        "yt-dlp": Tool("yt-dlp", "yt-dlp", yt + yt_asset, yt + "SHA2-256SUMS", yt_asset, size_hint=40_000_000),
        "deno": Tool("deno", "Deno", deno + deno_asset, deno + deno_asset + ".sha256sum", deno_asset,
                     member="deno", size_hint=42_000_000),
    }


TOOLS = _tools()
_lock = threading.Lock()            # une installation à la fois
_versions: dict[str, str] = {}


def path(tid: str) -> str:
    """Chemin du programme installé, vide s'il n'est pas là."""
    p = os.path.join(BIN_DIR, tid)
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else ""


def installed(tid: str) -> bool:
    return bool(path(tid))


def version(tid: str) -> str:
    """Version installée (mise en cache), vide si absent ou illisible."""
    p = path(tid)
    if not p:
        return ""
    key = f"{p}:{os.stat(p).st_mtime_ns}"
    if key in _versions:
        return _versions[key]
    try:
        out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    line = out.strip().splitlines()[0] if out.strip() else ""
    if tid == "deno" and line.startswith("deno "):
        line = line.split()[1]
    _versions[key] = line
    return line


def _open(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "Ithaca"})
    return urllib.request.urlopen(req, timeout=timeout)


def published_sha(tool: Tool) -> str:
    """Somme SHA-256 publiée pour la dernière version."""
    try:
        with _open(tool.sums) as r:
            text = r.read(1_000_000).decode("utf-8", "replace")
    except OSError as e:
        raise ToolError(str(e)) from e
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == tool.asset:
            return parts[0].lower()
        if len(parts) == 1 and len(parts[0]) == 64:          # fichier .sha256sum réduit à la somme
            return parts[0].lower()
    raise ToolError(f"somme introuvable pour {tool.asset}")


def installed_sha(tid: str) -> str:
    try:
        with open(os.path.join(BIN_DIR, tid + ".sha256"), encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def update_available(tid: str) -> bool:
    """Une autre version est publiée (ToolError si GitHub est injoignable)."""
    if not installed(tid):
        return True
    return published_sha(TOOLS[tid]) != installed_sha(tid)


def install(tid: str, progress: Optional[Callable[[int, int], None]] = None,
            cancel: Optional[threading.Event] = None) -> str:
    """Télécharge, vérifie et installe la dernière version ; renvoie la version installée.
    Bloquant : à appeler dans un fil. progress(reçu, total) est appelé depuis ce fil."""
    tool = TOOLS[tid]
    with _lock:
        os.makedirs(BIN_DIR, exist_ok=True)
        expected = published_sha(tool)
        tmp = os.path.join(BIN_DIR, f".{tid}.download")
        digest = hashlib.sha256()
        try:
            with _open(tool.url, timeout=60) as r, open(tmp, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise ToolError("annulé")
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
            if digest.hexdigest() != expected:
                raise ToolError(f"somme SHA-256 incorrecte pour {tool.asset}")
            exe = os.path.join(BIN_DIR, f".{tid}.new")
            if tool.member:
                with zipfile.ZipFile(tmp) as z, z.open(tool.member) as src, open(exe, "wb") as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
            else:
                os.replace(tmp, exe)
            os.chmod(exe, 0o755)
            os.replace(exe, os.path.join(BIN_DIR, tid))
            with open(os.path.join(BIN_DIR, tid + ".sha256"), "w", encoding="ascii") as fh:
                fh.write(expected + "\n")
        except OSError as e:
            raise ToolError(str(e)) from e
        except zipfile.BadZipFile as e:
            raise ToolError(str(e)) from e
        finally:
            for leftover in (tmp, os.path.join(BIN_DIR, f".{tid}.new")):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    return version(tid)


def missing() -> list[str]:
    return [tid for tid in TOOLS if not installed(tid)]
