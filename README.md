# z-cam-surveillance

Application web locale de surveillance RTSP et détection d’objets. Elle analyse le flux basse résolution, affiche l’image et les labels détectés dans la page **Détection**, et peut enregistrer les événements depuis le flux haute résolution. Le scanner historique de chemins RTSP (`tst_RTSP_flux.py`) reste un outil séparé, disponible sur le port 8090.

## Fonctionnalités web

- **Détection** (`/`) : aperçu caméra, cadres, labels et état des flux/enregistrements.
- **Configuration** (`/configuration`) : formulaire unique pour modifier l’ensemble des paramètres persistés.
- **Aide** (`/help`) et **À propos** (`/about`). La version initiale est `0.0.1`; elle ne s’incrémente pas automatiquement.

Le modèle SSD MobileNet V2 COCO quantifié inclus dans `models/` détecte `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat`. Le flux basse résolution alimente la capture et l’inférence (0,5 seconde entre les analyses par défaut). Lorsqu’une détection est présente, le moteur ouvre le flux haute résolution et enregistre celui-ci en MP4 dans `captures/`; il ferme le clip après 10 secondes sans détection et ferme le flux haute résolution en dehors des événements. Aucun affichage OpenCV n’est utilisé.

Le modèle provient de [google-coral/test_data](https://github.com/google-coral/test_data). SHA-256 : `42fb3d70ffb7bb37dd518f730f7be784b831c2078f30c497d0019cc2e987fa26`. Vérification optionnelle :

```bash
sha256sum models/ssd_mobilenet_v2_coco_quant_postprocess.tflite
```

## Installation (Linux x86_64)

Python 3.10 ou plus récent. Depuis la racine du dépôt :

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libopenjp2-7 libavcodec-extra
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Dépendances : LiteRT (`ai-edge-litert`), NumPy, Flask et `opencv-python-headless`. Pas besoin de TensorFlow complet ni d’OpenCV avec interface graphique.

## Lancement

```bash
./start.sh
```

Le script démarre toujours l’application web, en utilisant le fichier JSON local. Arrêtez avec `Ctrl+C`. La version peut être affichée avec `./start.sh --version`.

Par défaut, le serveur écoute sur `0.0.0.0:8091`. Ouvrez `http://<adresse-du-serveur>:8091/`. L’application n’a pas d’authentification : gardez-la sur un réseau de confiance, restreignez l’accès par pare-feu et ne l’exposez pas directement à Internet.

## Configuration persistante

Au premier lancement, l’application crée `surveillance.config.json` à partir des valeurs par défaut (également documentées dans `surveillance.config.example.json`). La page **Configuration** permet de modifier en une fois toutes les valeurs de ce JSON : URL RTSP basse et haute résolution, seuil, cadence, délai avant fermeture du clip, activation de l’enregistrement, dossier de sortie, modèle, labels, adresse d’écoute et port.

Les paramètres modifiables à chaud sont appliqués immédiatement. Les changements de modèle, labels, adresse ou port sont sauvegardés et signalés dans la page, mais prennent effet au prochain redémarrage. Un champ URL laissé vide conserve la valeur mémorisée; une case dédiée permet d’effacer une URL.

`surveillance.config.json` est ignoré par Git et ses permissions sont limitées au propriétaire (`0600`). Il contient les identifiants RTSP **en clair sur le disque** : protégez l’accès au compte système et ne copiez/committez jamais ce fichier. L’interface et l’API ne renvoient jamais ces URL. Le fichier d’exemple ne contient aucun identifiant.

Les options de lancement disponibles sont consultables avec `./start.sh --help`. Elles sont destinées au démarrage et leurs valeurs sont persistées dans le fichier JSON; évitez de fournir des URL contenant des identifiants dans l’historique du shell. Les réglages usuels se font depuis la page Configuration.

## Scanner RTSP historique

Le scanner Flask reste disponible séparément :

```bash
.venv/bin/python tst_RTSP_flux.py
```

Il écoute sur le port 8090. Il est destiné à un usage local/de confiance, pas à une exposition Internet directe.

## Confidentialité

- Ne committez jamais `surveillance.config.json` ni une URL contenant des identifiants. Le fichier de configuration local est ignoré par Git.
- Les captures d’écran et journaux ne doivent jamais révéler de mot de passe. Vérifiez-les avant publication.
- `captures/` est ignoré par Git; les enregistrements vidéo peuvent contenir des données sensibles.
