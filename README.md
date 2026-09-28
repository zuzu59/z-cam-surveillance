# z-cam-surveillance

Ce dépôt contient deux outils : le scanner web historique pour découvrir les flux RTSP et `surveillance.py`, un détecteur headless TFLite avec enregistrement d'événements pour Raspberry Pi.

## Surveillance IA RTSP (Raspberry Pi 3)

`surveillance.py` ouvre un flux RTSP, lit les images en continu dans un thread pour éviter d'accumuler du retard réseau, et exécute le modèle TFLite dans un second thread toutes les 0,5 seconde. Seules les classes COCO `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat` au-dessus du seuil déclenchent l'alarme et l'enregistrement MP4. Celui-ci s'arrête après 10 secondes sans détection. Le dossier `captures/` est créé automatiquement; aucun affichage OpenCV n'est utilisé.

### Runtime et dépendances (Raspberry Pi 3)

Le runtime est **LiteRT** (`ai-edge-litert`), qui fournit l'API `Interpreter` utilisée par le script. Les wheels officielles actuelles couvrent Linux **64 bits/aarch64** et Python 3.10–3.13. Utilisez Raspberry Pi OS 64 bits à jour; ARMv7/OS 32 bits n'est pas couvert par ces wheels. Depuis la racine du dépôt :

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libopenjp2-7 libavcodec-extra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` installe `ai-edge-litert`, NumPy et `opencv-python-headless`. Il n'est pas nécessaire d'installer TensorFlow complet. Sur un Pi OS 32 bits, passez à l'image 64 bits ou compilez un runtime compatible séparément; `ai-edge-litert` ne fournit pas de wheel ARMv7. Évitez d'installer en parallèle `opencv-python` avec interface graphique.

### Modèle inclus et démarrage

Le dépôt inclut le modèle SSD MobileNet V2 quantifié COCO `models/ssd_mobilenet_v2_coco_quant_postprocess.tflite` (environ 6,2 MB) et ses labels `models/coco_labels.txt`. Ils proviennent de [google-coral/test_data](https://github.com/google-coral/test_data); SHA-256 du modèle : `42fb3d70ffb7bb37dd518f730f7be784b831c2078f30c497d0019cc2e987fa26`. Vérification optionnelle :

```bash
sha256sum models/ssd_mobilenet_v2_coco_quant_postprocess.tflite
```

Définissez l'URL RTSP dans l'environnement afin de ne pas l'inscrire dans le code ni dans la commande sauvegardée dans l'historique shell, puis lancez le détecteur avec les chemins par défaut :

```bash
read -rsp 'URL RTSP: ' RTSP_URL; echo; export RTSP_URL
python surveillance.py --threshold 0.5
unset RTSP_URL
```

Le seuil est compris entre `0` et `1`; `--interval`, `--no-detection-seconds` et `--output-dir` règlent la cadence, le délai de fin et le dossier des vidéos. Seules les classes `person`, `car`, `bicycle`, `motorcycle`, `dog` et `cat` sont retenues. Utilisez de préférence un substream basse résolution pour limiter le décodage et le coût CPU; le Pi 3 peut ne pas atteindre une cadence temps réel.

## Prérequis et installation

Python 3.10 ou plus récent est recommandé. Depuis la racine du projet :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Les dépendances du scanner sont Flask et `opencv-python-headless` (OpenCV sans interface graphique). Le runtime LiteRT/NumPy est requis par l'outil de surveillance décrit ci-dessus. Installation minimale du scanner seul :

```bash
python -m pip install Flask opencv-python-headless
```

## Démarrer l'application

```bash
python tst_RTSP_flux.py
```

Le serveur écoute sur `0.0.0.0:8090`. Ouvrez <http://localhost:8090> sur la machine qui l'exécute, ou `http://<adresse-ip-du-serveur>:8090` depuis un appareil du même réseau. Le serveur Flask intégré est destiné à un usage local/de confiance, pas à une exposition directe sur Internet.

## Utilisation

1. Collez l'URL RTSP actuelle de votre caméra dans le formulaire, par exemple `rtsp://utilisateur:mot-de-passe@192.168.1.50:554/chemin`.
2. L'application extrait l'hôte, le port et les identifiants, puis construit une liste dédupliquée de chemins alternatifs, notamment des variantes `stream`/`channel`, `/stream1`, `/stream2`, `/h264`, `/h265`, `/live/ch0`, `/live/ch1`, `/onvif1`, `/onvif2`, `/mpeg4` et des chemins courants de constructeurs.
3. Les variantes sont testées en arrière-plan. La page affiche l'état et, lorsqu'une image est décodée, sa résolution. Le bouton **Copier** copie l'URL complète du flux fonctionnel pour la réutiliser dans votre application.

Les scans sont conservés en mémoire uniquement et expirent après 30 minutes. Ils testent les variantes l'une après l'autre; le nombre de chemins et le temps de réponse de la caméra déterminent donc la durée totale.

## Timeout et limites d'OpenCV

Le test utilise `cv2.VideoCapture` avec le backend FFmpeg et demande un timeout d'ouverture et de lecture de 2,5 secondes par URL. Les timeouts OpenCV dépendent du backend et de la compilation FFmpeg de la plateforme : ils ne sont pas garantis sur tous les systèmes. Une variante est dite fonctionnelle uniquement si `read()` décode effectivement une image. Une caméra peut refuser certains chemins, exiger une syntaxe propriétaire ou imposer une limite de connexions simultanées.

## Confidentialité des identifiants

- Le champ de saisie est masqué et vidé dès le démarrage du scan.
- Les identifiants ne figurent pas dans l'adresse de la page, les messages de statut ou les URL affichées : l'interface masque à la fois la partie utilisateur/mot de passe avant `@` et les paramètres de type `password=` dans le chemin.
- L'application ne journalise et n'enregistre pas les URL sur disque. Les données nécessaires au test restent en mémoire et sont transmises à OpenCV; les URL complètes sont aussi envoyées au navigateur pour que le bouton **Copier** fonctionne.
- Le presse-papiers contiendra les identifiants si vous copiez une URL. N'utilisez l'application que sur un réseau de confiance et évitez de partager les URLs complètes ou des captures d'écran non vérifiées.

Les captures de tests headless Chromium sont dans `screenshots/`; elles utilisent des URLs expurgées. Vérifiez toujours les fichiers avant de les publier ou de les ajouter à un commit.
