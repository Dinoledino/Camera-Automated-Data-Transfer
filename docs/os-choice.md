# Phase 0bis : choix du système de l'A50

Ordre d'évaluation : **C** (postmarketOS) → **B** (GSI TrebleDroid vanilla) → **A** (One UI nettoyé, solution de repli).

## Identification du téléphone
| Élément | Valeur |
|---|---|
| Modèle exact | **SM-A505FN** (appareil `a50`, produit `a50eea`) |
| Version d'Android | Android 11 (API 30), correctif de sécurité du 2023-03-01 (dernière mise à jour Samsung) |
| Build / bootloader | `A505FNXXU9CWB4` : bootloader en **binaire 9** (« U9 »). On ne peut pas flasher un firmware de binaire inférieur |
| CSC (région) | `XEF` (France) |
| Noyau d'origine | 4.14.113 |
| RAM / stockage | 4 Go / 128 Go (58 Go utilisés au 2026-11-23 : **données à sauvegarder avant tout flash**) |
| microSD | aucune insérée au 2026-11-23 |
| Verrouillage | bootloader verrouillé (`flash.locked=1`, état vérifié `green`), Knox intact (`warranty_bit=0`), `ro.oem_unlock_supported=1` |
| Treble | `ro.treble.enabled=true` (compatible GSI, option B) |
| USB hôte (Android) | `android.hardware.usb.host` déclaré |
| SELinux | enforcing |
| Firmware d'origine téléchargé (nom du fichier, SHA-256) | à télécharger : SM-A505FN / XEF, au moins `A505FNXXU9CWB4`. C'est la dernière version publiée, et Samsung ne sert plus que la dernière version : c'est donc précisément celle qu'on obtiendra. Outil conseillé : Bifrost (SamloaderKotlin, maintenu en 2026) |

## Option C : postmarketOS
Note : depuis fin 2026, postmarketOS est hébergé sous `nura.eco` (wiki.nura.eco, gitlab.nura.eco ; les anciennes adresses redirigent). Le wiki est protégé par Anubis et refuse les clients automatisés, mais `git clone` du dépôt pmaports fonctionne.

État du portage `samsung-a50` dans pmaports, d'après un clone du 2026-11-23 :
- **Catégorie `archived`, sans mainteneur** (`# Archived: unmaintained`, `maintainer=""`). Les paquets ne sont probablement plus construits par les dépôts binaires : il faudra sans doute tout compiler en local avec pmbootstrap. **À VÉRIFIER.**
- Noyau d'origine Samsung (fork du noyau constructeur) `linux-samsung-a50` 4.14.194, configuration tirée de `exynos9610-a50_core_defconfig`. Sa compilation demande `gcc6`, un paquet ancien qui risque de manquer.
- Flash : `heimdall-bootimg`. Le noyau va dans la partition `BOOT`, le rootfs dans `USERDATA` (image creuse, « sparse »). La partition BOOT est limitée à environ 55 Mo.
- `deviceinfo_external_storage="true"` : la microSD est prévue.
- Configuration du noyau : `CONFIG_USB_XHCI_HCD=y`, `CONFIG_USB_DWC3_DUAL_ROLE=y`, `CONFIG_USB_HOST_NOTIFY=y`. Le mode hôte USB est donc compilé, ce qui ne prouve pas qu'il fonctionne réellement.
- **2026-11-23 : `pmbootstrap build linux-samsung-a50` réussit** (pmbootstrap 3.12.1, canal edge, en 8 min sur un i5-9600KF). `gcc6-aarch64` est disponible précompilé : pas besoin de compiler le compilateur.
- **vbmeta** : le `deviceinfo` de l'A50 ne déclare pas de partition vbmeta. Or, avec un bootloader déverrouillé, les Samsung de cette époque refusent de démarrer un boot.img modifié tant que vbmeta n'est pas neutralisé (erreur `vbmeta HASH_MISMATCH`, boucle en mode Download). Les portages Samsung comparables le déclarent : S10 Exynos `beyond1lte-downstream` (2019, heimdall) `deviceinfo_flash_heimdall_partition_vbmeta="VBMETA"`, et A21s.
  - Solution : `pmbootstrap flasher flash_vbmeta --partition VBMETA`. Cette commande génère avec `avbtool make_vbmeta_image --flags 2` une image vbmeta dont la vérification est désactivée, puis la flashe avec heimdall. Elle passe **avant** `flash_kernel`.
  - **À VÉRIFIER** : le nom exact de la partition sur l'A50. En mode Download, lancer `heimdall print-pit --no-reboot | grep -i -A3 vbmeta`.
- Conséquence : l'option C est plus risquée que prévu (pas de maintenance, compilation complète). Elle reste peu coûteuse à essayer, car un retour au firmware d'origine reste possible.

| Test | Commande / méthode | Résultat | Notes |
|---|---|---|---|
| Démarrage | | | |
| USB networking (SSH depuis le PC) | `ssh user@172.16.42.1` | | |
| Wi-Fi | `nmcli device wifi connect …` | | |
| Charge et état de la batterie | `cat /sys/class/power_supply/*/uevent` | | |
| microSD montée, en écriture | `lsblk`, `dd` | | |
| **USB en mode hôte (OTG)** | `lsusb` avec le faux Nikon | | point bloquant |
| gphoto2 voit le faux Nikon | `gphoto2 --auto-detect` | | |

## Option B : GSI TrebleDroid
| Test | Résultat | Notes |
|---|---|---|
| Démarrage, Termux, Magisk | | |
| **USB en mode hôte (OTG)** | | |
| gphoto2 voit le faux Nikon | | |

## Option A : One UI nettoyé
| Test | Résultat | Notes |
|---|---|---|
| Root avec Magisk | | |
| **USB en mode hôte (OTG)** | | |
| gphoto2 voit le faux Nikon | | |

## Décision
_à remplir : option retenue, raisons, et ce qui reste à surveiller._
