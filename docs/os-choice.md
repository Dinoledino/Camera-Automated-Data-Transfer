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
| Firmware d'origine | **Téléchargé le 2026-11-23 avec Bifrost 2.1.4.** Fichier `SM-A505FN_2_20230304140301_rqrfoux5q6_fac_A505FNXXU9CWB4_A505FNOXM9CWB4_A505FXXU9CWB1_A505FNXXU9CWB4_XEF.zip` (4,7 Go), SHA-256 `de61b36d574ac83995f784eb89ff8d61035911ba8d6edeedcc85f26d81c24796`, CRC du zip OK. Contenu : BL, AP, CP, CSC et HOME_CSC en `A505FNXXU9CWB4` / CP `A505FXXU9CWB1`. Retour à l'origine : BL + AP + CP + **CSC** (pas HOME_CSC). `vbmeta.img` est présent dans BL et dans AP. Rangé hors du dépôt, dans `~/Bureau/projets/DispositifNikon/` |

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
  - **Vérifié le 2026-11-23** avec `heimdall print-pit` (sortie complète dans `docs/a50-pit.txt`). Blocs UFS de 4096 octets :
    - `VBMETA` : 16 blocs, soit 64 Kio ;
    - `BOOT` : 14 080 blocs, soit **55,0 Mio** (57 671 680 octets). Le `boot.img` OpenRC fait 50 784 256 octets : il reste environ 6,9 Mo de marge ;
    - `USERDATA` : 0 bloc, c'est-à-dire tout l'espace restant du disque.
  - Le mode Download s'identifie en USB comme `04e8:685d`. À la première tentative, la détection USB ne s'est pas terminée : il a fallu débrancher puis rebrancher le câble, le téléphone restant en mode Download.
- Conséquence : l'option C est plus risquée que prévu (pas de maintenance, compilation complète). Elle reste peu coûteuse à essayer, car un retour au firmware d'origine reste possible.

### Essai du 2026-11-23 : échec au démarrage
Déroulé et pièges rencontrés :
- `flash_vbmeta` échoue avec `avbtool: not found` : le chroot natif ne l'installe pas. Contournement : `pmbootstrap chroot -- apk add android-tools-avbtool`.
- **heimdall échoue sur les gros envois vers `USERDATA`** : arrêt systématique vers 46 % en sparse et 16 % en raw, quels que soient le câble et le port. Une image d'environ 247 Mo passe. L'image a été réduite avec `ui none`, `install --no-recommends` et un minimum d'`extra_packages`. Il faut **supprimer réellement** `chroot_rootfs_samsung-a50` : `pmbootstrap zap` n'avait fait qu'arrêter le chroot.
- Thor (alternative à heimdall) : le téléchargement du PIT échoue (timeout). Abandonné.
- Après une session heimdall ratée, il faut redémarrer le téléphone en mode Download avant de réessayer. `--resume` ne sert que si la commande précédente a utilisé `--no-reboot` et a réussi.
- Résultat : **boucle infinie sur les écrans d'avertissement**, sans aucun périphérique USB côté PC. Ni le passage de `os_version` / `os_patch_level` à 11.0.0 / 2023-03, ni le flash de notre propre DTB n'y changent rien.

**Cause probable : KnoxGuard, pas le portage.** En mode Download : `KG STATE: Checking`, `Current Binary: Custom (0x307)`, `OEM LOCK: OFF (U)`. Le même comportement s'observe avec **TWRP officiel** (3.7.0_11-0-a505f) placé dans `BOOT`, alors que le `boot.img` d'origine, signé, démarre jusqu'au logo Samsung. Le bootloader accepte donc les flashs mais refuse de démarrer tout binaire non signé tant que KG n'est pas sorti de « Checking ». **À VÉRIFIER** : le portage pmOS pourrait démarrer une fois KG débloqué.

Remise à l'origine : les images `boot`, `recovery`, `vbmeta` et `dt` ont été extraites de l'AP (archives `.lz4`, décompression en Python) et flashées avec heimdall. `SYSTEM` et `VENDOR` n'avaient jamais été modifiés.

**Déblocage de KG en cours.** Sans Wi-Fi, le téléphone accède à Internet par USB grâce à [gnirehtet](https://github.com/Genymobile/gnirehtet) (`gnirehtet run`, puis accepter la demande de VPN sur le téléphone). Le client `kgclient` contacte les serveurs de Samsung dès qu'une connexion apparaît. Pour suivre son état sans passer en mode Download : `adb shell dumpsys knoxguard_service | grep -i vaultState`. `Prenormal` correspond à « Checking ».

**Limite matérielle : Wi-Fi et Bluetooth hors service.** Wi-Fi : `Could not set interface flags for wlan0 (Invalid argument)`, puis `Failed to start legacy HAL`. Bluetooth : `Open h4 device failed: Invalid argument`, puis le service redémarre en boucle. Les firmwares (`/vendor/etc/wifi/mx140.bin`) sont intacts. Avant nos modifications, le Wi-Fi ne fonctionnait déjà qu'environ une fois sur cinq : c'est probablement la puce combo Wi-Fi/BT qui se dégrade. **Conséquence pour le projet : tout passe par USB**, aussi bien le D850 vers le téléphone que le PC vers le téléphone.

| Test | Commande / méthode | Résultat | Notes |
|---|---|---|---|
| Démarrage | | ❌ | boucle d'avertissements, KG « Checking » |
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
