# Mesures de base — a50-interne (2026-11-25T10:55:54)

Machine : {'platform': 'Android-11-aarch64-64bit', 'machine': 'aarch64', 'python': '3.14.10', 'cpus': 8, 'cpu_max_mhz': [1742, 2314], 'mem_total_mb': 3604}
Dossier mesuré : `/data/data/com.termux/files/home/bench-tmp`

| Mesure | Résultat |
|---|---|
| Écriture séquentielle (1024 Mo, fsync) | 188.4 Mo/s |
| Lecture, cache vidé | 398.3 Mo/s (⚠ probablement encore en cache) |
| SHA-256 en mémoire | 1146.8 Mo/s (0.044 s pour 50 Mo) |
| SHA-256 d'un fichier de 50 Mo | 0.222 s |
| Copie vérifiée de 5 NEF de 52.6 Mo (sans USB) | 72.2 Mo/s, 0.73 s par NEF, 5/5 vérifiés |
| exiftool : 1er appel / suivants | 0.825 s / 0.077 s |
| Indexation d'un NEF (EXIF, miniature, aperçu) | 1.41 s, 1.4 s, 1.68 s |
| Mémoire maximale du processus | 116 Mo |
