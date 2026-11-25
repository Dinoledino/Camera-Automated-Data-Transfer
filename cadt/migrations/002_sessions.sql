-- Version 2 : sessions de prise de vue.
--
-- Une session est un dossier sur la microSD : <day>_<slug>, ex. 2026-11-23_Mariage-Dupont.
-- Les photos détectées pendant qu'une session est active y sont rangées et nommées
-- <day>_<slug>_<numéro>.<ext>. Le RAW et le JPEG d'une même prise partagent le numéro.

CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,             -- tel que saisi : « Mariage Dupont »
    slug        TEXT    NOT NULL,             -- pour les noms de fichiers : « Mariage-Dupont »
    day         TEXT    NOT NULL,             -- date locale de création, AAAA-MM-JJ
    folder      TEXT    NOT NULL UNIQUE,      -- dossier relatif à la racine de la microSD
    next_number INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL              -- UTC, ISO 8601
);

-- Petites valeurs globales, ex. active_session_id.
CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

ALTER TABLE photos ADD COLUMN session_id INTEGER REFERENCES sessions (id);
ALTER TABLE photos ADD COLUMN session_number INTEGER;

CREATE INDEX photos_session ON photos (session_id, session_number);

-- Déplacement d'une photo sauvegardée vers une autre session, en trois temps :
-- 1. on enregistre l'intention ici ; 2. on renomme le fichier ; 3. on met à jour la photo
-- et on efface la ligne. Au redémarrage, une ligne restante est terminée ou annulée
-- selon l'endroit où se trouve le fichier.
CREATE TABLE pending_moves (
    photo_id       INTEGER PRIMARY KEY REFERENCES photos (id),
    src            TEXT    NOT NULL,
    dst            TEXT    NOT NULL,
    session_id     INTEGER REFERENCES sessions (id),
    session_number INTEGER,
    created_at     TEXT    NOT NULL
);
