# Phase 1 : mesures de base

Script : `bench/baseline.py` (relançable sur n'importe quel système et support).
Résultats bruts : `bench/resultats/`.

## A50, One UI d'origine (Android 11), Termux, mémoire interne — 2026-11-25

Exynos 9610 (4 × 2,3 GHz + 4 × 1,7 GHz), 3,6 Go de RAM visibles. Python 3.14, exiftool 13.64, Pillow 13.0.
**Pas de microSD dans le téléphone** : les mesures de stockage portent sur la mémoire interne (UFS),
bien plus rapide qu'une microSD. À refaire avec la carte achetée.

| Mesure | A50 | PC (référence, en RAM) |
|---|---|---|
| Écriture séquentielle 1 Go, avec fsync | 188 Mo/s | — |
| SHA-256 en mémoire | **1 147 Mo/s** | 541 Mo/s |
| Copie vérifiée d'un NEF de 53 Mo, sans USB | 0,73 s, soit **72 Mo/s** | 0,22 s |
| exiftool : 1er appel / suivants | 0,83 s / 0,08 s | 0,30 s / 0,02 s |
| Indexation d'un NEF (EXIF, miniature, aperçu) | **1,4 à 1,7 s** | 0,3 à 0,5 s |
| Mémoire maximale | 116 Mo | 122 Mo |

La suite de tests passe entièrement sur l'A50 (88 réussis, 4 ignorés faute de python-gphoto2).

## Ce qu'on en conclut

- **Le hachage n'est pas un problème** : l'A50 hache deux fois plus vite que le PC, grâce aux
  instructions SHA-2 d'ARMv8 (le i5-9600KF n'a pas SHA-NI). Le point « À VÉRIFIER » du plan
  (§ Choix du langage) est confirmé.
- **Côté téléphone, la copie vérifiée tient 72 Mo/s** sur la mémoire interne, soit plus du double
  des 25 à 35 Mo/s attendus en USB 2.0. Le goulot sera l'USB (ou le D850), pas le téléphone…
  à condition que la microSD suive : une carte A2/V30 écrit typiquement 30 à 90 Mo/s, à mesurer.
- **L'indexation coûte environ 1,5 s par NEF.** Une rafale de 70 NEF demande donc près de 2 minutes
  d'indexation, après la sauvegarde. Le watcher l'exécute entre deux passages avec un budget de
  temps : la sauvegarde reste prioritaire. Si besoin, ne générer d'abord que les miniatures
  (le décodage du JpgFromRaw de 45 Mpx pour l'aperçu est la partie la plus coûteuse).
- **La mémoire n'est pas un problème** : environ 120 Mo, y compris un NEF entier en mémoire.

## À mesurer encore

- Avec la microSD (écriture, relecture, copie vérifiée).
- Le débit PTP réel : avec le faux Nikon (phase 2), puis le D850 (sonde).
- La chauffe sur une séance longue (débit en fin de séance par rapport au début).
