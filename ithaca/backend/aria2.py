"""aria2 : lancement du processus local et client JSON-RPC (sans dépendance).

Varia laissait l'interface RPC ouverte sans secret et avec --rpc-allow-origin-all : n'importe quelle
page web pouvait piloter son aria2 sur localhost (ajouter un téléchargement vers ~/.bashrc…). Ici :
écoute sur 127.0.0.1 seulement, secret aléatoire à chaque lancement, aucune origine autorisée.
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

# Codes d'erreur d'aria2 utiles (voir « EXIT STATUS » dans man aria2c)
ERR_FILE_EXISTS = "13"          # --allow-overwrite=false et --auto-file-renaming=false
ERR_NOT_FOUND = "3"


class RPCError(Exception):
    pass


def find_aria2c() -> str:
    return shutil.which("aria2c") or ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent():
    """Le noyau envoie SIGTERM à aria2 si Ithaca meurt (même tué net)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)      # 1 = PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


class Aria2:
    """Un aria2 joignable en JSON-RPC : local (start) ou distant (host / port / secret donnés)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, secret: str = "", scheme: str = "http"):
        self.host = host
        self.port = port
        self.secret = secret
        self.scheme = scheme
        self.process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}/jsonrpc"

    # -- processus local ----------------------------------------------------
    def start(self, options: dict, log_file: str = "", timeout: float = 8.0):
        """Lance aria2c avec ces options globales ; RPCError s'il ne répond pas."""
        exe = find_aria2c()
        if not exe:
            raise RPCError("aria2c introuvable")
        last = None
        for _attempt in range(3):            # port pris entre-temps : on en reprend un autre
            self.port = _free_port()
            self.secret = secrets.token_hex(16)
            args = [exe, "--enable-rpc", "--rpc-listen-all=false",
                    f"--rpc-listen-port={self.port}", f"--rpc-secret={self.secret}",
                    f"--stop-with-process={os.getpid()}"]
            if log_file:
                args += [f"--log={log_file}", "--log-level=warn"]
            args += [f"--{k}={v}" for k, v in options.items()]
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    self.call("aria2.getVersion", timeout=1)
                    return
                except RPCError as e:
                    last = e
                    time.sleep(0.05)
            self._kill()
        raise RPCError(str(last) if last else "aria2c ne répond pas")

    def stop(self, timeout: float = 3.0):
        """Arrêt propre (aria2 écrit ses fichiers de contrôle .aria2), puis forcé."""
        if self.process is None:
            return
        try:
            self.call("aria2.shutdown", timeout=1)
        except RPCError:
            pass
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
        self._kill()

    def _kill(self):
        p, self.process = self.process, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    # -- JSON-RPC --------------------------------------------------------------
    def _params(self, params) -> list:
        return ([f"token:{self.secret}"] if self.secret else []) + list(params)

    def _post(self, payload, timeout: float):
        req = urllib.request.Request(self.url, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # aria2 répond 400 avec le détail de l'erreur dans le corps
            try:
                return json.loads(e.read())
            except (ValueError, OSError):
                raise RPCError(str(e)) from e
        except (OSError, ValueError) as e:
            raise RPCError(str(e)) from e

    def call(self, method: str, *params, timeout: float = 10):
        data = self._post({"jsonrpc": "2.0", "id": "ithaca", "method": method,
                           "params": self._params(params)}, timeout)
        if "error" in data:
            raise RPCError(data["error"].get("message", "?"))
        return data.get("result")

    def multicall(self, calls: list[tuple], timeout: float = 10) -> list:
        """[(méthode, param…), …] en une requête ; chaque résultat, ou RPCError pour celui qui échoue."""
        payload = [{"jsonrpc": "2.0", "id": str(i), "method": c[0], "params": self._params(c[1:])}
                   for i, c in enumerate(calls)]
        data = self._post(payload, timeout)
        if isinstance(data, dict):
            raise RPCError(data.get("error", {}).get("message", "?"))
        out = [None] * len(calls)
        for item in data:
            i = int(item.get("id", 0))
            out[i] = RPCError(item["error"].get("message", "?")) if "error" in item else item.get("result")
        return out
