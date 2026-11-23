# Phase 0bis : choix du système de l'A50

Ordre d'évaluation : **C** (postmarketOS) → **B** (GSI TrebleDroid vanilla) → **A** (One UI nettoyé, solution de repli).

## Identification du téléphone
| Élément | Valeur |
|---|---|
| Modèle exact (Paramètres > À propos) | _à remplir_ (SM-A505F / FN / …) |
| Version de One UI / Android | |
| Version du bootloader (binaire, ex. « U5 ») | |
| CSC (région) | |
| RAM / stockage | |
| Firmware d'origine téléchargé (nom du fichier, SHA-256) | |

## Option C : postmarketOS
État du portage `samsung-a50` : **À VÉRIFIER**. Le 2026-11-23, wiki.postmarketos.org et gitlab.postmarketos.org redirigeaient (301/308) vers les domaines `wiki.nura.eco` et `gitlab.nura.eco`. Il faut confirmer que ce sont bien les adresses officielles avant de s'y fier.

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
