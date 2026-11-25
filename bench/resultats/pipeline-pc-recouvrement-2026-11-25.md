# Pipeline simulé à débit bridé — pc-recouvrement (2026-11-25T11:21:38)

Échelle de temps : ×10 (durées remultipliées). Latence PTP simulée : 0.1 s par fichier. Passage du watcher toutes les 2.0 s, stabilité sur 2 passages.

## Rafale de 3 s, RAW + JPEG (21 prises, 42 fichiers, ~1,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 263.2 s | 77.0 s | 263.2 s | 42/42 |
| 15 Mo/s | 113.6 s | 34.6 s | 113.6 s | 42/42 |
| 25 Mo/s | 73.1 s | 30.0 s | 73.1 s | 42/42 |
| 35 Mo/s | 55.3 s | 26.8 s | 55.3 s | 42/42 |

## Rafale de 10 s, RAW (70 NEF, ~3,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 613.6 s | — | 613.6 s | 70/70 |
| 15 Mo/s | 258.6 s | — | 258.6 s | 70/70 |
| 25 Mo/s | 159.6 s | — | 159.6 s | 70/70 |
| 35 Mo/s | 118.1 s | — | 118.1 s | 70/70 |
