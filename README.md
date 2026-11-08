# Ithaca

Un petit lecteur de musique pour GNOME (GTK 4 / libadwaita), qui joue votre musique et rien
d'autre. Inspiré d'[Amberol](https://gitlab.gnome.org/World/amberol), sans Flatpak : il suit le
thème GTK du système et utilise GStreamer de la distribution.

- Pas de bibliothèque à gérer : on ouvre des morceaux ou des dossiers (bouton, glisser-déposer,
  « Ouvrir avec » depuis le gestionnaire de fichiers) ; la file est retenue entre deux lancements.
- Listes de lecture .m3u, .m3u8, .xspf (et .pls) : ouvertes comme un dossier, file ou sélection
  enregistrée en liste (chemins relatifs : la liste suit la musique), ajout à une liste existante ;
  webradios des listes lues en direct, avec le titre annoncé par la station.
- Grande pochette, barre de progression en forme d'onde, aléatoire, répétition (file, morceau),
  recherche dans la file, enchaînement sans blanc entre les morceaux.
- ReplayGain (album ou morceau), contrôle depuis le panneau de GNOME / Zorin, l'écran verrouillé et
  les touches multimédia (MPRIS), lecture en arrière-plan avec icône dans la barre système (extension
  AppIndicator) et notification, pas de mise en veille pendant la lecture.
- Couleurs du thème du système, ou, en option, teintées d'après la pochette comme Amberol.
- Interface en français, anglais et allemand.

## Installer (Ubuntu 24.04 / 26.04, Zorin OS 18, Linux Mint 22)

```sh
sudo add-apt-repository ppa:nourpon/ithaca
sudo apt update && sudo apt install ithaca
```

Autres distributions basées sur Debian (Debian 13…) : le `.deb` de la page « Releases » de GitHub,
puis `sudo apt install ./ithaca_X_all.deb`.

## Essayer sans installer

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good
git clone https://github.com/ToFpon/Ithaca.git
cd Ithaca
./bin/ithaca                       # ou : ./bin/ithaca ~/Musique/un-album
```

Formats plus rares (WMA, APE…) : `sudo apt install gstreamer1.0-libav`.

## Installer pour l'utilisateur (~/.local)

```sh
./install.sh                # ./install.sh --uninstall pour retirer
```

Cible : Zorin OS 18 / Ubuntu 24.04 (GNOME 46, GTK 4.14, libadwaita 1.5, GStreamer 1.24).

Licence : GPL-3.0.
