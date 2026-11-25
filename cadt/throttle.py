"""Source bridée : limite le débit de lecture, pour simuler un lien USB / PTP lent.

Débit visé en Mo/s (1 Mo = 1 048 576 octets), plus un délai fixe par fichier qui imite
le coût des échanges PTP avant le transfert (GetObjectInfo, ouverture…).
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

from .downloader import Source

MB = 1024 * 1024


class ThrottledSource:
    def __init__(self, inner: Source, mb_per_s: float, per_file_latency: float = 0.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if mb_per_s <= 0:
            raise ValueError("débit strictement positif attendu")
        self.inner = inner
        self.bytes_per_s = mb_per_s * MB
        self.per_file_latency = per_file_latency
        self.clock = clock
        self.sleep = sleep

    def iter_chunks(self, folder: str, name: str) -> Iterator[bytes]:
        if self.per_file_latency:
            self.sleep(self.per_file_latency)
        start, sent = self.clock(), 0
        for chunk in self.inner.iter_chunks(folder, name):
            sent += len(chunk)
            ahead = start + sent / self.bytes_per_s - self.clock()
            if ahead > 0:
                self.sleep(ahead)
            yield chunk
