-- Schéma SQLite, version 1.
-- Appliqué par cadt/db.py ; la version est suivie par PRAGMA user_version.
--
-- Cycle de vie d'une photo (colonne state) :
--   detected → downloading → downloaded → verified → synced_pc
--   - downloaded : .part écrit et fsync, SHA-256 calculé pendant le téléchargement
--   - verified   : .part relu depuis la microSD, SHA-256 identique, renommé en fichier final
-- La suppression côté appareil suit un cycle séparé (colonne delete_state) :
--   none → delete_requested → deleted_on_camera
-- Les triggers ci-dessous sont un garde-fou : même un bug dans le code Python
-- ne peut pas marquer « vérifiée » une photo sans hash, ni demander la suppression
-- d'une photo qui n'est pas vérifiée.

CREATE TABLE photos (
    id              INTEGER PRIMARY KEY,
    -- Identité côté appareil
    camera_folder   TEXT    NOT NULL,             -- ex. /store_00010001/DCIM/100NC850
    name            TEXT    NOT NULL,             -- ex. DSC_0001.NEF
    size            INTEGER NOT NULL CHECK (size >= 0),
    capture_time    TEXT    NOT NULL,             -- heure locale de l'appareil, ISO 8601 sans fuseau
    storage_id      TEXT,                         -- carte d'origine (XQD / SD), informatif
    kind            TEXT    NOT NULL CHECK (kind IN ('raw', 'jpeg', 'other')),
    group_key       TEXT    NOT NULL,             -- regroupe RAW et JPEG d'une même prise
    -- Sauvegarde
    state           TEXT    NOT NULL DEFAULT 'detected'
                    CHECK (state IN ('detected', 'downloading', 'downloaded', 'verified', 'synced_pc')),
    sha256          TEXT    CHECK (sha256 IS NULL OR (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')),
    local_path      TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    -- Tri
    rating          INTEGER NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
    flag            TEXT    NOT NULL DEFAULT 'none' CHECK (flag IN ('none', 'keep', 'reject')),
    -- Suppression côté appareil
    delete_state    TEXT    NOT NULL DEFAULT 'none'
                    CHECK (delete_state IN ('none', 'delete_requested', 'deleted_on_camera')),
    -- Horodatage (UTC, ISO 8601)
    detected_at     TEXT    NOT NULL,
    verified_at     TEXT,
    synced_at       TEXT,
    updated_at      TEXT    NOT NULL,

    -- Le D850 recommence à DSC_0001 après 9999 : un fichier est identifié
    -- par (nom, taille, date de prise de vue), pas par son nom seul.
    UNIQUE (name, size, capture_time)
);

CREATE INDEX photos_queue ON photos (state, kind, capture_time);
CREATE INDEX photos_group ON photos (group_key);
CREATE INDEX photos_sha   ON photos (sha256);

CREATE TABLE events (
    id        INTEGER PRIMARY KEY,
    ts        TEXT    NOT NULL,                  -- UTC, ISO 8601
    photo_id  INTEGER REFERENCES photos (id),
    kind      TEXT    NOT NULL,                  -- ex. detected, download_started, verified, error
    message   TEXT,
    data      TEXT                               -- JSON libre
);

CREATE INDEX events_photo ON events (photo_id);
CREATE INDEX events_ts    ON events (ts);

-- Garde-fou 1 : un état « verified » ou au-delà exige un hash et un chemin local.
CREATE TRIGGER photos_verified_requires_hash
BEFORE UPDATE OF state ON photos
WHEN NEW.state IN ('verified', 'synced_pc')
 AND (NEW.sha256 IS NULL OR NEW.local_path IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'verified requires sha256 and local_path');
END;

-- Garde-fou 2 : le hash d'une photo vérifiée ne change plus.
CREATE TRIGGER photos_hash_immutable_after_verify
BEFORE UPDATE OF sha256 ON photos
WHEN OLD.state IN ('verified', 'synced_pc') AND NEW.sha256 IS NOT OLD.sha256
BEGIN
    SELECT RAISE(ABORT, 'sha256 is immutable once verified');
END;

-- Garde-fou 3 : une photo vérifiée ne redescend jamais à un état antérieur.
CREATE TRIGGER photos_no_downgrade_after_verify
BEFORE UPDATE OF state ON photos
WHEN OLD.state IN ('verified', 'synced_pc')
 AND NEW.state NOT IN ('verified', 'synced_pc')
BEGIN
    SELECT RAISE(ABORT, 'cannot downgrade a verified photo');
END;

-- Garde-fou 4 (règle 2 de CLAUDE.md) : suppression côté appareil uniquement
-- pour une photo déjà sauvegardée et vérifiée.
CREATE TRIGGER photos_delete_requires_verified
BEFORE UPDATE OF delete_state ON photos
WHEN NEW.delete_state <> 'none'
 AND NEW.state NOT IN ('verified', 'synced_pc')
BEGIN
    SELECT RAISE(ABORT, 'camera deletion requires a verified photo');
END;

-- Garde-fou 5 : on ne passe pas à deleted_on_camera sans être passé par delete_requested.
CREATE TRIGGER photos_delete_order
BEFORE UPDATE OF delete_state ON photos
WHEN NEW.delete_state = 'deleted_on_camera'
 AND OLD.delete_state NOT IN ('delete_requested', 'deleted_on_camera')
BEGIN
    SELECT RAISE(ABORT, 'deleted_on_camera requires delete_requested first');
END;
