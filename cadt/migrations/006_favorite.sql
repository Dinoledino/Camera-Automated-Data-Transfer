-- Version 6 : favori (remplace les notes en étoiles, écartées par l'utilisateur le 2026-11-25).

ALTER TABLE photos ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1));
