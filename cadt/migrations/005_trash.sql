-- Version 5 : corbeille du téléphone (rejeter = supprimer, en trois temps).
--
-- trashed_at   : mise à la corbeille (UTC) ; NULL hors corbeille.
-- trash_origin : chemin d'origine, pour la récupération.
-- trash_boot   : identifiant de démarrage du téléphone au moment de la mise à la corbeille ;
--                un démarrage différent signifie que le téléphone s'est éteint entre-temps.
-- purged_at    : supprimée définitivement du téléphone (le fichier n'existe plus).

ALTER TABLE photos ADD COLUMN trashed_at TEXT;
ALTER TABLE photos ADD COLUMN trash_origin TEXT;
ALTER TABLE photos ADD COLUMN trash_boot TEXT;
ALTER TABLE photos ADD COLUMN purged_at TEXT;

CREATE INDEX photos_trash ON photos (trashed_at) WHERE trashed_at IS NOT NULL;

-- Garde-fou : une photo supprimée du téléphone ne revient jamais en corbeille ni en galerie.
CREATE TRIGGER photos_purge_is_final
BEFORE UPDATE OF purged_at ON photos
WHEN OLD.purged_at IS NOT NULL AND NEW.purged_at IS NOT OLD.purged_at
BEGIN
    SELECT RAISE(ABORT, 'purged_at is final');
END;
