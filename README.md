# z-cam-surveillance

Application web locale pour découvrir les chemins RTSP alternatifs d'une caméra Wi-Fi et repérer les flux principaux ou secondaires, notamment les substreams basse résolution.

## Prérequis et installation

Python 3.10 ou plus récent est recommandé. Depuis la racine du projet :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Les dépendances sont Flask et `opencv-python-headless` (OpenCV sans interface graphique). Installation équivalente sans fichier requirements :

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
