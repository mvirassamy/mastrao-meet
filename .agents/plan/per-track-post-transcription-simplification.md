# Simplification de la post-transcription par piste participant

## Objectif

Remplacer la reconstruction actuelle des locuteurs à partir d'un audio composite et d'événements VAD par une source audio distincte pour chaque piste microphone LiveKit.

Le résultat final doit avoir un seul chemin de production, moins d'états et moins de code. La transcription live reste inchangée.

## Décision métier

Le créateur choisit avant la réunion :

- enregistrer ;
- demander aux participants ;
- ne pas enregistrer.

La décision effective est verrouillée deux minutes avant la création de la salle. Un refus explicite interdit l'enregistrement. Une réunion autorisée est créée comme enregistrable. Le consentement n'est plus demandé dans la salle.

Cette politique appartient à Platform/Core. Meet reçoit uniquement une décision immuable `record` ou `do_not_record` liée à la réunion.

## Architecture cible

```text
Décision verrouillée avant la salle
        ↓
Création de la salle LiveKit
        ↓
Webhook signé track_published
        ↓
Filtre AUDIO + MICROPHONE d'une réunion enregistrable
        ↓
Track Egress direct vers le stockage objet
        ↓
Manifest : participant, piste, objet, début, fin, checksum
        ↓
ASR sans diarisation, une fois par piste
        ↓
Décalage sur l'horloge de la réunion
        ↓
Fusion chronologique déterministe
        ↓
Transcription finale nominative et synthèse
```

`AutoTrackEgress` ne sera retenu que si le produit veut réellement conserver toutes les pistes vidéo et de partage. Pour la transcription audio, le chemin normal filtre les pistes microphone afin d'éviter la surcapture et le stockage inutile.

## Invariants à préserver

- L'identité d'une piste provient du participant LiveKit lié au grant autorisé, jamais d'un nom libre fourni par le navigateur.
- Le nom affiché est un snapshot lié à cette identité.
- Une reconnexion produit une nouvelle piste mais conserve la même identité logique.
- Les paroles simultanées restent deux segments qui se chevauchent.
- Les webhooks, objets et reçus restent authentifiés et liés à la réunion et à l'organisation.
- Core conserve RLS, rétention, audit, admission ASR, budget et idempotence.
- Le rollback se fait par image immuable précédente, sans feature flag applicatif.

## Étape 1 — Tracer bullet isolé

1. Remplacer le `TrackCompositeEgress` HLS du chemin natif par un `TrackEgress` OGG direct vers le stockage objet.
2. Conserver dans le manifeste l'identité, le nom figé, le `track_sid`, l'origine exacte du fichier et la clé objet.
3. Transcrire deux pistes sans diarisation.
4. Fusionner les segments sur une horloge commune :

   ```text
   segment_meeting_ms = track_file_started_at_ms - meeting_origin_ms + segment_source_ms
   ```

5. Prouver deux participants, parole alternée, chevauchement, mute et reconnexion.

Le POC n'est pas déployé sur staging et n'ajoute aucun flag. Le changement Meet
et le changement Core forment une seule tranche fonctionnelle : Meet transmet
une référence d'objet signée et Core lit cet objet privé. Aucun second chemin
Base64 ou fallback ne reste dans le produit après la bascule.

## Étape 2 — Politique avant réunion

1. Remplacer la décision runtime par une politique de réunion dans Platform/Core.
2. Permettre au créateur de choisir `record`, `ask` ou `do_not_record`.
3. Pour `ask`, collecter les réponses avant la réunion ; un refus explicite produit
   `do_not_record`, une réponse positive ou absente produit `record`.
4. Verrouiller la décision effective deux minutes avant la création de la salle.
5. Créer la salle seulement après ce verrouillage et fournir à Meet uniquement la
   décision effective signée.
6. Retirer les pages, modales et appels de consentement dans la salle.

La planification des emails et leur contenu sont un chantier produit séparé. Le contrat attendu par Meet reste un simple résultat verrouillé.

## Étape 3 — Bascule et suppression

Après preuve locale et staging :

- supprimer le chemin audio composite pour la post-transcription ;
- supprimer `speaker-evidence` et le mapping VAD/acoustique ;
- supprimer le consentement et l'admission native dans la salle ;
- supprimer `TrackCompositeEgress`, le spool HLS, la conversion locale et le transfert Base64 du chemin natif ;
- supprimer les modèles, contrats, tâches, flags et tests devenus sans consommateur ;
- conserver l'enregistrement composite vidéo seulement s'il sert encore une fonctionnalité produit distincte.

Les anciennes migrations restent dans l'historique. Une nouvelle migration supprime les tables obsolètes.

## Preuves requises

### Automatiques

- deux participants et deux identités exactes ;
- trois participants sans intervenant fantôme ;
- arrivée tardive correctement positionnée ;
- mute sans texte produit ;
- chevauchements conservés ;
- reconnexion regroupée sous la même identité ;
- homonymes distincts par identifiant ;
- retry sans double piste ni double segment ;
- aucun changement sur la transcription live ;
- aucun nouveau feature flag ou chemin de compatibilité.

### Staging

- réunion réelle à deux appareils ;
- réunion réelle à trois participants avec chevauchement ;
- historique avec vrais noms ;
- synthèse produite ;
- aucun redémarrage ou OOM Egress/worker ;
- résultat disponible moins de deux minutes après une réunion de trois minutes.

## Séquence de livraison

1. PR Meet : production d'un objet Track Egress et de son manifeste signé.
2. PR Platform : ingestion de la référence objet privée et ASR sans transfert Base64.
3. Revue `review-code` puis `review-code-style`; corriger uniquement les défauts prouvés ou probables avec `review-fix-loop`.
4. Qualification locale du tracer bullet complet sur les deux dépôts.
5. PR Platform : politique de réunion verrouillée et contrat minimal vers Meet.
6. PR Meet : bascule du chemin actif et qualification staging.
7. PR Meet + Platform : suppression complète des anciens chemins, pages, flags, contrats et tables.

La bascule TrackComposite vers TrackEgress est volontairement unique et sans
compatibilité applicative. Avant la migration destructive, le rollout arrête les
workers natifs et vérifie que la table des captures de l'ancien pipeline est vide.
La migration refuse de s'appliquer si une capture existe encore.

Chaque PR doit rester cohésive et supprimer le code qu'elle remplace dès que la preuve de rollback par image suffit.

## Critère de fin

Le chantier est terminé lorsque la post-transcription utilise uniquement les pistes microphone LiveKit identifiées, que le composite/VAD n'est plus un chemin de secours, que le consentement runtime a disparu, et que le diff cumulé réduit matériellement le nombre de modules, d'états et de lignes de production.
