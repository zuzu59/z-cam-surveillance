# z-cam-surveillance

Application web locale de surveillance RTSP et détection d’objets. Elle analyse le flux basse résolution, affiche l’image et les labels détectés dans la page **Détection**, et peut enregistrer les événements depuis le flux haute résolution. Le scanner historique de chemins RTSP (`tst_RTSP_flux.py`) reste un outil séparé, disponible sur le port 8090.

## Fonctionnalités web

- **Détection** (`/`) : aperçu caméra, cadres, labels et état des flux/enregistrements.
- **Configuration** (`/configuration`) : formulaire unique pour modifier l’ensemble des paramètres persistés.
- **Enregistrements** (`/recordings`) : recherche instantanée dans les clips MP4, lecteur avec navigation et vitesses 1×/1,5×/2×, détails des fichiers et suppression confirmée.
- **Aide** (`/help`) et **À propos** (`/about`). La version est `0.0.7`; elle ne s’incrémente pas automatiquement et n’est incrémentée que sur demande du propriétaire.

La bibliothèque **Enregistrements** parcourt uniquement les fichiers MP4 du dossier de sortie configuré; sa recherche filtre les noms et dates sur toutes les pages. Chaque clip s’accompagne d’un `.jpg` annoté avec les cadres verts de la première détection et d’un `.txt` listant les labels détectés pendant l’événement, tous deux avec le même nom de base que le MP4. Le lecteur affiche ces indices avec la vidéo. Les métadonnées techniques (durée, résolution, codec, cadence et débit) sont lues à la demande avec `ffprobe` lorsqu’il est disponible. L’API ne révèle jamais le chemin absolu du dossier; la lecture supporte les requêtes HTTP Range du navigateur. La suppression demande une confirmation et efface définitivement le clip et ses fichiers d’indices associés.

Le modèle SSD MobileNet V2 COCO quantifié inclus dans `models/` détecte `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat`. Le flux basse résolution alimente la capture et l’inférence (0,5 seconde entre les analyses par défaut). Lorsqu’une détection est présente, le moteur ouvre le flux haute résolution et enregistre celui-ci en MP4 H.264 (`yuv420p`) dans `captures/`; il ferme le clip après 10 secondes sans détection et ferme le flux haute résolution en dehors des événements. L’encodage finalisé est publié après conversion FFmpeg pour être lisible par les navigateurs. Aucun affichage OpenCV n’est utilisé.

Les anciens clips encodés en MPEG-4 Part 2 (`mp4v`) ne sont pas décodés par tous les navigateurs. Pour les convertir, commencez par vérifier le bilan, puis exécutez la conversion atomique :

```bash
.venv/bin/python transcode_recordings.py --directory captures
.venv/bin/python transcode_recordings.py --directory captures --apply
```

Les fichiers valides sont convertis de façon atomique et leurs originaux sont archivés dans le sous-dossier masqué `captures/.originals/`; les fichiers illisibles sont conservés et ignorés. L’archive est exclue de la bibliothèque vidéo; une suppression depuis l’interface supprime également la sauvegarde liée au clip choisi.

Le modèle provient de [google-coral/test_data](https://github.com/google-coral/test_data). SHA-256 : `42fb3d70ffb7bb37dd518f730f7be784b831c2078f30c497d0019cc2e987fa26`. Vérification optionnelle :

```bash
sha256sum models/ssd_mobilenet_v2_coco_quant_postprocess.tflite
```

## Installation (Linux x86_64)

Python 3.10 ou plus récent. Depuis la racine du dépôt :

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libopenjp2-7 libavcodec-extra ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Dépendances : LiteRT (`ai-edge-litert`), NumPy, Flask et `opencv-python-headless`. FFmpeg avec `libx264`/`ffprobe` (fourni par le paquet système `ffmpeg`) est nécessaire pour publier les clips en H.264 lisible par les navigateurs. Pas besoin de TensorFlow complet ni d’OpenCV avec interface graphique.

## Lancement

```bash
./start.sh
```

`./start.sh` est l’unique commande de démarrage/redémarrage : elle recherche le processus qui utilise le fichier de configuration de ce dépôt, l’arrête proprement si nécessaire, puis lance l’application. Utilisez-la aussi après une modification manuelle du JSON. Dans un terminal interactif, `Ctrl+C` arrête le serveur. La version peut être affichée avec `./start.sh --version` sans interrompre une instance en cours.

Par défaut, le serveur écoute sur `0.0.0.0:8091`. Ouvrez `http://<adresse-du-serveur>:8091/`. L’application n’a pas d’authentification : gardez-la sur un réseau de confiance, restreignez l’accès par pare-feu et ne l’exposez pas directement à Internet.

## Configuration persistante

Au premier lancement, l’application crée `surveillance.config.json` à partir des valeurs par défaut (également documentées dans `surveillance.config.example.json`). La page **Configuration** permet de modifier en une fois les 12 paramètres de ce JSON : URL RTSP basse et haute résolution, seuil, cadence, délai sans détection avant fermeture du clip, activation de l’enregistrement, classes détectées, dossier de sortie, modèle, fichier de labels, adresse d’écoute et port. Les classes sont sélectionnées par cases à cocher; les six classes usuelles sont cochées par défaut. Le bouton **Sauvegarder et recharger**, en haut à droite, écrit le JSON complet puis redémarre automatiquement le processus : tous les paramètres sont relus depuis le fichier, y compris le modèle, les labels, l’adresse et le port. La page se reconnecte ensuite au serveur redémarré. Les MP4 suivent les horodatages du flux RTSP pour conserver la vitesse de déplacement réelle, même si OpenCV vide son tampon plus vite que le flux n’avance; l’horloge monotone sert de repli si les horodatages ne sont pas disponibles.

Les champs URL restent volontairement vides au chargement pour ne pas exposer les secrets; leur statut confirme si une valeur est mémorisée. Les laisser vides conserve les URL existantes; les cases dédiées permettent de les effacer. L’API et les journaux ne les affichent pas.

`surveillance.config.json` est ignoré par Git et ses permissions sont limitées au propriétaire (`0600`). Il contient les identifiants RTSP **en clair sur le disque** : protégez l’accès au compte système et ne copiez/committez jamais ce fichier. Les réponses de configuration ordinaires masquent les URL; le bouton œil de la page Configuration demande explicitement l’affichage d’une seule URL à la fois. Le serveur n’a pas d’authentification : toute personne pouvant accéder à l’application peut révéler ces identifiants, réservez donc son accès à un réseau et à des appareils de confiance. Le fichier d’exemple ne contient aucun identifiant.

Les options de lancement disponibles sont consultables avec `./start.sh --help`. Elles sont destinées au démarrage et leurs valeurs sont persistées dans le fichier JSON; évitez de fournir des URL contenant des identifiants dans l’historique du shell. Les réglages usuels se font depuis la page Configuration.

## Outils de découverte caméra

Le scanner de ports `scan_camera.py` prend une adresse IP, détecte les ports TCP ouverts (1–65535), identifie les protocoles courants et teste des chemins média possibles pour RTSP et HTTP(S). Les chemins non vérifiés restent cachés; seules les URLs qui renvoient effectivement un média valide sont affichées et copiables :

```bash
.venv/bin/python scan_camera.py
```

Il écoute par défaut sur `127.0.0.1:8092` et limite les cibles aux adresses IP privées ou locales. Pour y accéder depuis un autre appareil du LAN, définissez `SCAN_CAMERA_HOST` sur l’adresse réseau du serveur, par exemple `SCAN_CAMERA_HOST=192.168.0.92 .venv/bin/python scan_camera.py`; n’ouvrez cet accès que sur un réseau de confiance. Le scan des ports n’envoie que l’IP. Si vous lancez le test des médias, les URLs et identifiants sont transmis temporairement en HTTP non chiffré au scanner local pour vérifier les flux et médias; ils ne sont pas enregistrés. Utilisez cette option uniquement sur un réseau de confiance. Les résultats dépendent de la connectivité et des réponses réelles de la caméra au moment du test.

Le scanner historique de chemins RTSP reste disponible séparément :

```bash
.venv/bin/python tst_RTSP_flux.py
```

Il écoute sur le port 8090. Les deux outils sont destinés à un usage local/de confiance, pas à une exposition Internet directe.

## Confidentialité

- Ne committez jamais `surveillance.config.json` ni une URL contenant des identifiants. Le fichier de configuration local est ignoré par Git.
- Les captures d’écran et journaux ne doivent jamais révéler de mot de passe. Vérifiez-les avant publication.
- `captures/` est ignoré par Git; les enregistrements vidéo peuvent contenir des données sensibles.
