# Plan détaillé : sauvegarde et tri des photos d'un Nikon D850 sur un Galaxy A50

## 1. Objectif

**Vision (précisée le 2026-11-25) : donner au D850 une sensation de « stockage infini ».** Les cartes
du D850 se remplissent vite et son interface ne permet pas de gérer les photos confortablement. Le
dispositif décharge la carte au fil de la prise de vue et offre une vraie interface de tri et de gestion.

- **Périmètre actuel (A50)** : récupérer les photos du D850 pour libérer sa carte, et proposer une
  interface pour trier et gérer les photos.
- **Plus tard (autre appareil, ou extensions)** : pousser plus loin le « stockage infini », et peut-être
  retoucher les photos avec des logiciels performants.

Concrètement, faire de l'A50 un compagnon de prise de vue :
- **Sauvegarde au fil de l'eau** : chaque photo prise par le D850 est copiée sur le téléphone dès que possible, pendant la séance.
- **Vérification** : chaque copie est contrôlée par SHA-256 avant d'être considérée comme sûre.
- **Libération de la carte** : chaque photo est effacée de la carte du D850 dès que sa copie est vérifiée (décision du 2026-11-25).
- **Tri sur place** : parcourir, mettre en favori, garder ou rejeter les photos depuis le téléphone.
- **Synchronisation** vers le PC une fois rentré chez soi.

### Décisions du 2026-11-25 (précisions de l'utilisateur)
- **Stockage intermédiaire, pas permanent.** Le téléphone garde les photos le temps de la séance ;
  il est vidé **manuellement depuis un PC, par USB** (le Wi-Fi de l'A50 est en panne). Plus tard, sur
  un autre téléphone avec Wi-Fi : envoi automatique vers un drive à une adresse donnée dès qu'un
  Wi-Fi est disponible (hors prototype). L'accès par USB depuis un PC restera utile et doit être gardé.
- **Emplacements de stockage** : mémoire interne du téléphone, puis microSD (remplies l'une après
  l'autre). Cela couvre ~99 % des séances.
- **Téléphone plein** : la copie s'arrête (avec une marge pour la base, le journal et les aperçus) ;
  les photos non copiées restent simplement sur la carte du D850, qui n'est effacée qu'après
  vérification. L'interface montre en permanence une **barre de mémoire utilisée** qui fait comprendre
  que la copie cessera quand elle sera pleine.
- **Rejeter = vouloir supprimer**, en trois temps : (1) sélection (photo marquée « rejetée ») ;
  (2) écran de confirmation (liste, place gagnée, avertissement : la photo n'est plus sur l'appareil
  photo) ; (3) **corbeille** : photo récupérable jusqu'à l'extinction du téléphone ou pendant un délai
  réglable (24 h par défaut), le premier des deux. « Extinction » = corbeille vidée au prochain
  démarrage du téléphone (détecté par l'identifiant de démarrage du noyau, pas par un simple
  redémarrage de l'application). Fichiers en corbeille déplacés (renommage atomique) dans un dossier
  caché du même emplacement de stockage. Téléphone plein : la corbeille est vidée en premier, des plus
  anciennes aux plus récentes, et le journal le signale.
- **Usage terrain** : le téléphone est **fixé sous le D850** dans un support imprimé en 3D et reste
  branché pendant toute la séance : la réactivité (photo copiée vite) prime. Points à vérifier :
  charge du téléphone pendant l'OTG (adaptateur avec charge ? pris en charge par l'A50 ? **À VÉRIFIER**),
  chauffe dans le support (laisser respirer le dos), pas de vis de trépied reporté sous le support.
- **Cartes du D850** : deux logements amovibles, XQD (ou CFexpress B) et SD ; pas de mémoire interne.
  Cartes utilisées et rôle de la 2e carte : à relever sur l'appareil (la sonde les détecte). En mode
  « Sauvegarde » (même photo sur les deux cartes), les deux exemplaires sont effacés après vérification.
- **Vidéos (MOV)** : récupérées et effacées comme les photos, **en dernier dans la file** (après tous les
  RAW et JPEG) ; pas d'aperçu pour l'instant.
- **Retouche** : souhaitée **sur le téléphone**, logiciels encore à choisir (faisabilité en discussion).
  Conséquence pour la phase 0bis : les applications de retouche Android (Lightroom Mobile, Snapseed…,
  prise en charge des NEF du D850 à vérifier) exigent de garder Android (options A ou B) ; postmarketOS
  (option C) ne laisse que des logiciels Linux (darktable…), peu adaptés à un écran de téléphone.
  Favoris, gardées et rejetées sont aussi écrits dans des fichiers XMP à côté des photos (relus par
  Lightroom, darktable et d'autres).

## 2. Matériel et faits établis

### Samsung Galaxy A50 : SM-A505FN, CSC XEF, bootloader binaire 9 (A505FNXXU9CWB4), 4 Go de RAM, 128 Go (détails dans docs/os-choice.md)
- Puce Exynos 9610, 4 ou 6 Go de RAM, 64 ou 128 Go de stockage.
- Emplacement microSD jusqu'à 1 To : c'est le stockage principal des sauvegardes.
- Port USB-C en **USB 2.0** : débit pratique estimé entre 25 et 35 Mo/s, à mesurer en phase 2.
- Root : déverrouillage OEM, puis firmware patché par Magisk, flashé avec Odin (Windows) ou Heimdall (Linux). Le compteur Knox sera grillé définitivement, ce qui est accepté.
- LineageOS n'existe qu'en version non officielle (18.1, Android 11, abandonnée en 2022) : écarté.
- Le choix du système se fait en phase 0bis, parmi trois options : A (One UI nettoyé), B (GSI AOSP) ou C (postmarketOS). Les essais avancent de C vers A, et A sert de solution de repli. Un Samsung se reflashe toujours avec le firmware d'origine via le mode Download et Odin ou Heimdall : les essais sont sans risque réel.

### Nikon D850
- Port **USB 3.0 Micro-B**, protocole PTP, pris en charge par libgphoto2.
- Wi-Fi intégré limité à SnapBridge (smartphones). Le FTP ou l'envoi vers un ordinateur demande le boîtier WT-7. **Décision : USB uniquement.**
- Deux emplacements de carte, XQD et SD. Mode « Sauvegarde » possible : écriture simultanée sur les deux cartes.
- Un RAW (NEF) pèse environ 50 Mo. La rafale atteint 7 images/s, soit environ 350 Mo/s.

### Conséquence clé
Le port USB 2.0 de l'A50 est environ 10 fois trop lent pour suivre une rafale en temps réel. La carte de l'appareil sert donc de **tampon**, que le téléphone **vide en continu**. Ordres de grandeur :
- En photo par photo, la sauvegarde est quasi immédiate.
- Une rafale de 50 RAW (environ 2,5 Go) prend 1 à 2 minutes à vider.

Le lien SD vers SD a été écarté : les deux lecteurs sont des hôtes, l'écriture concurrente corromprait le système de fichiers FAT, et le gain serait faible.

## 3. Achats nécessaires
- Un adaptateur OTG USB-A femelle vers USB-C (à utiliser avec le câble Nikon UC-E22), ou un câble USB-C vers Micro-B USB 3.0 direct.
- Un câble USB-C vers USB-C pour les tests avec le « faux Nikon » (voir phase 2).
- Une microSD rapide et fiable (A2/V30) d'une capacité suffisante.
- Facultatif : une batterie externe, car l'OTG vide la batterie du téléphone.

## 4. Réglages du D850 (le jour J)
- Carte 1 (XQD) : RAW. Carte 2 (SD) : JPEG de petite taille (fonction « RAW principal, JPEG secondaire »). Le téléphone récupère les JPEG en priorité pour l'aperçu, puis les RAW.
- Variante pour une sécurité maximale : carte 2 en « Sauvegarde ». Le téléphone n'a alors plus de JPEG à récupérer, et les aperçus seront extraits des NEF.
- Option de menu « USB » ou mode de connexion : **À VÉRIFIER**, selon ce qui permet de déclencher pendant que le D850 est branché.

## 5. Architecture logicielle

```
┌────────────┐  USB/PTP  ┌──────────────────────── A50 (Termux, root) ─────────────────────────┐
│   D850     │──────────▶│ watcher : écoute les événements PTP « nouvel objet » (ObjectAdded)   │
│ carte =    │           │    │                                                                │
│ tampon     │           │    ▼                                                                │
└────────────┘           │ file prioritaire (SQLite) : JPEG > RAW, puis ordre chronologique     │
                         │    │                                                                │
                         │    ▼                                                                │
                         │ downloader : copie .part → SHA-256 → relecture → renommage atomique  │
                         │    │                                                                │
                         │    ▼                                                                │
                         │ indexeur : EXIF, aperçu (JpgFromRaw), miniature                      │
                         │    │                                                                │
                         │    ▼                                                                │
                         │ interface web locale (Starlette) : galerie, tri, corbeille           │
                         │ sync : Syncthing ou rsync vers le PC, avec vérification              │
                         └─────────────────────────────────────────────────────────────────────┘
```

### Choix du langage
Mesure de référence (sur x86, 10 fichiers de 50 Mo, chaque fichier copié, haché en SHA-256, synchronisé sur disque, relu et vérifié) : C, Rust, Go, Python et Node.js sont **à moins de 4 % les uns des autres** (environ 1,6 s). Le hachage passe toujours par du code natif optimisé (OpenSSL ou l'assembleur de Go), et le facteur limitant réel est l'USB 2.0 (environ 30 Mo/s). La vitesse brute du langage n'est donc **pas un critère**. Les critères qui comptent :
- la qualité de la liaison avec libgphoto2 ;
- la possibilité de corriger sur le téléphone le jour J ;
- la facilité de compilation pour aarch64 avec musl (postmarketOS).

Décision par défaut : **Python** (`python-gphoto2`). Option d'apprentissage : réécrire le démon en Rust (crate `gphoto2`) ou en Go (cgo) une fois la version Python validée, en gardant les mêmes tests.

**Phase 1** : relancer `bench-langages/run.sh` sur l'A50 pour confirmer ce résultat sur ARM (hachage via les instructions SHA2 d'ARMv8). **À VÉRIFIER.**

### Modèle de données (SQLite)
- `photos` : id, chemin côté appareil, nom, taille, date de prise de vue, type (raw/jpeg), sha256, état, chemin local, note, drapeau, id du groupe RAW/JPEG.
- États : `detected → downloading → downloaded → verified → synced_pc`. Les états de suppression sont séparés : `delete_requested → deleted_on_camera`.
- `events` : journal horodaté de toutes les opérations, pour le débogage et l'audit.
- Mode WAL activé et transactions courtes, pour que la base survive aux coupures.

### Règles de robustesse
- Écrire dans `fichier.part`, faire un `fsync`, relire depuis la carte, comparer le SHA-256, puis renommer. Seul le renommage rend le fichier « réel ».
- Au démarrage, réconcilier la base avec la microSD (supprimer les `.part` orphelins) et avec la liste des fichiers de l'appareil s'il est branché.
- Identifier un fichier par le triplet (nom, taille, date) : le D850 recommence la numérotation à DSC_0001 après 9999.
- Utiliser des notes et marques au format XMP à côté des fichiers, compatibles avec Lightroom et darktable.

## 6. Phases

### Phase 0bis : choix du système (le faire en premier)
Objectif : un système minimaliste qui ne fait que ce dont le projet a besoin. Trois options, à évaluer dans cet ordre :

**C. postmarketOS (Linux Alpine, sans Android), l'option idéale si elle fonctionne**
- Un portage `samsung-a50` existe (noyau dérivé de celui de Samsung, version 4.14), mais sans mainteneur déclaré. Son état réel est **À VÉRIFIER** sur le wiki : wiki.postmarketos.org/wiki/Samsung_Galaxy_A50_(samsung-a50).
- Installation avec `pmbootstrap`, en choisissant l'interface `console` ou `none` (pas d'interface graphique) : on obtient un vrai Linux, avec SSH, `apk add libgphoto2 python3`, des services OpenRC, sans Termux ni Android.
- À valider avant d'adopter cette option : démarrage, Wi-Fi, charge et état de la batterie, montage de la microSD, et surtout **USB en mode hôte** (`lsusb` avec le faux Nikon branché en OTG).
- Si l'un de ces points échoue, passer à l'option B ou A. Une demi-journée de tests suffit pour trancher.

**Procédure détaillée pour l'option C, depuis un PC Ubuntu (26.04 LTS)**

*Préparation du PC*
```bash
sudo apt update
sudo apt install git pipx python3-venv kpartx heimdall-flash adb android-sdk-platform-tools-common
# android-sdk-platform-tools-common installe les règles udev qui permettent d'accéder aux appareils Android/Samsung sans être root
pipx ensurepath
# pmbootstrap n'est plus publié sur PyPI (versions retirées) : installer le dernier tag depuis git
pipx install "git+https://gitlab.nura.eco/postmarketOS/pmbootstrap.git@$(git ls-remote --tags --refs https://gitlab.nura.eco/postmarketOS/pmbootstrap.git | sed 's|.*refs/tags/||' | sort -V | tail -1)"
# se déconnecter puis se reconnecter pour mettre à jour le PATH
heimdall version && adb version && pmbootstrap --version
```
- Télécharger et garder le **firmware Samsung d'origine** du modèle exact (SM-A505F ou FN, avec la région CSC du téléphone), via l'outil `samloader` ou un site de firmwares Samsung. C'est la solution de repli.

*Préparation du téléphone*
1. Vérifier le modèle exact (Paramètres > À propos du téléphone) et la version de One UI.
2. Options développeur (taper 7 fois sur le numéro de version), puis activer le déverrouillage OEM et le débogage USB.
   - Si le déverrouillage OEM est absent, connecter le téléphone à Internet et attendre, car certains Samsung imposent un délai. **À VÉRIFIER** sur l'A50.
3. Passer en mode Download : téléphone éteint, maintenir volume haut + volume bas, puis brancher le câble. Ensuite, appui long sur volume haut pour déverrouiller le bootloader. **Ça efface toutes les données.**
4. Redémarrer, refaire rapidement la configuration initiale, puis réactiver les options développeur (l'option de déverrouillage OEM doit apparaître grisée et activée).

*Construction et installation*
```bash
pmbootstrap init        # fabricant : samsung, appareil : a50, interface : console (ou none)
                        # définir l'utilisateur, le mot de passe et le nom de machine ; ajouter des paquets : openssh, python3, libgphoto2
pmbootstrap install     # construit l'image
```
Deux méthodes d'installation : lire d'abord la page wiki de l'appareil pour savoir laquelle est recommandée (**À VÉRIFIER**).
- **Recovery (méthode indiquée sur le wiki)** : installer TWRP pour l'A50 avec Heimdall, démarrer en recovery, choisir *ADB sideload*, puis lancer :
  ```bash
  pmbootstrap install --android-recovery-zip
  adb sideload "$(pmbootstrap config work)/chroot_buildroot_aarch64/var/lib/postmarketos-android-recovery-installer/pmos-samsung-a50.zip"
  ```
- **Flash direct** : téléphone en mode Download, puis `pmbootstrap flasher flash_rootfs` et `pmbootstrap flasher flash_kernel` (via Heimdall).

*Premier démarrage*
```bash
ip a                    # une interface réseau USB doit apparaître sur le PC
ssh <utilisateur>@172.16.42.1
# sur le téléphone :
nmcli device wifi connect "<SSID>" password "<mdp>"
sudo apk add python3 libgphoto2 gphoto2 exiftool sqlite rsync
lsusb                   # puis brancher le faux Nikon en OTG et relancer lsusb
```

*Retour à l'origine en cas d'échec*
```bash
# téléphone en mode Download ; extraire les fichiers .tar.md5 du firmware d'origine
heimdall flash --BOOT boot.img --SUPER super.img ...   # partitions selon le firmware ; ou utiliser Odin sous Windows (plus simple)
```
Repli plus simple : faire le retour à l'origine avec Odin depuis un PC Windows, en utilisant les fichiers BL, AP, CP et CSC.

**B. GSI AOSP minimaliste (TrebleDroid « vanilla », sans services Google)**
- L'A50 est compatible avec Project Treble et figure sur le wiki TrebleDroid. Problèmes connus : le transfert de fichiers MTP demande le noyau Quantum, et l'appareil photo du téléphone ne marche pas sur certains exemplaires (sans importance ici).
- On garde Android (et donc Termux, Magisk, l'API USB Host), mais sans surcouche Samsung.
- **À VÉRIFIER** : l'USB OTG en mode hôte avec le faux Nikon.

**A. One UI d'origine, nettoyé : la solution de repli sûre**
- Réinstallation propre : flasher le firmware complet avec Odin ou Heimdall (fichiers BL, AP, CP et CSC, **pas** HOME_CSC), ce qui efface tout.
- Configuration minimale : pas de compte Google ni Samsung.
- Allègement : `adb shell pm uninstall --user 0 <paquet>` pour les applications Samsung, Google et Facebook inutiles. Ne pas toucher au téléphone, aux SMS ni à `com.android.*` essentiel. Garder la liste des paquets retirés dans le dépôt (`debloat.txt`), pour pouvoir annuler.
- Ensuite, root avec Magisk.
- Ce sont les pilotes Samsung d'origine : l'USB OTG est quasi certain de fonctionner.

**Conséquence pour le code** : l'application Python et libgphoto2 sont identiques dans les trois cas. Seuls le déploiement (service OpenRC ou script de démarrage Termux) et le module d'accès USB changent, et ils sont déjà prévus comme interchangeables (voir « Une seule application, des modules interchangeables »). Écrire `deploy/` avec une cible par système.

**Livrable** : `docs/os-choice.md`, avec ce qui a été testé, ce qui marche et la décision prise.

### Phase 0 : préparation du téléphone (sans D850)
Les étapes ci-dessous valent pour les options A et B. Pour l'option C, remplacer Termux par le système postmarketOS lui-même (SSH et `apk`).
1. Sauvegarder les données éventuelles de l'A50, puis le mettre à jour une dernière fois.
2. Activer les options développeur, le déverrouillage OEM et le débogage USB.
3. Rooter avec Magisk via Odin ou Heimdall, puis le vérifier avec `su` dans Termux.
4. Installer Termux et Termux:API depuis **F-Droid** (pas depuis le Play Store).
5. Lancer `pkg install python git sqlite exiftool openssh rsync`, puis `pkg search gphoto2`. Si gphoto2 n'est pas disponible, compiler libgphoto2 (**À VÉRIFIER**).
6. Limiter la charge à environ 80 % (module Magisk ACC ou équivalent).
7. Mettre en place l'accès SSH depuis le PC, pour développer confortablement.
8. Formater la microSD en exFAT et repérer son point de montage dans Termux.

**Critère de réussite** : connexion SSH depuis le PC, `su` fonctionne, la microSD est accessible en écriture.

### Phase 1 : mesures de base
- Débit de la microSD en écriture et en lecture (`dd` sur 2 Go, en vidant le cache entre les deux).
- Temps de calcul du SHA-256 d'un fichier de 50 Mo.
- Temps d'extraction de l'aperçu (`exiftool -b -JpgFromRaw`) à partir de vrais NEF de D850, téléchargés sur raw.pixls.us.

**Livrable** : `bench/baseline.md` avec les chiffres.

### Phase 2 : le « faux Nikon » (sans D850)
1. Relier un autre téléphone Android à l'A50 en USB-C, puis le passer en mode USB « Transfert de photos (PTP) ».
2. Vérifier la détection avec `gphoto2 --auto-detect`, puis `--list-files` et `--get-all-files`.
3. Mesurer le **débit PTP réel**, le chiffre qui dimensionne tout le projet.
4. Tester débranchement et rebranchement, ainsi que la détection automatique.
5. Tester aussi l'accès sans root avec `termux-usb`, pour savoir si le root est vraiment nécessaire.

**Critère de réussite** : un script télécharge tout le DCIM du faux appareil, vérifie chaque fichier et affiche le débit moyen.

### Phase 3 : moteur de sauvegarde
1. Écrire le schéma SQLite et la couche d'accès.
2. Écrire le downloader (.part, SHA-256, relecture, renommage) avec reprise après erreur.
3. Écrire la file prioritaire : JPEG d'abord, puis RAW, puis ordre chronologique.
4. Écrire le watcher : événements PTP « nouvel objet » (équivalent de `gphoto2 --wait-event-and-download`, ou bindings `python-gphoto2`), avec une vérification périodique de la liste des fichiers en filet de sécurité.
5. Écrire un simulateur de rafale : pilote gphoto2 « Directory Browse », plus un script qui dépose des NEF à 7 images/s pendant 10 s.

**Tests obligatoires** :
- Rafale de 70 photos : 70 fichiers vérifiés, aucun doublon.
- Débranchement en pleine copie : reprise propre, sans `.part` comptabilisé comme sauvegardé.
- Appli tuée (`kill -9`) puis relancée : reprise propre.
- microSD pleine : arrêt propre et alerte, sans corruption.

### Phase 4 : indexation et aperçus
- Extraction des données EXIF (date, objectif, focale, ISO, vitesse, ouverture).
- Aperçu extrait du NEF (JpgFromRaw), puis miniature d'environ 400 px.
- Regroupement de chaque RAW avec son JPEG, et regroupement par séance (photos séparées par plus de X minutes).

### Phase 5 : interface de tri (Starlette, ouverte dans le navigateur du téléphone)
Starlette + Uvicorn + Jinja2, en pur Python, plutôt que FastAPI : sur l'A50 (Termux), pydantic-core
n'existe pas précompilé et sa compilation Rust échoue (constaté le 2026-11-25). Starlette est la base
de FastAPI, sans la validation pydantic, inutile ici. L'interface tourne dans un processus séparé du
watcher : un plantage de l'interface n'interrompt jamais la sauvegarde.
- Galerie avec un compteur bien visible : « N photos pas encore sauvegardées ».
- Vue plein écran avec balayage, notes de 1 à 5, drapeau « garder » ou « rejeter ».
- Effacement côté appareil automatique, juste après la vérification (voir CLAUDE.md, règle 2) : pas d'écran de suppression manuelle. Avant chaque effacement PTP, relire les informations du fichier sur l'appareil (nom, taille, date) et ne l'effacer que si elles sont identiques à la copie vérifiée. Après une coupure entre l'effacement et l'écriture en base (`delete_requested`), constater au redémarrage que le fichier a disparu de l'appareil et passer à `deleted_on_camera`.
- Interface (maquette validée le 2026-11-25) : galerie, plein écran avec favori / garder / rejeter, sélection et déplacement entre sessions (renommage en option, décoché par défaut), sessions, réglages (thème, couleur, taille des miniatures, informations affichées), journal des événements en langage clair, exportable dans un fichier. Aucun terme technique (SHA-256, etc.) dans l'interface.
- Rangement : renommage et classement par séance.
- Écriture des notes et drapeaux dans les fichiers XMP.

### Phase 6 : vidage vers le PC (par USB)
- Le téléphone est vidé manuellement depuis un PC, par câble USB : copie des photos avec leurs XMP,
  recalcul du hash côté PC, puis passage à l'état `synced_pc` (rsync en SSH sur le réseau USB, ou
  script côté PC).
- Après confirmation que la copie PC est identique, proposer de libérer le téléphone.
- Rapport de fin de séance : nombre de photos, taille, durée, anomalies.
- Plus tard (autre téléphone avec Wi-Fi) : envoi automatique vers un drive ; l'accès USB reste.

### Phase 7 : premier essai avec le D850
L'essai se fait via `probe.py` (voir la section suivante). Les étapes ci-dessous sont ce que la sonde doit couvrir.
1. Brancher le D850, puis vérifier qu'il est détecté par `gphoto2 --auto-detect`.
2. **À VÉRIFIER, point bloquant** : peut-on déclencher normalement pendant que le D850 est branché en USB ? Les photos vont-elles bien sur la carte (et pas seulement en RAM) ?
3. Problème connu : certains Samsung ont des soucis d'OTG avec le D850. Une piste citée pour l'appli qDslrDashboard est de désactiver « Use Android native USB access ». En root, l'accès direct à `/dev/bus/usb` devrait aussi contourner ce problème.
4. Tester en conditions réelles : photo par photo, puis rafale de 50, puis séance de 30 minutes (batterie, température, ralentissement dû à la chaleur).

### Contrainte : pas de PC de développement le jour où le D850 est disponible
Le jour où le D850 est disponible, l'utilisateur n'aura **pas** son PC de développement, tout au plus un accès SSH à un serveur depuis un autre PC. Tout ce qui doit être découvert avec le vrai appareil doit donc être **préparé à l'avance et automatisé**.

#### a) Une seule application, des modules interchangeables
Chaque point incertain est isolé derrière une interface commune, avec plusieurs implémentations choisies dans `config.toml` :

| Point incertain | Implémentations à prévoir |
|---|---|
| Accès USB | `root_libusb` (accès direct à `/dev/bus/usb`), `termux_usb` (descripteur de fichier fourni par Termux:API) |
| Pilote PTP | `gphoto2_cli` (processus externe), `python_gphoto2` (bindings), `pyusb_ptp` (PTP minimal en Python pur, en dernier recours) |
| Détection des nouvelles photos | `events` (événement PTP « nouvel objet »), `polling` (comparaison périodique de la liste des fichiers), `hybrid` |
| Carte source | XQD, SD, ou les deux ; gestion avec ou sans paire RAW/JPEG |
| Récupération | fichier complet, ou aperçu seul puis fichier complet |

Chaque implémentation est validée **à l'avance** sur le faux Nikon (phase 2) et dans le simulateur (phase 3).

#### b) `probe.py` : sonde automatique à lancer avec le D850
Un seul script, lancé dans Termux sur le téléphone, guide l'utilisateur pas à pas et teste toute la matrice :

1. Informations système : version d'Android, root, version de libgphoto2, espace libre sur la microSD.
2. Détection du D850 avec chaque méthode d'accès USB, en notant le résultat et l'erreur éventuelle.
3. Pour chaque pilote : liste des fichiers, téléchargement d'un NEF, débit mesuré, SHA-256.
4. Étapes interactives, du type « Déclenche 5 photos maintenant, puis appuie sur Entrée » :
   - les déclenchements sont-ils possibles pendant que l'appareil est branché ?
   - les événements de nouvelle photo arrivent-ils, et avec quel délai ?
   - les fichiers sont-ils écrits sur la carte, ou seulement en RAM ?
5. Changements de réglages guidés (carte 2 en « Sauvegarde » puis en « RAW principal, JPEG secondaire », options de menu USB), avec un nouveau test à chaque fois.
6. Rafale réelle de 3 secondes, puis temps de vidange du tampon.
7. Test de suppression PTP, **uniquement sur une photo de test créée pendant la sonde**.
8. Résultats :
   - `probe_report.json` et `probe_report.md`, lisibles par un humain ;
   - `config.recommended.toml`, la meilleure combinaison trouvée, appliquée automatiquement ;
   - les journaux complets de libgphoto2 (`--debug --debug-logfile`) pour chaque test, afin d'analyser les échecs plus tard sur le PC.
9. La sonde doit être **reprenable** : si elle plante à l'étape 4, on peut la relancer à partir de cette étape.

Exigence : la sonde n'a aucune dépendance réseau et ne fait aucune suppression en dehors des photos de test. Elle doit d'abord tourner entièrement contre le faux Nikon et contre « Directory Browse ».

#### c) Corriger le jour J sans le PC
- **Le téléphone est lui-même un environnement de développement** : dépôt git cloné dans Termux, éditeur (`nano`, `vim` ou `micro`), Python, tests. On peut corriger un bug sur place.
- **Tunnel SSH inversé** du téléphone vers le serveur (`ssh -R 2222:localhost:8022 serveur`, avec autossh). Depuis l'autre PC, on se connecte au serveur, puis au téléphone avec `ssh -p 2222 localhost`, et on travaille dessus comme sur une machine distante. Également utilisable par Claude Code s'il est installé sur le serveur. **À VÉRIFIER** : le serveur doit autoriser la redirection de port (`AllowTcpForwarding`).
- **Dépôt git sur le serveur** (ou GitHub) : le téléphone envoie (`push`) les rapports de la sonde et les correctifs, puis le PC de développement les récupère (`pull`) plus tard.
- Le partage de connexion 4G ou le Wi-Fi local suffit pour le tunnel.

#### d) À préparer avant le jour J (liste de contrôle)
- [ ] Toutes les implémentations du tableau a) passent les tests sur le faux Nikon et dans le simulateur.
- [ ] `probe.py` s'exécute de bout en bout sur le faux Nikon.
- [ ] Le tunnel SSH inversé a été testé depuis un autre PC.
- [ ] Le dépôt git est cloné sur le téléphone, avec la configuration de `push` vers le serveur.
- [ ] Batterie externe, câble Nikon, adaptateur OTG, câble de secours.
- [ ] Le D850 est chargé, ses deux cartes sont formatées, son firmware est à jour.

## 7. Risques et solutions de repli

| Risque | Solution de repli |
|---|---|
| Le D850 refuse de déclencher en étant branché | Copier par lots entre deux scènes, en branchant le téléphone seulement à ce moment-là |
| OTG instable avec le Samsung | Changer de câble ou d'adaptateur, accéder en root via libusb, utiliser un hub alimenté |
| gphoto2 absent de Termux | Compiler, ou passer par un chroot Debian (proot-distro) |
| Débit USB réel très inférieur à 25 Mo/s | Récupérer seulement les JPEG pendant la séance, et les RAW le soir |
| Ralentissement dû à la chaleur | Limiter la fréquence du processeur et du hachage, ventiler le téléphone |
| Plantage du code compilé de traitement d'image (constaté : Python 3.14.10 de Termux, ramasse-miettes en cause) | Trois processus séparés (sauvegarde, aperçus, interface) relancés par `cadt.supervise` ; ramasse-miettes désactivé dans le processus des aperçus sur Android ; la sauvegarde reprend proprement après un arrêt brutal |
| Batterie qui gonfle à force d'être branchée | Limite de charge à 80 %, contrôle visuel régulier |

## 8. Pistes bonus (après le projet principal)
- Rétro-ingénierie de SnapBridge (Bluetooth basse consommation et Wi-Fi, probablement du PTP/IP : **À VÉRIFIER**), en capturant le trafic entre le D850 et un téléphone.
- Application Android native en Kotlin pour remplacer l'interface web.
- Géolocalisation des photos à partir du GPS du téléphone (fichier GPX, puis rapprochement par l'heure).
