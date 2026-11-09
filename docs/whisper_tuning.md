# Réglages Whisper anti-boucle

## Environnement du test

- Date : 23 septembre 2026
- GPU : NVIDIA GeForce RTX 4090
- Modèle : `ggml-large-v3-turbo.bin`
- Média : vidéo de 2 h 33 min 21 s, AAC stéréo
- Binaire : `whisper-cli.exe` CUDA, options vérifiées avec `--help`

## Mesure avant correction

La transcription historique en un seul appel a pris 292,4 secondes.
Le diagnostic a relevé 128 signalements. Les principales dérives étaient :

- `00:00:00–00:08:34` : « Sous-titrage Société Radio-Canada » ;
- `00:10:11–00:15:17` : répétitions de « S'il y'all » / « y'all » ;
- `00:43:52–00:52:36` : répétitions de « C'est parti » / « C'est ça » ;
- `01:36:39–02:16:37` : longue contagion de « c'est ce que » puis « c'est de ».

Le rapport complet se trouve dans le dossier de diagnostic du média sous
`work/diagnostics/`.

## Paramètres anti-boucle, sans découpage

Avec `max_context=0`, la suppression des tokens non vocaux, les seuils plus
stricts et la VAD limitée à 30 secondes, le même appel complet a pris
284,4 secondes et produit 60 signalements.

La propagation massive de `01:36` à `02:16` a disparu. Des hallucinations
ponctuelles « Radio-Canada » restaient présentes, ce qui justifie le
découpage et le nettoyage.

## Pipeline final par morceaux

Le média a été découpé dans les silences en 15 morceaux d'environ 10 minutes.
Chaque morceau suspect a été retranscrit une fois avec les réglages stricts.
La première exécution complète, retranscriptions comprises, a pris environ
8 min 56 s. La reprise à partir des JSON existants a pris moins de 10 secondes.

Après fusion et nettoyage :

- aucun n-gramme de 1 à 8 mots n'est répété au moins 3 fois d'affilée ;
- aucune série de 3 segments identiques ou presque ne subsiste ;
- aucune occurrence de « Radio-Canada » ne subsiste ;
- chacune des tranches de 10 minutes contient du texte ;
- une zone à fort ratio de compression (`00:59:03–00:59:33`) reste conservée
  et marquée comme passage incertain, sans suppression silencieuse.

Les retranscriptions strictes sont exclues du plafond de temps demandé. Le
temps du premier passage par morceaux est comparable à celui de l'appel
historique ; chaque appel bénéficie du redémarrage du contexte.

## Modèle `large-v3`

Le fichier `ggml-large-v3.bin` n'est pas installé. Le test A/B entre
`large-v3-turbo` et `large-v3` n'a donc pas été exécuté. Le modèle par défaut
reste `large-v3-turbo`.

L'interface propose un commutateur **Précision maximale (large-v3)**. Il reste
désactivé tant que le fichier suivant n'est pas présent :

`whisper.cpp/models/ggml-large-v3.bin`

Lorsqu'il est activé, le choix est mémorisé et transmis au moteur pour les
traitements suivants. Les caches de morceaux portent un suffixe distinct afin
qu'une transcription Turbo ne soit jamais réutilisée pendant un essai
`large-v3`. Désactiver le commutateur rétablit immédiatement Turbo comme
modèle par défaut.

## Configuration

Les valeurs peuvent être surchargées dans `plaud_config.json` :

```json
{
  "whisper_params": {
    "max_context": 0,
    "entropy_thold": 2.4,
    "no_speech_thold": 0.6,
    "vad_threshold": 0.5,
    "vad_max_speech_duration_s": 30,
    "vad_speech_pad_ms": 150,
    "chunk_seconds": 600
  },
  "whisper_hallucinations": [
    "Sous-titrage Société Radio-Canada",
    "Sous-titres réalisés par la communauté d'Amara.org",
    "Sous-titrage ST' 501",
    "Merci d'avoir regardé cette vidéo",
    "Abonnez-vous"
  ]
}
```

## Langue source

La langue Whisper n'est plus forcée en français. L'interface permet de choisir
la langue parlée pour chaque fichier, ou de conserver la détection automatique.
Le choix est enregistré dans la file d'attente et dans la clé du cache des
morceaux. Un retraitement en anglais ne peut donc pas réutiliser par erreur des
JSON précédemment produits avec `-l fr`.

La note structurée (synthèse Ollama, PDF, Markdown) reste rédigée en français.
Seule l'annexe de transcription conserve la langue d'origine du média.
Changer la combo box met à jour la file du dossier courant ; au lancement d'un
traitement, la langue affichée dans l'UI prévaut toujours pour Whisper.