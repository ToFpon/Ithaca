# Ithaca

Gestionnaire de téléchargements pour GNOME (GTK 4 / libadwaita), inspiré de
[Varia](https://github.com/giantpinkrobots/varia), sans Flatpak : il suit le thème GTK du système
et utilise les programmes de la distribution.

État :
- fichiers HTTP / HTTPS / FTP avec aria2 (connexions multiples, pause, reprise même après la
  fermeture d'Ithaca, limite de vitesse, authentification, cookies) ;
- vidéos, musique et playlists (YouTube et plus de mille sites) avec yt-dlp, qu'Ithaca installe et
  tient à jour lui-même, avec choix de la qualité, du format, de l'audio seul et des sous-titres ;
- torrents et liens magnet avec libtorrent : choix des fichiers et de leurs noms, partage jusqu'au
  ratio choisi, chiffrement exigé, ouverture automatique du port ;
- notifications, interface en français, anglais et allemand.

À venir : icône de la barre système, planificateur, actions de fin de téléchargement, mode distant.

## Essayer sans installer

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 aria2 ffmpeg python3-libtorrent
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
