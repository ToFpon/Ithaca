# Ithaca

Gestionnaire de téléchargements pour GNOME (GTK 4 / libadwaita), inspiré de
[Varia](https://github.com/giantpinkrobots/varia), sans Flatpak : il suit le thème GTK du système
et utilise les programmes de la distribution.

État : étape 1 — téléchargements HTTP / HTTPS / FTP avec aria2 (connexions multiples, pause,
reprise même après la fermeture d'Ithaca, limite de vitesse, authentification, cookies),
notifications, interface en français, anglais et allemand. À venir : vidéos (yt-dlp), torrents
(libtorrent), icône de la barre système, planificateur, actions de fin de téléchargement.

## Essayer sans installer

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 aria2
git clone https://github.com/ToFpon/Ithaca.git
cd Ithaca
./bin/ithaca                       # ou : ./bin/ithaca https://exemple.fr/fichier.iso
```

## Installer pour l'utilisateur (~/.local)

```sh
./install.sh                # ./install.sh --uninstall pour retirer
```

Cible : Zorin OS 18 / Ubuntu 24.04 (GNOME 46, GTK 4.14, libadwaita 1.5).

Licence : GPL-3.0.
