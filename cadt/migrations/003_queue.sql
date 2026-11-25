-- Version 3 : file prioritaire avec délai entre les tentatives et abandon explicite.
--
-- next_attempt_at : une photo en échec n'est pas reprise avant cette heure (UTC, ISO 8601).
-- given_up_at     : la photo a échoué trop souvent ; elle sort de la file mais reste
--                   comptée comme non sauvegardée, jusqu'à une relance manuelle.
-- priority        : plus grand = plus tôt (ex. « télécharger celle-ci maintenant »).

ALTER TABLE photos ADD COLUMN next_attempt_at TEXT;
ALTER TABLE photos ADD COLUMN given_up_at TEXT;
ALTER TABLE photos ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;

DROP INDEX photos_queue;
CREATE INDEX photos_queue ON photos (state, given_up_at, priority, kind, capture_time);
