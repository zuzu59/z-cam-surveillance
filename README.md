# z-cam-surveillance

Deux outils indépendants : le scanner historique de chemins RTSP (`tst_RTSP_flux.py`, port 8090) et la détection d’objets (`surveillance.py`). L’application de surveillance fournit un tableau web en mode test et un mode headless en production.

## Détection et enregistrement

Le modèle SSD MobileNet V2 COCO quantifié inclus dans `models/` détecte `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat`. Le flux **basse résolution** alimente la capture et l’inférence (0,5 seconde entre les analyses par défaut). Lorsqu’une détection est présente, le moteur ouvre le flux **haute résolution** et enregistre celui-ci en MP4 dans `captures/`; il ferme le clip après 10 secondes sans détection et ferme le flux haute résolution pour économiser la bande passante en dehors des événements. Le flux basse résolution reste utilisé pour l’aperçu et l’analyse. Aucun affichage OpenCV n’est utilisé.

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

## Configuration persistante

Au premier lancement, l’application crée `surveillance.config.json` à partir des valeurs par défaut. Ce fichier contient les deux URL RTSP et tous les paramètres de démarrage (seuil, cadence, durée avant fermeture du clip, activation de l’enregistrement, modèle, labels, dossier de sortie, mode, hôte et port). Il est ignoré par Git et ses permissions sont limitées au propriétaire (`0600`). Un exemple sans identifiants est fourni dans `surveillance.config.example.json`.

Le JSON contient les identifiants RTSP **en clair sur le disque** : protégez l’accès au compte système et ne copiez/committez jamais le fichier. L’interface et l’API ne renvoient jamais ces URL. Une modification appliquée depuis le tableau est enregistrée immédiatement; au redémarrage, les valeurs sont rechargées.

Dans le tableau, saisissez un nouveau flux dans le champ correspondant pour le remplacer : basse résolution pour la détection, haute résolution pour les clips. Un champ vide conserve l’URL déjà enregistrée. Les autres réglages du formulaire sont eux aussi sauvegardés.

## Démarrage

Après installation, lancez le tableau avec :

```bash
./start.sh
```

Le script utilise `.venv`, se place à la racine du projet et démarre le mode défini dans le JSON (par défaut `test`). Arrêtez avec `Ctrl+C`. Pour lancer la surveillance headless :

```bash
./start.sh --mode prod
```

Les options passées au script sont transmises à `surveillance.py`; les valeurs de démarrage modifiées en ligne de commande sont persistées dans le fichier de configuration. Les options `--low-url` et `--high-url` permettent de remplacer les deux sources, mais préférez le tableau pour ne pas inscrire des identifiants dans l’historique du shell.

Par défaut, le tableau écoute sur `0.0.0.0:8091` (configurable par `host` et `port` dans le JSON), conformément au déploiement sur le serveur du projet. Il n’a pas d’authentification : gardez-le sur un réseau de confiance et restreignez l’accès par pare-feu; ne l’exposez pas sur Internet.

## Scanner RTSP historique

Le scanner Flask reste disponible séparément :

```bash
.venv/bin/python tst_RTSP_flux.py
```

Il écoute sur le port 8090. Il est destiné à un usage local/de confiance, pas à une exposition Internet directe. Son interface vérifie des variantes de chemins de caméra et indique les flux qui décodent une image.

## Confidentialité

- Ne committez jamais `surveillance.config.json` ni une URL contenant des identifiants. Le fichier de configuration local est ignoré par Git.
- Les captures d’écran et journaux ne doivent jamais révéler de mot de passe. Vérifiez-les avant publication.
- `captures/` est ignoré par Git; les enregistrements vidéo peuvent contenir des données sensibles.
