Agis comme un expert en Edge AI et vision par ordinateur sur Raspberry Pi.

Écris un script Python de vidéosurveillance optimisé pour un Raspberry Pi 3. Le script doit se connecter à une caméra Wi-Fi via un flux RTSP (Substream basse résolution) et utiliser un modèle TensorFlow Lite (TFLite) pré-entraîné sur le dataset COCO (80 classes).

Voici le cahier des charges strict pour la boucle d'exécution et de vérification :

1. Architecture Threadée :
   - Thread 1 : Capture en continu le flux RTSP pour vider le buffer réseau et éviter tout décalage temporel.
   - Thread 2 : Traite l'analyse IA à une fréquence réduite (ex: 1 frame toutes les 0.5 seconde) pour ne pas saturer le CPU du Pi 3.

2. Détection ciblée (Classes COCO) :
   - Le script doit charger un modèle léger (ex: 'mobilenet_v2_coco_quant_postprocess.tflite').
   - Filtre les détections pour ne réagir QUE si l'étiquette (label) appartient à la liste suivante : ['person', 'car', 'bicycle', 'motorcycle', 'dog', 'cat']. Ignore toutes les autres classes.
   - Applique un seuil de confiance minimal (ex: 50%) paramétrable pour valider la détection.

3. Logique d'Enregistrement Automatique :
   - Si une des classes cibles est détectée avec succès, l'alarme se déclenche.
   - Le script doit immédiatement commencer à enregistrer le flux vidéo localement (dans un dossier /captures/) au format .mp4 avec horodatage dans le nom du fichier.
   - L'enregistrement continue tant qu'un objet de la liste est détecté, et s'arrête automatiquement après 10 secondes consécutives sans détection.

4. Optimisations Headless :
   - Le code doit tourner en tâche de fond (Headless, pas de cv2.imshow).
   - Ajoute des logs clairs dans la console à chaque étape (ex: "[INFO] Connexion RTSP réussie", "[ALERTE] Humain détecté (68%) - Début de l'enregistrement", etc.).
   - Gère proprement les exceptions (perte de connexion Wi-Fi de la caméra avec reconnexions automatiques).

Génère le code complet, explique-moi la structure et donne-moi les commandes terminal pour installer les dépendances (OpenCV et le runtime TFLite) sur mon Raspberry Pi 3.

Je veux que tu testes l'appli en vrai en utilisant ton browser headless chromium/playwrite et sauvegarde tous les screenshots (et garde l'historique de progression) que tu auras fait dans le dossier screenshots dans CE projet préfixés avec la date yymmdd.hhmmss

# IMPORTANT:
Vérifies qu'aucun passwords RTSP ne se retrouvent dans les copies d'écran et/ou commits !

