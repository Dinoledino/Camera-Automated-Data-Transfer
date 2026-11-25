# Pipeline simulé à débit USB bridé

Script : `bench/pipeline_sim.py` (sans appareil ni gphoto2, tourne aussi sur le téléphone).
Résultats bruts : `bench/resultats/pipeline-*`.

Le simulateur tire les photos, le vrai Watcher les détecte, le vrai Downloader les copie et les
vérifie à travers une source bridée (6, 15, 25, 35 Mo/s ; 0,1 s de latence PTP par fichier).
Temps accéléré ×10 ; les coûts fixes par fichier sont alors gonflés : chiffres plutôt pessimistes.

## Rafale de 10 s en RAW (70 NEF, ~3,5 Go) : vidange après la dernière prise

| Débit USB | Théorie (transfert seul) | PC | A50 (mémoire interne) |
|---|---|---|---|
| 6 Mo/s | ~590 s | 632 s | 777 s |
| 15 Mo/s | ~240 s | 283 s | 349 s |
| 25 Mo/s | ~145 s | 189 s | 276 s |
| 35 Mo/s | ~100 s | 149 s | 203 s |

Photo par photo (une toutes les 4 s), délai médian entre le déclenchement et la sauvegarde
vérifiée sur l'A50 : 46 s à 6 Mo/s (le retard s'accumule), 21 s à 15 Mo/s, 12 s à 25 Mo/s, 8 s à 35 Mo/s.

## Constats

1. **Tout est sauvegardé et vérifié dans tous les cas**, sans doublon ni perte, y compris à 6 Mo/s.
2. **Le downloader est séquentiel** : pour chaque photo, transfert USB, puis écriture, fsync,
   relecture et hash. Le travail local (~70 à 80 Mo/s) s'ajoute au transfert au lieu de s'y
   superposer : à 35 Mo/s d'USB, le débit effectif tombe vers 17 à 24 Mo/s. Superposer le
   transfert de la photo suivante à la vérification de la précédente rapprocherait le débit
   effectif de celui de l'USB.
3. **La priorité aux JPEG est faible pendant une rafale** : une vidange traite tout ce qui était
   visible au début du passage ; les JPEG des prises suivantes attendent la fin de la vidange,
   derrière des NEF. Découper la vidange (revenir au watcher toutes les ~10 s) laisserait passer
   les nouveaux JPEG devant.
4. **À 6 Mo/s (chiffre NX MobileAir), le projet reste utilisable photo par photo** quand on
   déclenche moins d'une fois toutes les 9 s environ, mais une rafale de 10 s demande ~13 min de
   vidange. À 25-35 Mo/s, la même rafale se vide en 3 à 5 minutes.

## À mesurer encore

Le débit PTP réel (faux Nikon, puis D850) et l'écriture sur microSD : ce sont eux qui diront
quelle ligne du tableau correspond à la réalité.
