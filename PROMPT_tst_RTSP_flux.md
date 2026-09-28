Agis comme un expert en développement Python Web et en protocoles de vidéosurveillance (RTSP / ONNX).

Je veux que tu écrives une application web en Python qui va m'aider à découvrir les différents flux vidéo cachés ou alternatifs (Substreams) de ma caméra Wi-Fi.

Voici le fonctionnement de l'application et son cahier des charges :

1. Interface Web et Démarrage :
   - Utilise une bibliothèque simple comme Flask ou FastAPI.
   - L'application doit démarrer et écouter sur l'adresse '0.0.0.0' et le port '8090'.
   - La page d'accueil ('/') doit afficher un formulaire web avec un champ texte pour que je puisse coller mon URL RTSP de base actuelle.

2. Analyse et Génération de Variantes :
   - Lorsque je valide le formulaire, le script doit analyser l'URL fournie (ex: rtsp://admin:mon_mdp@192.168.1.50:554/user=admin_password=mon_mdp_channel=1_stream=0.sdp).
   - Le script doit extraire dynamiquement : l'identifiant, le mot de passe, l'adresse IP et le port.
   - À partir de ces données, le script doit générer automatiquement une liste de variantes d'URLs RTSP classiques et courantes (les "paths" typiques pour les flux principaux et secondaires des caméras du marché). Par exemple :
     * Les changements de stream : stream=1, stream=2, channel=2
     * Les syntaxes courantes : /stream1, /stream2, /h264, /h265, /live/ch0, /live/ch1, /onvif1, /onvif2, /mpeg4
     * Une syntaxe épurée sans les arguments répétitifs à la fin.

3. Script de Test Automatique (Scanner) :
   - Pour chaque URL générée, le script doit tenter de se connecter en arrière-plan à l'aide d'OpenCV (cv2.VideoCapture).
   - Applique un 'timeout' très court (ex: 2 ou 3 secondes max par URL) pour éviter que la page web ne charge indéfiniment.
   - Si cv2.VideoCapture.read() renvoie un résultat positif (True), l'URL est marquée comme "[FONCTIONNELLE]". Bonus si le script arrive à extraire la résolution (largeur x hauteur) du flux trouvé.
   - Si la connexion échoue, elle est marquée comme "[INACCESSIBLE]".

4. Affichage des Résultats :
   - L'application web doit afficher proprement le résultat du scan sous forme de liste ou de tableau sur la page.
   - Les URLs fonctionnelles doivent être mises en évidence afin que je puisse copier-coller l'URL du flux secondaire (basse résolution) pour mon projet de détection d'humains sur Raspberry Pi.

Génère le code complet de cette application web, commente la logique de test OpenCV et indique-moi les commandes pour installer les dépendances (comme Flask et opencv-python-headless).

Je veux que tu testes l'appli en vrai en utilisant ton browser headless chromium/playwrite et sauvegarde tous les screenshots (et garde l'historique de progression) que tu auras fait dans le dossier screenshots dans CE projet préfixés avec la date yymmdd.hhmmss

# IMPORTANT:
Vérifies qu'aucun passwords RTSP ne se retrouvent dans les copies d'écran et/ou commits !