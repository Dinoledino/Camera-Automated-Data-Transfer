# Pipeline simulé à débit bridé — a50-interne (2026-11-25T11:09:32)

Échelle de temps : ×10 (durées remultipliées). Latence PTP simulée : 0.1 s par fichier. Passage du watcher toutes les 2.0 s, stabilité sur 2 passages.

## 10 photos RAW, une toutes les 4 s

| Débit | Délai déclenchement → vérifiée (médian / max) | Sauvegardées |
|---|---|---|
| 6 Mo/s | 45.9 s / 92.2 s | 10/10 |
| 15 Mo/s | 21.2 s / 37.3 s | 10/10 |
| 25 Mo/s | 12.2 s / 13.1 s | 10/10 |
| 35 Mo/s | 7.9 s / 9.3 s | 10/10 |

## Rafale de 3 s, RAW + JPEG (21 prises, 42 fichiers, ~1,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 326.9 s | 228.9 s | 326.9 s | 42/42 |
| 15 Mo/s | 168.0 s | 118.6 s | 168.0 s | 42/42 |
| 25 Mo/s | 121.9 s | 86.1 s | 121.9 s | 42/42 |
| 35 Mo/s | 102.0 s | 81.8 s | 102.0 s | 42/42 |

## Rafale de 10 s, RAW (70 NEF, ~3,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 776.8 s | — | 776.8 s | 70/70 |
| 15 Mo/s | 348.5 s | — | 348.5 s | 70/70 |
| 25 Mo/s | 275.8 s | — | 275.8 s | 70/70 |
| 35 Mo/s | 203.0 s | — | 203.0 s | 70/70 |
