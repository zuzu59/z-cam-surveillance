# z-cam-surveillance

Deux outils indépendants : le scanner historique de chemins RTSP (`tst_RTSP_flux.py`, port 8090) et la détection d'objets (`surveillance.py`). Le moteur de surveillance fonctionne sur un serveur Intel en deux modes : **production headless** et **test avec aperçu web**. Les deux modes partagent la capture RTSP, l'inférence TFLite et l'enregistrement; seul le mode test encode un aperçu et sert le tableau de bord. Par défaut, une analyse est effectuée toutes les **0,5 seconde**.

## Détection et enregistrement

Le moteur utilise le modèle SSD MobileNet V2 COCO quantifié inclus dans `models/`. Seules les classes `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat` au-dessus du seuil de confiance déclenchent l'alarme. Si l'enregistrement événementiel est actif, les images sont ajoutées à un MP4 horodaté dans `captures/`; l'enregistrement s'arrête après 10 secondes sans détection. La capture RTSP continue en arrière-plan pour garder une image récente et conserver la vidéo entre les analyses. Aucun affichage OpenCV n'est utilisé.

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
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Dépendances : LiteRT (`ai-edge-litert`), NumPy, Flask et `opencv-python-headless`. Pas besoin de TensorFlow complet ni d'OpenCV avec interface graphique. Le paquet LiteRT fournit des wheels Linux x86_64/aarch64 récents.

## Démarrage

L'URL RTSP peut être fournie par variable d'environnement afin de ne pas l'inclure dans le code ou l'historique shell. Pour préserver les identifiants, utilisez le substream de la caméra si disponible.

### Production — mode headless

```bash
read -rsp 'URL RTSP: ' RTSP_URL; echo; export RTSP_URL
.venv/bin/python surveillance.py --mode prod --interval 0.5
```

Arrêt avec `Ctrl+C`. Le moteur garde les journaux dans la console et écrit les clips sous `captures/`. Réglez éventuellement le seuil ou le répertoire :

```bash
.venv/bin/python surveillance.py --mode prod --threshold 0.5 --interval 0.5 --no-detection-seconds 10 --output-dir captures
```

### Test — tableau de bord

```bash
# RTSP_URL est facultative en mode test : configurez la caméra dans le tableau si besoin.
.venv/bin/python surveillance.py --mode test --interval 0.5 --host 127.0.0.1 --port 8091
```

Ouvrez <http://127.0.0.1:8091> sur le serveur. Le tableau affiche un aperçu mis à jour à chaque analyse, les boîtes et étiquettes IA, l'état du flux et de l'enregistrement. Il permet de modifier le seuil, la cadence (0,2 à 30 s), le délai de fin et l'activation de l'enregistrement sans redémarrer. Une nouvelle URL saisie dans le champ protégé remplace la source en mémoire et déclenche une reconnexion; le champ se vide après l'envoi. Les réglages et l'URL ne sont jamais persistés, et l'API ne renvoie pas l'URL de la caméra.

L'interface est liée à `127.0.0.1` par défaut et ne possède pas de mécanisme d'authentification. Pour l'ouvrir depuis un autre poste, préférez un tunnel SSH :

```bash
ssh -L 8091:127.0.0.1:8091 utilisateur@serveur
```

N'utilisez `--host 0.0.0.0` que sur un réseau de confiance et avec une restriction réseau adaptée : l'interface permet de remplacer la source RTSP et n'est pas conçue pour être exposée sur Internet.

Pour quitter le mode test : `Ctrl+C`. L'arrêt ferme le flux et termine les threads du moteur.

## Paramètres et variables d'environnement

- `RTSP_URL` : source RTSP initiale (obligatoire en prod, facultative en test); jamais inscrite dans les logs d'état ni renvoyée à l'interface.
- `DETECTION_THRESHOLD` : seuil par défaut (0–1), remplacé par `--threshold`.
- `INFERENCE_INTERVAL` : cadence par défaut en secondes, remplacée par `--interval`.
- `--model`, `--labels` : chemins vers le modèle et les étiquettes.
- `--output-dir` : dossier de clips (par défaut `captures/`).
- `--host`, `--port` : adresse et port du tableau de bord en mode test uniquement.

Un flux substream basse résolution économise le CPU de décodage et le stockage. L'inférence est limitée à une analyse toutes les 0,5 s par défaut; augmenter `--interval` réduit davantage la charge du serveur au prix d'une détection moins réactive. `cv2.setNumThreads(2)` limite le nombre de threads OpenCV.

## Scanner RTSP historique

Le scanner Flask reste disponible séparément :

```bash
.venv/bin/python tst_RTSP_flux.py
```

Il écoute sur le port 8090. Il est destiné à un usage local/de confiance, pas à une exposition Internet directe. Son interface vérifie des variantes de chemins de caméra et indique les flux qui décodent une image.

## Confidentialité

- Ne committez jamais d'URL contenant des identifiants. Utilisez `RTSP_URL` ou le champ protégé du mode test.
- Les captures d'écran et journaux ne doivent jamais révéler de mot de passe. Vérifiez-les avant publication.
- `captures/` est ignoré par Git; les enregistrements vidéo peuvent contenir des données sensibles.
