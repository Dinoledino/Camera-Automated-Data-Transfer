# Camera-Automated-Data-Transfer

Sauvegarde au fil de l'eau des photos d'un Nikon D850 vers un Samsung Galaxy A50 (USB/PTP), avec vérification SHA-256, indexation SQLite et tri depuis le téléphone.

- Plan détaillé et phases : [`PLAN.md`](PLAN.md)
- Choix du système du téléphone (phase 0bis) : [`docs/os-choice.md`](docs/os-choice.md)

## Règles non négociables
1. Une photo n'est « sauvegardée » qu'après relecture depuis la microSD et un SHA-256 identique.
2. Une photo n'est effacée de la carte de l'appareil qu'après vérification de sa copie, et alors automatiquement.
3. Toute opération reprend proprement après une coupure.
