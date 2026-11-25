# Pipeline simulé à débit bridé — pc (2026-11-25T11:02:51)

Échelle de temps : ×10 (durées remultipliées). Latence PTP simulée : 0.1 s par fichier. Passage du watcher toutes les 2.0 s, stabilité sur 2 passages.

## 10 photos RAW, une toutes les 4 s

| Débit | Délai déclenchement → vérifiée (médian / max) | Sauvegardées |
|---|---|---|
| 6 Mo/s | 40.3 s / 63.3 s | 10/10 |
| 15 Mo/s | 12.8 s / 13.3 s | 10/10 |
| 25 Mo/s | 6.7 s / 7.5 s | 10/10 |
| 35 Mo/s | 5.6 s / 6.4 s | 10/10 |

## Rafale de 3 s, RAW + JPEG (21 prises, 42 fichiers, ~1,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 271.8 s | 144.2 s | 271.8 s | 42/42 |
| 15 Mo/s | 124.4 s | 62.8 s | 124.4 s | 42/42 |
| 25 Mo/s | 85.4 s | 43.7 s | 85.4 s | 42/42 |
| 35 Mo/s | 68.2 s | 35.2 s | 68.2 s | 42/42 |

## Rafale de 10 s, RAW (70 NEF, ~3,5 Go)

| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | Sauvegardés |
|---|---|---|---|---|
| 6 Mo/s | 631.9 s | — | 631.9 s | 70/70 |
| 15 Mo/s | 282.8 s | — | 282.8 s | 70/70 |
| 25 Mo/s | 189.3 s | — | 189.3 s | 70/70 |
| 35 Mo/s | 149.0 s | — | 149.0 s | 70/70 |
