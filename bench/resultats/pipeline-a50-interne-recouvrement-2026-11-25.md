# Pipeline simulé à débit bridé — a50-interne-recouvrement (2026-11-25T11:32:01)

Échelle de temps : ×10 (durées remultipliées). Latence PTP simulée : 0.1 s par fichier. Passage du watcher toutes les 2.0 s, stabilité sur 2 passages.

## Rafale de 3 s, RAW + JPEG (21 prises, 42 fichiers, ~1,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 337.8 s | 122.8 s | 337.8 s | 42/42 |
| 15 Mo/s | 169.0 s | 60.0 s | 169.0 s | 42/42 |
| 25 Mo/s | 123.3 s | 60.2 s | 123.3 s | 42/42 |
| 35 Mo/s | 94.7 s | 54.8 s | 94.7 s | 42/42 |

## Rafale de 10 s, RAW (70 NEF, ~3,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 769.4 s | — | 769.4 s | 70/70 |
| 15 Mo/s | 354.5 s | — | 354.5 s | 70/70 |
| 25 Mo/s | 245.8 s | — | 245.8 s | 70/70 |
| 35 Mo/s | 204.8 s | — | 204.8 s | 70/70 |
