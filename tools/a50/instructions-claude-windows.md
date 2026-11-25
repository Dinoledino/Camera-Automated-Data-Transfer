# Instructions pour Claude (Windows) : donner Internet au Galaxy A50 par USB et surveiller KnoxGuard

À transmettre tel quel à Claude Code sur la partition Windows du même PC.

## Contexte

- Téléphone : Samsung Galaxy A50, modèle **SM-A505FN**, Android 11 d'origine (One UI), relié au PC
  en USB, **débogage USB activé**. Il sert de prototype pour un projet de sauvegarde de photos.
- **Son Wi-Fi est en panne (matériel)** : il n'a Internet que si le PC le lui fournit par le câble.
- Objectif : que le téléphone ait Internet en continu, pour que le service Samsung **KnoxGuard**
  puisse joindre ses serveurs. Son état est actuellement « Checking » ; une vérification est
  programmée par le téléphone **environ toutes les 24 h (prochaine vers le 26/11 à 11:44)**. Le
  téléphone doit avoir Internet à ce moment-là, et idéalement en permanence.
- Outil : **gnirehtet** (Genymobile), « reverse tethering » : une application VPN sur le téléphone
  fait passer son trafic par adb vers le PC. Il fonctionne déjà sous Ubuntu sur ce PC ; le client
  Android (`com.genymobile.gnirehtet`) est **déjà installé** sur le téléphone.

## Ce qu'il ne faut PAS faire

- Ne rien flasher, ne pas redémarrer le téléphone en mode Download, ne pas réinitialiser le téléphone.
- Ne pas désinstaller ni désactiver d'application Samsung (en particulier `com.samsung.android.kgclient`).
- Ne pas accepter de conditions d'utilisation ou d'écran de configuration à la place de l'utilisateur :
  si le téléphone affiche une demande, la lui signaler.
- Ne rien envoyer d'identifiant du téléphone (numéro de série, IMEI) à un service extérieur.

## Étapes

1. **Pilote USB Samsung** : vérifier que Windows voit le téléphone en adb. S'il n'apparaît pas à
   l'étape 3, installer le pilote officiel « Samsung Android USB Driver for Windows » (site
   développeurs de Samsung), puis débrancher et rebrancher le câble.
2. **Télécharger gnirehtet pour Windows** (il contient adb) :
   `https://github.com/Genymobile/gnirehtet/releases/tag/v2.5.1`, fichier
   `gnirehtet-rust-win64-v2.5.1.zip`. Vérifier l'empreinte SHA-256 publiée avec la version si elle
   existe, puis le décompresser, par exemple dans `C:\outils\gnirehtet`.
3. Dans ce dossier, vérifier la connexion :
   ```powershell
   .\adb.exe devices -l
   ```
   Le téléphone doit apparaître avec `device` et `model:SM_A505FN`. S'il apparaît `unauthorized`,
   demander à l'utilisateur d'accepter « Autoriser le débogage USB » sur l'écran du téléphone
   (cocher « Toujours autoriser cet ordinateur »).
4. **Lancer le partage** :
   ```powershell
   .\gnirehtet.exe run
   ```
   (ou double-cliquer `gnirehtet-run.cmd`). Laisser cette fenêtre ouverte : c'est le relais.
   Au premier lancement sous Windows, le téléphone peut demander d'autoriser la connexion VPN de
   gnirehtet : l'utilisateur doit l'accepter sur l'écran.
5. **Vérifier que le téléphone a Internet** : dans la fenêtre du relais, des lignes
   `TcpConnection: 10.0.0.2:… -> …:443 Open` doivent apparaître. Et :
   ```powershell
   .\adb.exe shell "dumpsys connectivity" | Select-String "type: VPN\[\], state: CONNECTED"
   ```
   doit renvoyer une ligne.
6. **Lancer la surveillance** (étape suivante) dans une seconde fenêtre PowerShell, et la laisser
   tourner. Si Windows bloque les scripts : `Set-ExecutionPolicy -Scope Process Bypass` dans
   cette fenêtre uniquement.
7. **Empêcher la mise en veille du PC** pendant ce temps (Paramètres > Système > Alimentation),
   sinon le téléphone perd Internet.

## Script de surveillance (PowerShell)

Enregistrer sous `C:\outils\gnirehtet\surveillance-a50.ps1` et lancer depuis ce dossier avec
`.\surveillance-a50.ps1`. Il relance le client gnirehtet si le partage se coupe (constaté sous
Ubuntu : le client s'était déconnecté au bout de quelques minutes), et prévient à chaque changement
de l'état de KnoxGuard.

```powershell
# Surveillance du partage de connexion de l'A50 et de l'état KnoxGuard.
$ErrorActionPreference = "Continue"
$dir = $PSScriptRoot
$adb = Join-Path $dir "adb.exe"
$gnirehtet = Join-Path $dir "gnirehtet.exe"
$log = Join-Path $dir "knoxguard.log"
$alert = 'vaultState|isResultSuccess|StateToRpmb|Completed|Locked|Active'

function Note($text) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function KgDump { (& $adb shell "dumpsys knoxguard_service" 2>$null) -split "`r?`n" | Where-Object { $_ } }

$prev = KgDump
Note ("Départ. KnoxGuard : " + (($prev | Select-String -Pattern 'vaultState|StateToRpmb' | Select-Object -Last 1).Line))
$tick = 0
while ($true) {
    Start-Sleep -Seconds 60
    $state = (& $adb get-state 2>$null)
    if ($state -ne "device") { Note "Téléphone non détecté (débranché ?)"; continue }

    $vpn = & $adb shell "dumpsys connectivity" 2>$null | Select-String -Pattern 'type: VPN\[\], state: CONNECTED'
    if (-not $vpn) {
        Note "Partage de connexion coupé : relance du client gnirehtet"
        & $gnirehtet start 2>&1 | Out-Null
    }

    $tick++
    if ($tick % 2 -ne 0) { continue }          # KnoxGuard toutes les 2 minutes
    $now = KgDump
    if (-not $now) { continue }
    $new = $now | Where-Object { $prev -notcontains $_ }
    $prev = $now
    foreach ($l in $new) { Add-Content -Path $log -Value $l -Encoding UTF8 }
    $important = $new | Select-String -Pattern $alert
    if ($important) {
        Note ("CHANGEMENT KnoxGuard : " + ($important | Select-Object -Last 1).Line)
        [System.Media.SystemSounds]::Exclamation.Play()
    }
}
```

## Comment lire l'état de KnoxGuard

```powershell
.\adb.exe shell "dumpsys knoxguard_service" | Select-String "vaultState|StateToRpmb|isResultSuccess"
```

- `Prenormal`, ou `setCheckingStateToRpmb` sans `isResultSuccess : true` : vérification toujours en cours.
- `isResultSuccess : true`, ou un état comme `Completed` / `Normal` : probablement débloqué. La
  confirmation définitive se lit en mode Download (« KG STATE ») : **ne pas y passer soi-même**,
  laisser l'utilisateur décider.

## À rapporter à l'utilisateur

- si le partage fonctionne (lignes `TcpConnection … Open`) ;
- chaque changement noté par la surveillance (fichier `knoxguard.log`) ;
- toute demande affichée par le téléphone (autorisation USB, VPN).
