-- Version 4 : métadonnées de prise de vue, miniature et aperçu (phase 4).
--
-- Une ligne par photo indexée. Les images générées sont recalculables : elles vivent
-- dans <racine>/.cadt/ et ne comptent pas comme sauvegarde.
-- error non NULL : l'indexation a échoué (la photo reste sauvegardée ; relance possible).

CREATE TABLE media (
    photo_id      INTEGER PRIMARY KEY REFERENCES photos (id),
    taken_at      TEXT,      -- DateTimeOriginal + fractions + décalage, ISO 8601
    make          TEXT,
    model         TEXT,
    lens          TEXT,
    serial        TEXT,
    focal_length  REAL,      -- mm
    aperture      REAL,      -- nombre f
    exposure_time REAL,      -- secondes
    iso           INTEGER,
    width         INTEGER,
    height        INTEGER,
    orientation   INTEGER,   -- valeur EXIF 1 à 8
    camera_rating INTEGER,   -- note donnée dans l'appareil
    shutter_count INTEGER,
    gps_lat       REAL,
    gps_lon       REAL,
    thumb_path    TEXT,
    preview_path  TEXT,
    indexed_at    TEXT    NOT NULL,
    error         TEXT
);

CREATE INDEX media_taken ON media (taken_at);
