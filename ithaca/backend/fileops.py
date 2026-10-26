"""Opérations de fichiers sûres (reprises d'Odysseus) : ranger un résultat sans jamais écraser."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil

_RENAME_NOREPLACE = 1
_renameat2 = None


def _rename2(a: str, b: str, flags: int) -> bool:
    """renameat2 ; False si le système de fichiers ne le gère pas (FAT, certains FUSE, NFS…)."""
    global _renameat2
    if _renameat2 is None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            _renameat2 = libc.renameat2
            _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        except (OSError, AttributeError):
            _renameat2 = False
    if not _renameat2:
        return False
    at_fdcwd = -100
    if _renameat2(at_fdcwd, os.fsencode(a), at_fdcwd, os.fsencode(b), flags) == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
        return False
    raise OSError(err, os.strerror(err), b)


def rename_new(src: str, dst: str):
    """os.rename qui n'écrase jamais : FileExistsError si dst existe, même apparu à l'instant
    (renameat2 RENAME_NOREPLACE ; là où ce n'est pas géré, vérification juste avant)."""
    if _rename2(src, dst, _RENAME_NOREPLACE):
        return
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
    os.rename(src, dst)


def place_new(src: str, folder: str, name: str) -> str:
    """Range src dans folder sous name, ou « name (2) »… si le nom est pris ; renvoie le nom choisi.
    Autre disque (EXDEV) : copie sous un nom caché puis renommage, la source n'est retirée qu'après."""
    from .downloads import free_name
    taken: set = set()
    for _attempt in range(1000):
        candidate = free_name(folder, name, taken)
        dst = os.path.join(folder, candidate)
        try:
            rename_new(src, dst)
            return candidate
        except FileExistsError:
            taken.add(candidate)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            tmp = os.path.join(folder, f".{candidate}.ithaca-tmp")
            if os.path.isdir(src):
                shutil.copytree(src, tmp, symlinks=True)
            else:
                shutil.copy2(src, tmp)
            try:
                rename_new(tmp, dst)
            except FileExistsError:
                taken.add(candidate)
                (shutil.rmtree if os.path.isdir(tmp) else os.remove)(tmp)
                continue
            (shutil.rmtree if os.path.isdir(src) else os.remove)(src)
            return candidate
    raise FileExistsError(errno.EEXIST, "aucun nom libre", os.path.join(folder, name))
