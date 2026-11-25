# Projet : sauvegarde Nikon D850 → Samsung Galaxy A50

Contexte pour Claude Code. Le plan détaillé est dans `PLAN.md` : lis-le avant toute chose et suis l'ordre des phases.

## En bref
But : donner au Nikon D850 une sensation de « stockage infini ». Un ancien Samsung Galaxy A50, relié en USB (OTG), récupère les photos au fur et à mesure de la prise de vue, les vérifie (SHA-256), les efface de la carte du D850 pour la libérer, les indexe (SQLite) et offre une interface pour les trier et les gérer. Le système du téléphone est en cours de choix (voir docs/os-choice.md). Retouche et « stockage infini » plus poussé : plus tard, sur un autre appareil.

## Utilisateur
- Étudiant en informatique, à l'aise avec le code. Réponds en français.
- Le téléphone peut être sacrifié : root, flash, etc. sont acceptés.
- Le D850 n'est pas toujours disponible : tout doit pouvoir être testé sans lui (voir PLAN.md, phase 2).

## Règles non négociables
1. **Ne jamais perdre une photo.** Un fichier n'est marqué « sauvegardé » qu'après relecture depuis la microSD et un SHA-256 identique à celui calculé au téléchargement.
2. **Effacement de la carte de l'appareil uniquement après vérification.** Chaque photo est effacée automatiquement de la carte du D850 dès que sa copie est vérifiée (choix de l'utilisateur du 2026-11-25 : libérer la carte est l'intérêt principal du projet). Jamais avant : jamais une photo non vérifiée, et seulement le fichier exact (nom, taille et date identiques à la copie vérifiée, contrôlés juste avant l'effacement, car le D850 réutilise les noms après DSC_9999). Conséquence assumée : la copie du téléphone est la seule jusqu'à la synchronisation vers le PC.
3. Toute opération doit pouvoir reprendre après une coupure (débranchement, appli tuée, batterie vide), sans doublon ni perte.
4. Les points marqués « À VÉRIFIER » dans PLAN.md sont des hypothèses : teste-les avant de construire dessus.

## Stack visée
Python 3 dans Termux (root), libgphoto2 / gphoto2 (ou bindings `python-gphoto2`), SQLite, exiftool, Starlette + Uvicorn + Jinja2 pour l'interface web locale (pas FastAPI : pydantic-core ne s'installe pas sur Termux).
