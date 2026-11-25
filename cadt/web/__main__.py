"""Lance l'interface :  python -m cadt.web --db cadt.db --dest MÉMOIRE_INTERNE [--dest MICROSD]

Puis ouvrir http://127.0.0.1:8000 dans le navigateur du téléphone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .. import exif
from ..storage import GB, StoragePool
from .app import WebConfig, create_app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Interface de tri des photos.")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--dest", type=Path, required=True, action="append",
                   help="emplacements de sauvegarde, dans l'ordre (comme pour le watcher)")
    p.add_argument("--reserve-gb", type=float, default=2.0)
    p.add_argument("--host", default="127.0.0.1",
                   help="adresse d'écoute (127.0.0.1 : le téléphone seulement)")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    pool = StoragePool(args.dest, reserve_bytes=int(args.reserve_gb * GB))
    factory = None
    if exif.available():
        from ..indexer import Indexer
        et = exif.ExifTool()
        factory = lambda db: Indexer(db, pool.primary, et)  # noqa: E731
    app = create_app(WebConfig(args.db, pool, factory))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
