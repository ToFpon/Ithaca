# Ithaca — notes de travail

Lecteur de musique GTK4 / libadwaita en Python, équivalent d'Amberol (`World/amberol` sur
gitlab.gnome.org, GPL-3.0, Rust) sans Flatpak, avec GStreamer de la distribution. Amberol exige
GTK 4.16 (Zorin : 4.14) et des variables CSS (`var(--…)`) absentes de GTK 4.14 : Ithaca est une
réécriture, pas un portage. Ossature reprise d'Odysseus (`ToFpon/Odysseus` : i18n, réglages,
fonctionnalités, vrais dialogues, tests). Le dépôt hébergeait avant un gestionnaire de
téléchargements (abandonné, historique repris de zéro à la demande de Tof).

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, GStreamer 1.24,
  Python 3.12). Pas d'API plus récente (pas d'Adw.Spinner, Adw.Dialog, Adw.AlertDialog…). Paquets
  apt seulement.
- Suivre le thème GTK du système : `data/base.css` ne fait que de la mise en page, aucune couleur fixe
  (seulement des teintes tirées du thème : `@accent_bg_color`, `alpha(currentColor, …)`). Seule
  exception, facultative et coupée par défaut : la recoloration d'après la pochette (`recolor`).
- Dialogues : de vraies fenêtres comme Nautilus 46 — `common.AlertDialog` (Adw.MessageDialog),
  `common.DialogWindow`, `Adw.PreferencesWindow`, `Adw.AboutWindow`. Jamais Adw.Dialog / Adw.AlertDialog.
- Chaque fonctionnalité facultative est déclarée dans `ithaca/features.py` (waveform, mpris,
  background, recolor) et reste désactivable sans redémarrage (`features.enabled(id)` au moment de
  l'usage, réglage `features` suivi en direct par la fenêtre et l'application).
- Tout texte visible passe par `tr()` / `ntr()` (jamais `_`). Après modification :
  `python3 tools/update-pot.py`, compléter `data/locale/en.po` et `de.po`, puis
  `msgfmt -c --statistics` et `msgcmp` (aucune chaîne non traduite, placeholders identiques).
  Un même texte français ne doit pas porter deux sens (« Lecture » = Playback ; le bouton dit « Lire »).
- Jamais `GLib.idle_add(widget.grab_focus)` (rappel sans fin) : `common.focus_later(widget)`. Toute
  fonction passée à `idle_add` / `timeout_add` renvoie False (sauf répétition voulue).
- Lancer sans installer : `bin/ithaca` (ou `python3 -m ithaca`). `install.sh` installe dans ~/.local
  (code dans `~/.local/lib/ithaca`, jamais dans `~/.local/share/ithaca` qui contient la file ;
  `--uninstall` n'y touche pas).
- Commandes (pour que Tof n'ait pas à valider chaque commande ; conseil de l'agent d'Odysseus) :
  fichiers écrits et modifiés avec les outils Edit / Write, jamais par redirection shell (`cat >`,
  `echo >>`, heredoc). Une seule action git par commande, avec `git -C /chemin/du/depot` : le commit,
  puis chaque push séparément ; jamais `cd … && git …`, jamais d'option `git -c` (identité réglée une
  fois par `git config user.name` / `user.email` dans le dépôt). Message de commit long : écrit dans un
  fichier avec Write (bloc-notes de la session), puis `git -C … commit -F fichier`. Pas de commandes
  enchaînées par `&&` / `;` / `|` quand une commande simple suffit. Une demande revient quand même : lire
  la commande exacte qu'elle cite (presque toujours un enchaînement ou une redirection).

## Publication (comme Odysseus)

- PPA `ppa:nourpon/ithaca` (https://launchpad.net/~nourpon/+archive/ubuntu/ithaca) : `tools/ppa.sh`
  (repris d'Odysseus) envoie l'entrée de tête de `debian/changelog` pour noble (24.04, « X~noble1 »)
  puis resolute (26.04, « X~resolute1 », réécrite dans une copie). Tourne chez Tof (sa clé GPG,
  `dput`) ; `--build` construit et signe sans envoyer ; `--only SÉRIE` reconstruit et renvoie une
  seule série, sans le site (1.1.0 : « erreur 500 » de Launchpad sur noble, finalement passée après
  coup — vérifier la page des paquets avant de renvoyer ; un envoi vraiment refusé ne consomme pas la
  version, on renvoie la même). Nouvelle version : `dch -v X~noble1 "…"`,
  `dch -r`, version dans `ithaca/__init__.py` et `<release>` dans la fiche AppStream, commit
  « Version X », push, `tools/ppa.sh`.
- `.github/workflows/release-deb.yml` : au push de `debian/changelog` sur main, .deb « X~deb1 » (plus
  ancien que « ~noble1 » : le PPA prend le relais) publié dans les Releases, notes tirées de la fiche
  AppStream (`data/io.github.ithaca.Ithaca.metainfo.xml`, en anglais).
- Paquet : code privé dans /usr/share/ithaca (dh_python3), lanceur `/usr/bin/python3 -P -c …`
  (debian/bin/ithaca), service D-Bus (.desktop en DBusActivatable), page de manuel. Vérifié ici :
  dpkg-buildpackage (binaire et source), lintian sans remarque, appstreamcli validate (seule note
  pédante : majuscules de l'identifiant, comme Odysseus), installé puis lancé (le `/usr/bin/python3`
  du bac à sable est un 3.13 sans gi : lancer le même lanceur avec python3.12).
- Site de présentation (`site/index.html`, anglais, tons violets pour se distinguer du bleu d'Odysseus,
  même gabarit) : en ligne sur https://tofmicro.pages-perso.free.fr/Ithaca/, envoyé par FTP à la fin de
  `tools/ppa.sh` via `tools/site.sh` (copie de celui d'Odysseus : lftp, identifiants dans ~/.netrc de
  Tof, `--transfer-all`, jamais de suppression ; `--essai` montre sans envoyer). Version dans la pastille
  de la barre et l'accroche (« v1.3.2 », « Version 1.3.2 ») : à tenir à jour. « Copy » ne copie que les deux
  commandes d'installation. Captures de `site/img/` en anglais et en sombre (sauf `light.png`) :
  `LANGUAGE=en … tools/screenshot.py site/img/NOM.png --delay=6000 --dark --size=1000x680 [options]` —
  dark, light (sans `--dark`), cover-colors (`--recolor`), search (`--search=pen`), selection, features
  (`--prefs=features`), welcome (`--empty`), compact (`--size=380x720`) ; `icon.svg` = icône couleur.

## Choix de Tof (comme Amberol)

- Pas de bibliothèque : fichiers et dossiers ouverts (boutons, glisser-déposer, « Ouvrir avec »,
  ligne de commande) ; file retenue, proposée par « Restaurer la file » sur la page d'accueil.
- Barre en forme d'onde, MPRIS + touches multimédia, ReplayGain (album / morceau / désactivé),
  lecture en arrière-plan. Thème système par défaut, recoloration d'après la pochette en option.

## Architecture

- `backend/song.py` : étiquettes par `GstPbutils.Discoverer` (~30 ms par fichier, dans un fil :
  `load_paths`, paquets de 50 renvoyés à la boucle GLib). Étiquettes fusionnées de tous les flux
  (`get_tags` du DiscovererInfo est déprécié). Pochette intégrée écrite une fois dans
  `~/.cache/ithaca/covers/<sha1 des octets>` (un fichier par album), sinon image du dossier
  (`cover`, `folder`, `front`… puis image seule). `Song.cover` est toujours un chemin : il sert aussi
  à MPRIS (`mpris:artUrl`). Dossiers parcourus en ordre naturel (`GLib.utf8_collate_key_for_filename`).
- `backend/playqueue.py` : logique pure. `original` (ordre d'ajout) et `items` (ordre de lecture =
  affichage) ; mélanger met le morceau courant en tête et tire le reste ; un morceau n'est qu'une
  fois dans la file. Répétition : consecutive (s'arrête), all, one. Retirer le morceau courant : le
  suivant prend sa place. `queue.json` : chemins, ordre mélangé, morceau courant, position.
- `backend/player.py` : playbin, drapeau vidéo coupé (une pochette intégrée à un MP3 est un flux
  vidéo : playbin ouvrait une fenêtre). Sans blanc : `about-to-finish` (fil de GStreamer) donne
  l'adresse de `next_song`, préparée d'avance dans la boucle GLib ; le changement effectif arrive par
  STREAM_START. ReplayGain = élément `volume` en audio-filter réglé par nous (gain des étiquettes,
  crête respectée, repli album ↔ morceau), changeable en direct sans couper le son. Volume cubique
  (`GstAudio.StreamVolume.convert_volume`). `ITHACA_AUDIO_SINK` remplace la sortie (tests).
- `backend/controller.py` : file + lecteur, événements song / state / position / queue / modes /
  volume / loading / error / ended. « Précédent » après 3 s : retour au début. Fichier illisible :
  message et morceau suivant (arrêt si tout est illisible). Enregistrement différé de 0,7 s, et
  toutes les 5 s pendant la lecture (position).
- `backend/waveform.py` : `uridecodebin ! audioconvert ! level` sans synchronisation, une crête par
  250 ms, cache `~/.cache/ithaca/waveforms/<sha1 chemin|taille|date>.json` ; un calcul à la fois.
- `backend/mpris.py` : org.mpris.MediaPlayer2(.Player) sur `org.mpris.MediaPlayer2.ithaca` ; les
  propriétés passent par les fonctions get / set de `register_object` (elles marchent en Python,
  contrairement à ce que laissait penser Odysseus) ; PropertiesChanged à chaque événement, Seeked
  sur un saut. Démarré / arrêté en direct (`features` « mpris »), seulement avec un bus de session.
- `ui/window.py` : disposition d'Amberol (Stack accueil / lecteur, OverlaySplitView avec la file en
  panneau, replié sous 560 sp ; pochette réduite à 180 px sous 680 sp de haut). Afficher / masquer la
  file (F9, bouton de la barre de titre comme Odysseus, bouton du bas, retour de la file) passe par
  `MainWindow.set_playlist_shown` : masquée → fenêtre au format compact (`COMPACT_WIDTH`, le lecteur
  seul comme la capture « compact » du site — choix de Tof, pas seulement « moins la file ») ;
  réaffichée → largeur d'avant (`_wide`), ou élargie de la file si redimensionnée entre-temps (demande
  de Tof : masquer laissait une grande fenêtre vide) ; pas de
  changement agrandie / plein écran ; `set_default_size` compte le cadre CSD (10 px), `get_width` non.
  Calcul sur la taille DEMANDÉE (`get_default_size` − cadre), jamais sur la taille mesurée : sous un
  vrai gestionnaire de fenêtres le redimensionnement arrive en différé, des clics rapides faisaient
  grandir la fenêtre. Mode « replié » (file par-dessus le lecteur) : le point de rupture n'a plus de
  setter, ses signaux apply / unapply passent par `_set_narrow` — replié seulement si la fenêtre est
  étroite ET la file affichée ; on élargit pour afficher → `collapsed` remis à False avant. Changer de
  mode en pleine animation donnait « Trying to snapshot AdwGizmo … without a current allocation » (vu
  chez Tof) ; ui_smoke échoue sur cet avertissement. Rétrécir la fenêtre à la main : libadwaita masque
  la file (normal). File à gauche (défaut, comme Amberol) ou à droite : réglage `playlist_side`
  start / end, Préférences → Apparence (idée de Tof, gauchers / droitiers, repris de Nausicaa) →
  `split.set_sidebar_position` en direct ; `_sync_side` repose le bouton de la file du côté de la file
  (pack_start / pack_end, icône sidebar-show(-right)) et tourne la flèche de fermeture. Boutons de la
  fenêtre : libadwaita les met d'elle-même sur la barre du bon bord — la barre de la file ne force
  plus show_*_title_buttons à False. `tools/screenshot.py --right` ; testé : `cote_de_la_file`.
  Testé : `replier_la_file` d'ui_smoke, AUSSI sous un vrai gestionnaire de fenêtres
  (xvfb seul redimensionne tout de suite et ne montrait rien) : `apt install openbox`, openbox lancé en
  fond dans le xvfb-run avant le test. `ui/waveform_view.py`
  : Gtk.Widget dessiné (`do_snapshot`, couleur d'accent / texte estompé du thème), clic, glisser,
  ← / →. `ui/playlist_view.py` : Gtk.ListView (seules les lignes visibles existent), recherche sans
  accents (`unicodedata`, les écritures non latines restent intactes), mode sélection. Glisser pour
  réordonner (demande de Tof) : Gtk.DragSource / DropTarget sur chaque ligne, contenu = CHAÎNE
  « ithaca-song:<id> » (sous X11 même un glisser interne passe par XDND, types MIME seulement),
  ligne d'insertion `.drop-above` / `.drop-below` (couleur d'accent), défilement près des bords
  (DropControllerMotion sur le ScrolledWindow), pas en mode sélection. `move_song` déplace la ligne
  dans le Gio.ListStore puis `Controller.move` ; `refresh` ne reconstruit plus la liste si l'ordre est
  déjà le bon (la reconstruire ramenait en haut). PIÈGE : un Gtk.WindowHandle autour de TOUTE la
  fenêtre s'emparait du geste (la fenêtre bougeait, rien n'était déposé) ; il n'entoure plus que le
  lecteur et la page d'accueil. Testé : `reordonner` d'ui_smoke, et `tests/ui_drag.py` (vrai glisser à
  la souris par xdotool, joué depuis un autre fil : la boucle GTK doit traiter les mouvements).
  `ui/marquee.py` (demande de Tof) : titre du lecteur en texte défilant s'il ne tient pas — Gtk.Widget
  qui porte un Gtk.Label (largeur minimale 0 : un titre long n'élargit jamais la fenêtre), pause 2 s,
  aller à 30 px/s, pause, retour (`scroll_offset`), tick callback seulement quand le texte déborde,
  fondu des bords par `push_mask` ; animations coupées dans le système → « … » comme avant. Même usage
  qu'un Gtk.Label (`set_label` / `get_label`). Testé : `titre_defilant` d'ui_smoke ; capture
  `tools/screenshot.py … --title=TEXTE`.
  `ui/cover.py` : pochettes carrées (recadrées au centre, Gtk.Image à taille fixe — un Gtk.Picture
  prenait la taille naturelle de l'image), cache de textures, recoloration (découpage médian,
  3 couleurs, fournisseur CSS remplacé à chaque pochette).
- `app.py` : instance unique (GApplication), `HANDLES_OPEN` + ligne de commande (ajouter et lire),
  pas de mise en veille pendant la lecture (`inhibit` SUSPEND), arrière-plan : fenêtre masquée +
  `hold()`, MPRIS Raise ou un nouveau lancement la font revenir. Pour en sortir (choix de Tof, le
  contrôle média de GNOME n'a pas de « Quitter ») : icône dans la barre système + notification
  « Ithaca continue de jouer » (Afficher / Quitter), retirées quand la fenêtre revient.
  SIGINT / SIGTERM / SIGHUP : `GLib.unix_signal_add` → `_quit` (file enregistrée, pas de trace
  KeyboardInterrupt ; Ctrl+C dans le terminal).
- `backend/tray.py` : icône AppIndicator sans libayatana-appindicator (GTK 3, incompatible avec
  GTK 4 dans le même processus) : StatusNotifierItem + com.canonical.dbusmenu écrits à la main,
  enregistrés auprès de org.kde.StatusNotifierWatcher (chez Tof : extension « AppIndicator and
  KStatusNotifierItem Support » d'Ubuntu, ubuntu/gnome-shell-extension-appindicator — il a quitté
  celle de Zorin, trop boguée) ; pas de
  barre : rien ne s'affiche, rien ne casse. Icône = celle de l'application (`io.github.ithaca.Ithaca`,
  choix de Tof après un essai d'icône violette dédiée, abandonné) ; shell réglé en icônes symboliques
  (`-st-icon-style: symbolic` dans gnome-shell.css) → l'extension prend d'elle-même
  `io.github.ithaca.Ithaca-symbolic` = note de musique (emblem-music-symbolic du thème d'icônes
  kora de bikass, GPL-3.0 : crédité dans debian/copyright, le README et « À propos » ; couleur
  simple — sa feuille de style KDE retirée), aussi sur la page d'accueil. « À propos » remercie aussi
  Ponce-De-Leon (https://forum.zorin.com/u/Ponce-De-Leon/), le testeur de Tof. Les deux installées
  (paquet, install.sh, qui retire l'ancienne `-tray`) → IconThemePath vide, la barre les prend dans le
  thème du système ;
  lancé sans installer → IconThemePath = data/icons, qui DOIT garder `hicolor/index.theme` (l'extension
  cherche avec St.IconTheme, logique de GTK 3 : sans index.theme rien n'est trouvé, icône d'attente
  « ••• » vue chez Tof ; GTK 4 la trouvait quand même, d'où le test `test_icon_resolvable`) ; menu
  (titre en cours, Afficher, Lire / Pause, Suivant, Précédent, Quitter) republié par LayoutUpdated
  à chaque événement.

## Listes de lecture et webradios

- `backend/playlists.py`, repris du module d'Odysseus (`backend/playlist.py`) : lecture .m3u / .m3u8 /
  .xspf / .pls (relatifs, absolus, file://, chemins Windows « \\ », vieux .m3u en cp1252, XSPF à
  adresses relatives encodées), écriture .m3u / .m3u8 / .xspf en chemins RELATIFS (la liste suit la
  musique sur une clé), #EXTINF / champs XSPF tirés des étiquettes déjà lues (pas de ffprobe).
  Écriture sûre : copie cachée `.ithaca-*.tmp` + fsync + `os.replace` + fsync du dossier, droits
  gardés. Compléter = contenu gardé tel quel, doublons par chemin réel. Extension ajoutée par nous
  (« Ma liste » → « .m3u ») : jamais d'écrasement (`free_name`) — la fenêtre n'a confirmé que l'autre nom.
- Ouvrir une liste = l'ouvrir comme un dossier (`song.expand` la déplie) ; morceau absent compté
  « ignoré ». Enregistrer : menu (« Enregistrer la file… », « Ajouter la file à une liste… ») et bouton
  de la sélection ; `MainWindow.write_playlist` / `append_to_playlist` (sans dialogue, testés).
- Webradios (choix de Tof) : `Song.stream` (path = adresse), jamais analysées (un flux ne finit pas),
  nom de la station = titre de la liste, sinon nom du serveur, retenu dans queue.json (`titles`).
  Titre en cours : étiquette TAG (ICY) → `Song.now_playing`, affiché en grand, station en dessous
  (`window.artist_line`) ; « En direct », ni forme d'onde ni avance. Testé avec un MP3 servi en HTTP.
  Validé chez Tof avec de vraies webradios en .m3u, ainsi que l'ouverture et l'enregistrement des listes.
- Logos des webradios (`backend/logos.py`, demande de Tof) : `tvg-logo="…"` des #EXTINF
  (`playlists.parse_extinf` : titre après la première virgule HORS guillemets, un attribut peut en
  contenir), `<image>` des .xspf → `Song.logo` ; à défaut le site annoncé par le flux : en-tête
  « icy-url », que souphttpsrc range dans l'étiquette `location` (pas `homepage`) → `Song.site`, et
  son icône (`apple-touch-icon` puis `icon` de la page, puis /favicon.ico ; 48 px au moins, un favicon
  de 16 px serait flou). Un fil (`logos.fetcher`, une demande à la fois, 3 Mo et 10 s au plus),
  image contrôlée (GdkPixbuf), posée au centre d'un carré OPAQUE (`squared_png` : couleur de son bord,
  blanc si transparente — texte sombre illisible sur thème sombre ; la pochette est recadrée en carré,
  un logo large aurait été rogné), PNG dans ~/.cache/ithaca/logos/<sha1 adresse>.png. Arrivée →
  `Song.cover` + événement « cover » (fenêtre, vignette de la file, MPRIS). logo / site retenus dans
  queue.json (`logos`, `sites`) : au retour, pris du cache sans réseau ; réécrits à l'enregistrement
  d'une liste (tvg-logo, <image> avant <album> comme le veut XSPF). Testé : `LogoTest`,
  `StreamLogoTest` (logo de la liste, icône du site par icy-url, page HTML refusée).

## Tests

Le `python3` du bac à sable n'a pas `gi` : utiliser `python3.12`.

```sh
python3.12 -m unittest discover -s tests            # moteur, listes, webradio (vrai GStreamer, sortie factice)
dbus-run-session -- python3.12 -m unittest discover -s tests -p "test_mpris.py"   # MPRIS
dbus-run-session -- python3.12 -m unittest discover -s tests -p "test_tray.py"    # icône, Ctrl+C
xvfb-run -a python3.12 tests/ui_smoke.py            # interface : doit finir par « Aucune erreur. »
LANGUAGE=en xvfb-run -a python3.12 tests/ui_smoke.py
LANGUAGE=de xvfb-run -a python3.12 tests/ui_smoke.py
xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_drag.py   # vrais glisser et molette (xdotool)
xvfb-run -a python3.12 tests/ui_keyboard.py         # F1 / Ctrl+? sur claviers us, de, fr (setxkbmap)
python3 -m pyflakes ithaca tests tools
```

`tests/media.py` fabrique la musique de test avec ffmpeg (étiquettes, pochette intégrée ou
`cover.jpg`, ReplayGain, fichier sans étiquette, faux MP3). Sortie `fakesink sync=true` : la lecture
prend le temps réel, d'où des morceaux de 1 à 2 s. Le sinus de ffmpeg est à -18 dB (crête 0,125).
`test_tray.py` : fausse barre (StatusNotifierWatcher) et vrai Ctrl+C sur un Ithaca lancé par
xvfb-run — viser le processus Python par `^[^ ]*python…` (la ligne de commande de xvfb-run contient
aussi « bin/ithaca » : c'est lui qui recevait le signal) ; les modules de test importés
(test_backend) redéfinissent les dossiers XDG à l'import : lire le chemin réellement transmis.
Tests MPRIS : proxys clients en `DO_NOT_LOAD_PROPERTIES` et appels asynchrones (un appel synchrone
vers son propre service, dans le même fil, attend jusqu'au délai).

Captures (Gtk.WidgetPaintable, configuration jetable, album d'exemple généré) :
`xvfb-run -a python3.12 tools/screenshot.py SORTIE.png [--empty] [--dark] [--recolor] [--size=LxH]
[--prefs=PAGE] [--hidden-list] [--search=TEXTE] [--selection] [--delay=ms]`.
Captures du README dans `docs/captures/` (en français, comme Odysseus ; README anglais puis français) :
`LANGUAGE=fr … tools/screenshot.py docs/captures/NOM.png --delay=6000 [options]` — accueil, sombre
(`--dark`), pochette (`--dark --recolor`), compact (`--size=380x720`), recherche (`--search=pén`), selection
(`--selection`), vide (`--empty`), fonctionnalites (`--prefs=features`). Album de démonstration : deux albums
de l'Odyssée (Ulysse, Pénélope), pochettes Mandelbrot, sons au volume modulé (forme d'onde vivante).

## Pièges connus

Le CLAUDE.md d'Odysseus (`ToFpon/Odysseus`) recense beaucoup de pièges de GTK 4.14 / libadwaita 1.5
vécus en vrai (raccourcis, focus, menus, boutons latéraux de la souris, ColumnView…) : le relire
avant de toucher à ces sujets.

- Raccourcis de l'application (Ctrl+← / →, Maj+← / →) : GTK 4 les traite AVANT le champ qui a le
  focus. `MainWindow._on_key` (contrôleur en capture) les rend à `Gtk.Text` par `move-cursor` ;
  testé dans ui_smoke (recherche de la file). Tout nouveau raccourci sur une touche d'édition doit
  y passer.
- Préférences → Lecture, repris de Nausicaa (demande de Tof) : « Volume maximal » (`volume_max` 100 /
  150 : curseur cubique jusqu'à 1,5, son amplifié comme VLC, repère des 100 % ; repasser à 100 %
  ramène le volume — `Controller.max_volume`, `Player.MAX_VOLUME`, MPRIS accepte > 1), « Saut avec
  ← / → » (`seek_step` 5 / 10 / 30 s, 10 par défaut : Maj+← / → et flèches de la forme d'onde ;
  fenêtre des raccourcis RECONSTRUITE au changement, libellés « Avancer de N secondes » en `ntr`),
  « Cran de molette » (`wheel_step` 2 / 5 / 10 % : molette = volume sur TOUT le lecteur, demande de Tof
  — EventControllerScroll en capture sur la ToolbarView du lecteur, avant le Gtk.Scale dont le pas ne
  se règle pas ; pas sur la file, qui défile ; et sur l'icône de la barre système). Testé :
  `reglages_lecture` d'ui_smoke, vraie molette dans `tests/ui_drag.py` (pochette / file).
- Fenêtre des raccourcis (`set_help_overlay`) : GTK n'attache Ctrl+? tout seul qu'à un help-overlay.ui
  des RESSOURCES — jusqu'en 1.3.1 il n'était branché nulle part (retour de Tof). Maintenant
  `set_accels_for_action("win.show-help-overlay", ["F1", "<Control>question"])` + filet repris de
  Nausicaa (`MainWindow._help_key`, appelé en tête de `_on_key` : caractère réel de la touche par
  `display.translate_key`, Ctrl retiré — « ? » = Maj+ß en QWERTZ, Maj+, en AZERTY). Testé :
  `tests/ui_keyboard.py` (setxkbmap us / de / fr + xdotool, avec et sans le raccourci de GTK, F1).
- Espace = lecture / pause partout, même si un bouton a le focus (`_on_key`, en capture) : un bouton
  cliqué garde le focus et Espace le « recliquait » (suivant, aléatoire…), retour de Tof. Exceptions :
  champs de texte, menus ouverts (ancêtre Gtk.Popover), cases du mode sélection. Testé dans ui_smoke et
  avec un vrai clic + vraie touche (xdotool sous openbox).

- `css_classes=[…]` au constructeur d'une fenêtre REMPLACE ses classes (« background »…) : texte
  blanc sur fond blanc. Toujours `add_css_class` pour une fenêtre.
- Textes venus de l'extérieur (noms de fichiers, étiquettes) : la description d'`Adw.StatusPage` et le
  titre d'`Adw.Toast` sont du balisage Pango → `GLib.markup_escape_text` / `toast.set_use_markup(False)`
  (`common.toast` le fait).
- Menus contextuels (`common.popup_menu`) : un seul Gtk.PopoverMenu par widget, gardé et réutilisé,
  jamais détruit à la fermeture (plantage de GTK 4.14 sinon, vu dans Odysseus).
- Nom du processus : `app._process_name` (prctl PR_SET_NAME) → « ithaca » dans ps / killall.
  WM_CLASS : `GLib.set_prgname(APP_ID)`.
