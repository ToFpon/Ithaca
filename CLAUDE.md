# Ithaca — notes de travail

Gestionnaire de téléchargements GTK4 / libadwaita en Python, équivalent de Varia
(`giantpinkrobots/varia`, MPL-2.0) sans Flatpak : fichiers (aria2), vidéos (yt-dlp), torrents
(libtorrent). Ossature reprise d'Odysseus (`ToFpon/Odysseus` : i18n, réglages, fonctionnalités,
dialogues, tests). Varia ne tourne pas hors Flatpak sur Zorin : elle exige libadwaita 1.6 / 1.7
(Adw.Dialog, AlertDialog, ToggleGroup, WrapBox, Spinner…) et embarque ses propres moteurs ; Ithaca
est une réécriture, pas un portage.

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, Python 3.12).
  Pas d'API plus récente (pas d'Adw.Spinner, Adw.Dialog, Adw.AlertDialog, Adw.ToggleGroup…).
  Dépendances : paquets apt d'Ubuntu 24.04 (aria2 1.37, python3-libtorrent 2.0.10, ffmpeg) ; seul
  yt-dlp sera un binaire géré par Ithaca (celui d'apt est périmé et casse face à YouTube).
- Suivre le thème GTK du système : `data/base.css` ne fait que de la mise en page, aucune couleur fixe.
  La zone de la liste utilise le fond de la fenêtre (pas `view`) ; les cartes prennent `card` du thème.
- Dialogues : de vraies fenêtres comme Nautilus 46, pour que le thème du système (fond, transparence,
  bordure) s'y applique — `common.AlertDialog` (Adw.MessageDialog), `common.DialogWindow` (remplace
  Adw.Dialog, même usage), `Adw.PreferencesWindow`, `Adw.AboutWindow`. Jamais Adw.Dialog /
  Adw.AlertDialog (feuilles dessinées dans la fenêtre).
- Chaque fonctionnalité facultative est déclarée dans `ithaca/features.py` et doit rester
  désactivable sans redémarrage (`features.enabled(id)` au moment de l'usage, pas à l'import).
- Tout texte visible passe par `tr()` / `ntr()` (jamais `_`). Après modification :
  `python3 tools/update-pot.py`, compléter `data/locale/en.po` et `de.po`, puis
  `msgfmt -c --statistics` et `msgcmp` (aucune chaîne non traduite, placeholders identiques).
- Jamais `GLib.idle_add(widget.grab_focus)` (rappel sans fin) : `common.focus_later(widget)`. Toute
  fonction passée à `idle_add` / `timeout_add` renvoie False (sauf répétition voulue).
- Lancer sans installer : `bin/ithaca` (ou `python3 -m ithaca`). `install.sh` installe dans ~/.local
  (code dans `~/.local/lib/ithaca`, jamais dans `~/.local/share/ithaca` qui contient les données ;
  `--uninstall` ne touche pas à la liste des téléchargements).

## Feuille de route (choix de Tof)

1. ✅ Fenêtre comme Varia, téléchargements HTTP / FTP par aria2, préférences, fr / en / de.
2. ✅ Vidéos et audio : yt-dlp et Deno en binaires officiels téléchargés et mis à jour par Ithaca
   (~/.local/share/ithaca/bin), ffmpeg d'apt ; fenêtre de choix comme Varia ; playlists dans un
   sous-dossier.
3. Torrents : python3-libtorrent (choix des fichiers, partage / ratio), .torrent et magnet
   (Ithaca déclaré pour application/x-bittorrent et x-scheme-handler/magnet). Filtre « En partage ».
4. Icône de la barre système (AppIndicator), planificateur, actions de fin (éteindre / veille,
   extraction avec le moteur sûr d'Odysseus), mode distant aria2 (RPC vers un NAS).
Pas d'extension de navigateur (décision de Tof). « Afficher dans le dossier » passe par
org.freedesktop.FileManager1 (Odysseus s'il a l'option, sinon Nautilus).

## Architecture

- `backend/aria2.py` : lance aria2c (port libre, secret aléatoire à chaque lancement, écoute
  127.0.0.1 seulement, aucune origine autorisée — Varia laissait l'RPC ouvert à toute page web),
  `--stop-with-process` + `PR_SET_PDEATHSIG` (aria2 ne survit pas à Ithaca), client JSON-RPC urllib.
- `backend/downloads.py` : `Engine` (fil, seul à parler à aria2 : commandes en file puis relevé
  toutes les 0,5 s en un `system.multicall`) et `Manager` (boucle GLib : liste, événements `added`,
  `changed`, `removed`, `completed`, `failed`, `engine`, `stats`). Les `Download` ne sont touchés
  que dans la boucle GLib (`_post` = `idle_add`), jamais de verrou côté interface.
  Pause / reprise : `intent_until` ignore 1,5 s un relevé fait avant le clic (sinon l'état
  scintillait).
- État : `~/.local/share/ithaca/downloads.json` (écriture atomique, différée de 0,5 s). Quitter
  garde les téléchargements en cours : ils sont relancés au démarrage avec le même `out=`, aria2
  reprend grâce à son fichier de contrôle `.aria2` (`auto-save-interval=10` : reprise même après un
  plantage). Les terminés restent dans l'historique (« Effacer les terminés de la liste »).
- Instance unique par GApplication (pas de socket) : `ithaca URL…` relancé envoie les liens à la
  première instance ; `HANDLES_OPEN` pour l'activation D-Bus (`.desktop` en `DBusActivatable`).
- Préférences globales (vitesse, simultanés) appliquées en direct par `aria2.changeGlobalOption` ;
  segments, authentification et cookies pris au moment d'ajouter / relancer un téléchargement.
  Mot de passe d'authentification : dans settings.json, écrit en 0600.
- settings.json ne garde que les valeurs choisies par l'utilisateur (0.1.0 écrivait tout : ses
  valeurs par défaut figées sont retirées au chargement, `OLD_DEFAULTS`, clé `version`). Ainsi une
  valeur par défaut corrigée s'applique à tous ceux qui n'y ont pas touché.
- 5 connexions par fichier par défaut, comme Varia. Avec 16, proof.ovh.net coupait en plein
  téléchargement chez Tof (erreur 22 d'aria2, « réponse du serveur inattendue »). Erreurs passagères
  (`TRANSIENT` : 2, 6, 22, 29) : jusqu'à 3 relances automatiques après 3 s, en reprenant le partiel,
  avec deux fois moins de connexions à chaque fois (`Download.max_connections`) ; « Réessayer »
  remet le compteur à zéro. Journal d'aria2 : `~/.cache/ithaca/aria2.log` (niveau warn).
- La vraie cause chez Tof : HTTP 429 Too Many Requests (proof.ovh.net rationne les requêtes par IP ;
  à 64 Mio/s, aria2 en enchaîne beaucoup en 2 s). aria2 en fait une erreur 22 ; le code HTTP se lit
  dans errorMessage (`http_status`, aria2 lancé en `LC_ALL=C` pour que ses messages restent en
  anglais). 429 / 503 → relance avec UNE connexion après 5 s, 15 s, 45 s, et le serveur garde une
  connexion pour la session (`Manager.rationed_hosts`). `RationedTest` reproduit avec
  `Server(limit=…, window=…)`. Un proxy (bac à sable) masque ce comportement : demander le journal.

## Vidéos (yt-dlp)

- `backend/tools.py` : yt-dlp (`yt-dlp_linux`) et Deno (moteur JavaScript que yt-dlp exige pour
  YouTube ; Varia embarque QuickJS) depuis `releases/latest/download` de GitHub. Somme SHA-256
  publiée vérifiée AVANT d'installer (SHA2-256SUMS, .sha256sum) ; échec → l'ancienne version reste.
  Mise à jour = somme publiée ≠ `nom.sha256` installé (pas d'API GitHub : limitée, refusée dans le
  bac à sable). Vérification quotidienne silencieuse (`app._auto_update_tools`, réglage
  `tools_auto_update`). `ITHACA_BIN_DIR` remplace le dossier (tests).
- `backend/videos.py` : `probe` (`-J --flat-playlist`) → `summarize` (qualités, sous-titres, playlist,
  miniature JPEG de préférence : GTK 4.14 ne lit pas toujours le WebP). yt-dlp toujours lancé avec
  `--ignore-config` (la config de l'utilisateur ne change rien), `--js-runtimes deno:…`, cookies
  (fichier, ou `--cookies-from-browser` : réglage `browser_cookies`, pour « confirmez que vous n'êtes
  pas un robot »), `--ffmpeg-location`. Progression par `--progress-template` / `--print` (lignes
  `ITHACA-P`, `ITHACA-V`, `ITHACA-F`), sortie lue dans un fil par téléchargement (`Job`), au plus
  `simultaneous` yt-dlp (`VideoRunner`).
- Fichiers : yt-dlp travaille dans `.ithaca-<id>` caché DANS le dossier de destination (même disque),
  puis `_collect` range le résultat par `fileops.place_new` (renameat2 NOREPLACE, « titre (2).mp4 »,
  jamais d'écrasement ; autre disque : copie sous nom caché puis renommage). Annuler =
  `remove_workdir` (seulement ce dossier, nom vérifié, pas un lien). Pause / quitter : SIGINT au
  groupe de processus (yt-dlp + ffmpeg), les `.part` restent, relancer la même commande reprend.
- Tests : `--embed-metadata` réécrit le conteneur, comparer les pistes (`ffmpeg -f streamhash`),
  pas les octets. YouTube refuse le bac à sable (adresse de datacenter : « not a bot ») : les tests
  utilisent l'extracteur générique sur des vidéos générées par ffmpeg (une page HTML à deux
  `<video>` = une playlist).

## Fichiers : ne jamais écraser, ne supprimer que le nôtre

- aria2 tourne avec `--allow-overwrite=false --auto-file-renaming=false` : un nom déjà pris donne
  l'erreur 13 et Ithaca relance sous « nom (2).ext » (`free_name`, doubles extensions .tar.gz…),
  comme Nautilus / Odysseus (aria2 seul faisait « nom.1.ext »).
- JAMAIS `--continue=true` : aria2 prenait un fichier existant sans `.aria2` pour un partiel et le
  « complétait » (fichier de l'utilisateur modifié). La reprise passe par le fichier de contrôle,
  qui suffit (`test_existing_name_never_overwritten`, `test_resume_after_restart`).
- Annuler supprime le fichier partiel seulement si son `.aria2` existe (`remove_partial`) : preuve
  qu'aria2 l'écrit pour nous. Retirer un téléchargement terminé garde le fichier ; « Mettre à la
  corbeille » passe par `Gio.File.trash`.

## Tests

Le `python3` du bac à sable n'a pas `gi` : utiliser `python3.12`.

```sh
python3.12 -m unittest discover -s tests            # moteur (vrai aria2 / yt-dlp + serveur local, sans GTK)
xvfb-run -a python3.12 tests/ui_smoke.py            # interface : doit finir par « Aucune erreur. »
LANGUAGE=en xvfb-run -a python3.12 tests/ui_smoke.py
LANGUAGE=de xvfb-run -a python3.12 tests/ui_smoke.py
python3 -m pyflakes ithaca tests tools
```

yt-dlp et Deno des tests : `ITHACA_TEST_TOOLS`, sinon `~/.cache/ithaca-test-tools` (installés par
`tools.install` au premier passage ; sans réseau, les tests de téléchargement vidéo sont sautés).

`tests/server.py` : serveur HTTP local (Range pour les segments et la reprise, débit bridé, 404,
Content-Disposition UTF-8, authentification Basic). Les tests ne fixent jamais de délai pour
« pendant le téléchargement » : débit bridé et attente d'un nombre d'octets reçus.
`tests/ui_smoke.py` pilote l'interface par des générateurs exécutés dans la vraie boucle GLib, dans
un HOME jetable ; les « -CRITICAL » de GTK sont comptés comme des échecs.

Captures (Gtk.WidgetPaintable, configuration jetable) : `xvfb-run -a python3.12 tools/screenshot.py
SORTIE.png [--demo] [--filter=…] [--details] [--prefs=PAGE] [--dark] [--delay=ms] [--size=LxH]` ;
`--demo` lance un serveur local et des téléchargements d'exemple (terminés, en cours, en pause, échec).

## Pièges connus

- Sans `en.po`, `i18n.decimal` prend la langue « en » (point décimal) alors que les textes restent
  en français : toujours livrer en.po et de.po complets.
- Messages d'aria2 en anglais : `row.ERRORS` traduit les codes de sortie courants (man aria2c,
  EXIT STATUS), le message brut sinon.
- Unités décimales comme GNOME (1 Ko = 1000 octets) ; la limite de vitesse est en Ko/s, envoyée à
  aria2 en octets/s.
- `Gtk.ListBox` de la liste : lignes non activables (`activatable=False`), double-clic et clic droit
  par un `Gtk.GestureClick` sur la ligne ; filtres par `set_filter_func` + `invalidate_filter` quand
  l'état change.
- Menus contextuels (`common.popup_menu`) : un seul Gtk.PopoverMenu par widget, gardé et réutilisé,
  jamais détruit à la fermeture (plantage de GTK 4.14 sinon, vu dans Odysseus).
- Textes venus de l'extérieur (messages de yt-dlp, noms de fichiers, adresses) : la description
  d'`Adw.StatusPage` et le titre d'`Adw.Toast` sont du balisage Pango → `GLib.markup_escape_text` /
  `toast.set_use_markup(False)` (« <HTTPError 404> » cassait l'affichage).
- Notifications : seulement s'il y a un bus de session (`get_dbus_connection()`), sinon GIO crie
  des CRITICAL (xvfb).
- Nom du processus : `app._process_name` (prctl PR_SET_NAME) → « ithaca » dans ps / killall.
  WM_CLASS : `GLib.set_prgname(APP_ID)`.
