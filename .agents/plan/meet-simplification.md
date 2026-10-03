# Audit et plan de simplification Meet

Date : 3 octobre 2026. Responsable : SIMPLIFICATION MEET.

**Décision courante : Matthias autorise la vague resserrée 01–04 uniquement.**
La section 13 fait autorité sur les décisions historiques de ce document.
D'abord versionner ce plan dans une PR documentation, puis quatre PR
indépendantes, revues et validées avant fusion dans l'intégration.
Aucun flag ni pipeline ne doit changer ; aucun déploiement staging ni merge
vers `develop` n'est autorisé.

## Périmètre et état de référence

- Dépôt : `mvirassamy/mastrao-meet`.
- `origin` actualisé par `git fetch origin` avant l'audit.
- Base examinée : `87582bcea283d41abec857d9613d8adfa6a3258e`, dernier `origin/develop` lors de cette actualisation, merge de la PR #92 (POC post-transcription Track Egress).
- Branche locale créée depuis cette base : **`develop-meet-simplification`**.
- Worktree : `/Users/matthias/.codex/worktrees/meet-simplification/mastrao-meet`.
- Le checkout initial sur `main`, avec ses modifications et fichiers non suivis, est conservé intact. Il ne constitue pas la base de cet audit.
- Aucune modification du code applicatif, aucune PR, aucun commit, aucun push, déploiement ou merge dans cet audit. Seul ce plan est ajouté ; les dépendances et fichiers générés ignorés du frontend ont été installés pour les tests.
- Toutes les futures PR Meet doivent cibler `develop-meet-simplification`, jamais `develop`. Créer leurs branches `codex/meet-simplification-*` depuis l'intégration ; chaque PR doit pouvoir être revue et annulée seule.
- Lecture complémentaire de Platform, sans modification, au snapshot local `origin/develop` **`b411e1b1df3dfe444cb8e497480c1d3a52edb9ce`**. Cette référence n'a pas été actualisée ; elle ne prouve ni le dernier état distant de Platform ni son état déployé.

Le fonctionnement en staging est déclaré par l'utilisateur. Cet audit ne relève ni les digests des images réellement déployées, ni les valeurs runtime des flags, ni les appels des consommateurs externes. **Le code de develop ne suffit pas à identifier le chemin qui fonctionne actuellement en staging.** Les valeurs des templates Helm, parfois `False`, ne sont pas les valeurs effectives du déploiement.

Le skill `clean-code` et le `AGENTS.md` du dépôt ont été appliqués à la revue. Les recherches couvrent les routes, imports, tâches, configurations versionnées, composants, contrats et tests des parcours Meet ; elles ne constituent pas une certification d'absence de code mort dans tout le dépôt.

## Méthode de classement

- **PROUVÉ INUTILISÉ** : aucune référence applicative dans le périmètre livré, après recherche des symboles, imports, réexports et chargements dynamiques. Une référence dans un test ne prouve pas un usage produit. La preuve porte sur le SHA audité et doit être répétée avant suppression.
- **ENCORE UTILISÉ** : consommateur applicatif identifié ou responsabilité actuelle démontrée. Cela signifie « raccordé au code », pas « observé dans chaque réunion en staging ».
- **INCERTAIN, À VÉRIFIER PAR UNE PREUVE D'EXÉCUTION** : chemin conditionnel, contrat externe, historique persistant ou configuration opérateur dont l'abandon n'est pas démontré. Aucune suppression sur la seule base du nom `legacy`, `POC`, `fallback` ou d'une valeur par défaut.

Une ligne peut distinguer un mécanisme encore utilisé de l'incertitude sur une de ses variantes. Ce ne sont pas deux conclusions contradictoires.

## Cartographie des parcours à préserver

| Parcours | Chaîne examinée | Invariants |
| --- | --- | --- |
| Création canonique | Frontend création → `mastrao_meeting_history.create_meeting` → `mastrao_platform_facade.request_platform` → Platform `src/lib/meet/service.ts` → Core → handoff hôte | Idempotence ; identité et organisation correctes ; rattachement de la salle au grant ; pas de création de secours d'une salle Mastrao sur 404/410. |
| Entrée et visio | Invitation/handoff → `Lobby` → décisions applicables → token LiveKit → `Conference` | Permissions, refus et fermeture respectés ; même instance `Room` lors des changements de périphériques ; micro/caméra/partage et reconnexion préservés. |
| Live multilocuteur | Backend `subtitle*` → dispatch LiveKit → `multi_user_transcriber` / `live_transcriber` → `lk.transcription` → store frontend → panneau live | Identités stables, pistes distinctes, remplacement interim/final, absence de doublons, reconnexion, arrêt et nettoyage des agents. |
| Post-transcription composite | Enregistrement → Egress composite → artefact audio / speaker evidence → `mastrao_transcription_*` → Core → historique Platform → Meet | Attribution, origine temporelle, chevauchements, essais/idempotence, résultat et synthèse cohérents. |
| Post-transcription native | Observations RTC → admission/notice → `mastrao_native_capture_*` → Track Egress / manifeste → transfert source → ASR → Core | Piste microphone autorisée, identité signée, objet privé, rétention, checksum et horloge. Usage effectif en staging à établir. |
| Fin et historique | Lifecycle / fermeture → arrêt des captures → feedback / retour Platform → historique | Ne pas recréer une salle fermée ; ne pas annoncer « terminée » sur un 404 masqué ; conserver les artefacts finalisés et la cohérence revision/digest/source. |

## 1. Feature flags et sélecteurs encore présents

Les booléens d'autorisation, TLS, OIDC, stockage et confidentialité ne sont pas des flags de rollout à retirer mécaniquement.

| ID | Configuration / preuve | Classement et décision |
| --- | --- | --- |
| F01 | `MASTRAO_MEETING_RECORDING_ENABLED`, `MASTRAO_MEETING_RECORDING_START_ENABLED`, `MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED` : `meet/settings.py:328`, lecteurs dans `mastrao_recording_session`, `mastrao_recording_adapter`, `mastrao_recording_access`, `mastrao_transcription_adapter`. | **ENCORE UTILISÉ**. Suppression des gates **INCERTAINE** : relever création, arrêt, accès aux anciens artefacts et valeurs runtime. Ne pas confondre arrêt des nouveaux enregistrements et accès aux anciens. |
| F02 | `MASTRAO_NATIVE_CAPTURE_START_ENABLED`, `MASTRAO_NATIVE_PREENTRY_ENABLED`, `MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED`, `MASTRAO_NATIVE_ASR_ENABLED` : `settings.py:509–530`, admission, notice, capture, transfert et worker natifs. | **ENCORE UTILISÉ** dans le code. Bascule unique **INCERTAINE** : prouver ensemble la décision de capture, les workers et le consommateur Core ; ne jamais supprimer isolément un verrou de consentement. |
| F03 | Catalogue `core/api/feature_flag.py:12` : `RECORDING_ENABLE`, `RECORDING_STORAGE_EVENT_ENABLE`, `ROOM_SUBTITLE_ENABLED`, `FILE_UPLOAD_ENABLED`, `ADDONS_ENABLED`, `APPLICATION_ENABLED`, `ROOMKIT_ENABLED`, `CONNECTION_TEST_ENABLED`. Décorateurs sur les viewsets correspondants. | **ENCORE UTILISÉ**. Fonctionnalités/configurations produit distinctes ; aucune preuve globale d'obsolescence. Conserver les contrôles des API. |
| F04 | `features/analytics/enums.ts` : `transcription-summary`, `screen-recording`, `face-landmarks`, `noise-reduction`, `candidate-polling`, `metadata-collector`. Lecteurs : hooks d'enregistrement, effets, réduction de bruit et `ConnectionObserver`. | **ENCORE UTILISÉ**. Les hooks autorisent certaines fonctionnalités lorsque l'analytics est désactivé : comportement à tester avant toute simplification. Valeurs PostHog par cohorte **INCERTAINES**. |
| F05 | `core/analytics/user_feature_flags.py` : `summary-enabled`, lu par `recording/event/notification.py:375`. | **ENCORE UTILISÉ**. Ne pas assimiler automatiquement au flag frontend `transcription-summary` : activation de synthèse et accès UI sont deux décisions. |
| F06 | `STT_PROVIDER` (`deepgram`, `kyutai`, `openai-live`), `ENABLE_SILERO_VAD` : `agents/multi_user_transcriber.py:37–55,165–216` ; `ROOM_SUBTITLE_PROVIDER` dans les settings et la réconciliation. | **ENCORE UTILISÉ**, plusieurs implémentations concrètes. Fournisseur staging et anciens workers **INCERTAINS**. Aucun retrait dans la première vague. |
| F07 | `METADATA_COLLECTOR_ENABLED`, `METADATA_COLLECTOR_ENABLE_VAD` ; `RECORDING_ENCODING_ENABLED` dans `recording/worker/factories.py:42`. | **ENCORE UTILISÉ**. Les métadonnées servent aussi à speaker evidence ; ne pas les supprimer parce qu'un pipeline par piste existe. Encodage personnalisé à vérifier sur les images et la charge réelles. |
| F08 | `SUMMARY_SERVICE_VERSION` : sélection v1/v2 dans `recording/event/notification.py:215` ; défaut v1 dans `settings.py:1314`. | **ENCORE UTILISÉ** comme sélection. Branche v1 **INCERTAINE** : le serveur summary livré a supprimé v1, mais le client peut cibler un service externe. Relever endpoint et trafic sans secrets. |
| F09 | `LIVEKIT_FORCE_WSS_PROTOCOL`, `LIVEKIT_ENABLE_FIREFOX_PROXY_WORKAROUND` : `Conference.tsx:278–310,400–412` ; WSS activé dans le template Helm commun. | **ENCORE UTILISÉ**. Suppression **INCERTAINE**, conditionnée à la matrice navigateurs/proxy réellement supportée. |
| F10 | `CELERY_ENABLED` : `core/tasks/_task.py`, dispatch natif/transcription et `SubtitleService`. `MASTRAO_ASR_QUALIFICATION_MODE` et profils autorisés : settings/contrats ASR. | **ENCORE UTILISÉ**. L'exécution synchrone et les modes de qualification exigent un inventaire des environnements et des tests ; ne pas les transformer implicitement en exécution de production. |
| F11 | `EXTERNAL_API_ENABLED`, `ROOM_TELEPHONY_ENABLED`, `LIVEKIT_EXPLICIT_ROOM_CREATION`, `ALLOW_UNREGISTERED_ROOMS`, `SIGNUP_NEW_USER_TO_MARKETING_EMAIL`. | **ENCORE UTILISÉ** par les routes/services/settings. Hors suppressions sûres : déterminer les engagements API, SIP, salles génériques et marketing. |

`VITE_ANALYZE` et `VITE_BUILD_SOURCEMAP` sont des options de build. Les switches OIDC/TLS/authentification, `FILE_UPLOAD_APPLY_RESTRICTIONS` et `RECORDING_ENABLE_STORAGE_EVENT_AUTH` protègent des frontières réelles : aucune proposition de suppression fondée uniquement sur leur présence.

## 2. Code mort et modules remplacés

| ID | Élément | Classement, preuve et périmètre supprimable |
| --- | --- | --- |
| D01 | `features/rooms/livekit/hooks/useFocusToggleParticipant.ts:7` | **PROUVÉ INUTILISÉ** dans le frontend livré. Le nom du symbole/fichier n'apparaît que dans sa définition ; pas d'import, réexport ou découverte dynamique de hooks. Supprimer le fichier entier (32 lignes). |
| D02 | `features/rooms/utils/getParticipantIsAuthenticated.ts:3` | **PROUVÉ INUTILISÉ**, même preuve. Supprimer le fichier de 8 lignes, sans remplacer les vérifications d'identité actives. |
| D03 | `utils/livekit.ts:7`, `isChromiumBased` | **PROUVÉ INUTILISÉ**. Une seule occurrence dans le dépôt. Supprimer uniquement cette fonction ; conserver `isFireFox`, `isSafari`, `isLocal`, `isMacintosh`. |
| D04 | `icons/index.tsx:119`, `CaptionsIcon` | **PROUVÉ INUTILISÉ**. Une seule occurrence, pas de registre dynamique d'icônes identifié. Supprimer uniquement l'export et l'import associé s'il devient inutilisé ; conserver la famille Heroicons imposée. |
| D05 | `subtitle/store/liveTranscriptionContract.ts:273`, `toLegacyTranscriptionEvent`, réexport `index.ts:16`, discriminant `legacy-event` | **PROUVÉ INUTILISÉ** en production : seuls définition, réexport et test unitaire y font référence. `LiveTranscriptionProvider.tsx:186–203` ne s'abonne plus à `TranscriptionReceived` ; `LiveTranscriptionProvider.test.tsx:539–577` vérifie ce non-abonnement. Candidat très limité, distinct du parseur de text streams encore actif. Ne pas retirer `LEGACY_LEG_ID`, les attributs LiveKit ni les fallbacks du text stream. |
| D06 | Ancien spool HLS / conversion native locale : `mastrao_native_spool.py`, `mastrao_native_audio.py`, POC `transcription_poc*` aperçus dans le checkout initial | **PROUVÉ INUTILISÉ dans la base auditée car absent**, pas une nouvelle suppression à réaliser. Des fichiers existent dans le travail local initial ; ils ne doivent surtout pas être effacés par ce chantier. |
| D07 | `mastrao_transcription_*`, `mastrao_speaker_evidence_adapter.py`, `agents/metadata_collector.py` | **ENCORE UTILISÉ** : routes internes, tâches et hooks Egress raccordés. Le plan par piste ne constitue pas une preuve qu'ils sont remplacés en staging. |

Reproduction de la recherche D01–D05 :

```sh
git grep -n -E 'useFocusToggleParticipant|getParticipantIsAuthenticated|isChromiumBased|CaptionsIcon|toLegacyTranscriptionEvent|legacy-event'
git grep -n -E 'import.meta.glob|require.context|import\(' -- src/frontend
```

Examiner aussi les imports namespace/réexports avant suppression ; le simple décompte de symboles est un filtre, pas une preuve suffisante à lui seul. Les chargements dynamiques constatés portent sur les routes, traductions et dépendances nommées, pas sur ces hooks/utilitaires.

## 3. Chemins concurrents, compatibilité et consentement

| ID | Élément et preuves | Classement et preuve manquante |
| --- | --- | --- |
| C01 | `Lobby.tsx:333–365` monte `RecordingConsent` puis `NativeRecordingConsent` selon les projections. API `recordingConsent.ts`, `nativeNotice.ts` ; actions `viewsets.py:640–755`. | **ENCORE UTILISÉ**. Le remplacement par une politique verrouillée avant réunion est une cible du plan existant, pas un fait. Pour retirer : trace création → décision signée → token → capture, hôte/invité/refus/arrivée tardive, sans appel des anciens endpoints. |
| C02 | `Conference.tsx:319–397` active l'enregistrement après connexion et publication de média ; polling des états à `:144`. | **ENCORE UTILISÉ**. Ne pas enlever les retries, l'idempotency key ou le contrôle `isEnding` en les assimilant à des fallbacks obsolètes. |
| C03 | Composite via `/internal/mastrao/recordings/*`, transcription/speaker-evidence ; natif via `/internal/mastrao/captures/native/*` ; tous enregistrés dans `core/urls.py`. | **ENCORE UTILISÉ** dans le graphe applicatif ; exclusivité staging **INCERTAINE**. Captures natives et enregistrement vidéo peuvent répondre à deux besoins distincts. Prouver le consommateur de chaque route et l'état des jobs persistants. |
| C04 | `0050_native_source_manifest.py` remplace `output_prefix` par `source_manifest`, mais refuse toute table native non vide. `mastrao_native_capture_adapter.py:152` construit le Track Egress. | **ENCORE UTILISÉ**. Risque de migration/déploiement majeur. Ne pas supprimer les anciennes migrations ni considérer #92 comme preuve de migration staging. Vérifier migration appliquée et état des captures avant une future bascule. |
| C05 | `mastrao_native_source_transfer.py` envoie un manifeste signé, mais `mastrao_native_asr_client.py:33–79` reçoit encore `audio_base64` de Core avant l'appel gateway. | **ENCORE UTILISÉ**. La suppression du transfert Base64 Meet → Core ne signifie pas sa disparition de tous les échanges ASR. Aucun changement sans nécessité et preuve de bout en bout. |
| C06 | `meetingHistoryApi.ts:39–68,307–333` reconstruit une projection depuis des anciens statuts et accepte plusieurs emplacements de projection. | **ENCORE UTILISÉ** ; nécessité des variantes **INCERTAINE**. Platform produit encore statuts anciens et projections. Capturer les réponses de liste/détail/synthèse du producteur déployé, y compris historique antérieur et résultats partiels. |
| C07 | `recording/event/notification.py:27–41`, ancien `SCREEN_RECORDING_BASE_URL` avant `RECORDING_BASE_URL`. | **INCERTAIN, À VÉRIFIER PAR UNE PREUVE D'EXÉCUTION** : compatibilité explicitement dépréciée mais lecteur actif. Inventaire des configurations effectives et lien de téléchargement issu d'une notification nécessaires. |
| C08 | `stores/accessibility.ts:127–153`, migration depuis `STORAGE_KEYS.NOTIFICATIONS`. | **INCERTAIN, À VÉRIFIER PAR UNE PREUVE D'EXÉCUTION**. L'absence d'écriture récente ne prouve pas l'absence de profils navigateur anciens. Retirer seulement après décision sur la durée de migration et preuve de conservation des préférences. |
| C09 | `core/tasks/_task.py`, `.delay`/`.apply_async` synchrones sans Celery ; `services/subtitle.py:164–210`, convergence synchrone bornée. | **ENCORE UTILISÉ**. Ne pas retirer avant inventaire dev/test/runtime ; la validation de configuration exige déjà Celery pour certains traitements post-réunion. |
| C10 | `subtitle_reconciliation.py:255`, adoption de dispatchs historiques sans métadonnées. | **INCERTAIN, À VÉRIFIER PAR UNE PREUVE D'EXÉCUTION** pour retrait. Il faut lister les dispatchs réels et prouver l'extinction des anciens agents. Conserver verrous, fencing de fermeture et list-delete-list. |
| C11 | `subtitle/store/README.md` prétend conserver un bridge `TranscriptionReceived`. | **PROUVÉ INUTILISÉ** pour le bridge décrit, contredit par le provider et ses tests. Corriger cette documentation avec D05 ; ne pas généraliser aux attributs text-stream encore acceptés. |
| C12 | Création sur 404 dans `Conference` pour salles génériques, mais branche spécifique Mastrao qui refuse de recréer la salle. | **ENCORE UTILISÉ**. Préserver cette distinction tant que les salles génériques sont exposées ; une seule voie active par fonctionnalité ne signifie pas confondre deux produits/contrats. |

## 4. Abstractions et responsabilités

| ID | Élément | Classement / proposition |
| --- | --- | --- |
| A01 | `services/marketing.py:32,52,134` : `MarketingServiceProtocol`, `BrevoMarketingService`, factory `import_string` et cache ; `settings.py:1359`, `MARKETING_SERVICE_CLASS`. | **ENCORE UTILISÉ**, une seule implémentation livrée, appelée à l'inscription (`authentication/backends.py:177`). Simplification en constructeur concret envisageable ; éventuel override opérateur **INCERTAIN**. Conserver cache et erreurs métier. |
| A02 | `recording/worker/factories.py`, `WorkerService`, `BaseEgressService`. | **ENCORE UTILISÉ**, deux implémentations audio/vidéo dans les settings. Ce n'est pas une abstraction à implémentation unique. Besoin de contrats Egress distincts à conserver tant que les deux usages sont actifs. |
| A03 | `recording/event/parsers.py`, `EventParser`, `BaseS3Parser`. | **ENCORE UTILISÉ**, implémentations `MinioParser` et `S3Parser`. Conserver sans preuve d'abandon d'un format d'événement. |
| A04 | `analytics/base.py`, `AnalyticsBackend`, `NoOpAnalytics` et `PostHogAnalytics`. | **ENCORE UTILISÉ**, deux comportements réels (analytics désactivé/activé). Pas de suppression automatique de l'interface ; ne pas supprimer l'option sans analytics. |
| A05 | `mastrao_platform_facade` et `mastrao_core_http`. | **ENCORE UTILISÉ**. Les deux font du HTTP borné, mais l'un transmet une session OAuth vers Platform, l'autre des contrats internes signés vers Core. Deux frontières réelles : aucun client/provider universel à introduire. |

Tailles mesurées sur les fichiers suivis, hors tests et migrations ; ce sont des signaux de navigation, pas des défauts automatiques :

| Fichier | Lignes | Responsabilités / classement |
| --- | ---: | --- |
| `core/models.py` | 2336 | Utilisateurs, salles, grants, RTC, captures, enregistrements, transcription, fichiers et applications. **ENCORE UTILISÉ**. Ne pas déplacer globalement les modèles : contraintes Django/migrations/imports à préserver. |
| `meet/settings.py` | 2047 | Configuration de tous les domaines/environnements. **ENCORE UTILISÉ**. Retirer les réglages au moment de retirer leur consommateur, pas via une réécriture des settings. |
| `core/api/viewsets.py` | 1993 | Room, fichiers, recordings, users, diagnostics. `RoomViewSet` seul couvre admission, consentements, lifecycle, webhooks, sous-titres et modération. **ENCORE UTILISÉ**. Première extraction possible : FileViewSet, sans refactorer RoomViewSet en même temps. |
| `EffectsConfiguration.tsx` | 976 | État et mise à jour du processeur, upload/suppression des fonds, prévisualisation, sélection et rendu. **ENCORE UTILISÉ**. Isoler d'abord les fonds personnels. |
| `subtitle_reconciliation.py` | 944 | Intents, dispatchs, adoption, états et nettoyage concurrents. **ENCORE UTILISÉ** ; sensible au live, hors première vague. |
| `agents/metadata_collector.py` | 835 | Participants, VAD, collecte et artefacts. **ENCORE UTILISÉ**, risque post-transcription. |
| `mastrao_transcription_contract.py` / `adapter.py` / `pipeline.py` | 756 / 746 / 706 | Contrats, admission, dispatch/essais. **ENCORE UTILISÉ** ; longueur seule insuffisante pour intervenir. |
| `summary/core/celery_worker.py` | 708 | ASR, formatage, synthèse et traitement de tâches. **ENCORE UTILISÉ** dans le service summary ; usage du service en staging **INCERTAIN**. |
| `MeetingHistoryDetailView.tsx` | 648 | Requête et déclenchement de synthèse, métadonnées, présentation synthèse/transcription, groupement de tours. **ENCORE UTILISÉ**. Des sous-composants existent déjà ; déplacer une section cohérente, sans bibliothèque générique. |
| `Conference.tsx` | 614 | Chargement/création, lifecycle, Room/media, activation enregistrement, navigation et rendu. **ENCORE UTILISÉ**. Extraction bornée à une responsabilité, avec preuve de stabilité de Room. |
| `Join.tsx` / `Lobby.tsx` | 486 / 485 | Preview/périphériques ; admission/consentements/lifecycle. **ENCORE UTILISÉ**. Conserver la séparation déjà établie des pistes de preview. |
| `meetingHistoryApi.ts` | 438 | Parsing de contrat, compatibilité, fusion des révisions et HTTP. **ENCORE UTILISÉ**. Ne pas réduire la fusion à « dernière réponse reçue ». |

### Conditions difficiles à lire, prouvées dans le code actif

- **ENCORE UTILISÉ** : `Conference.tsx:144–160`, ternaire imbriqué du polling (`active` → 2000 ms, états transitoires → 1000 ms, sinon arrêt). À remplacer par une fonction métier explicite, sans changer les intervalles.
- **ENCORE UTILISÉ** : `GuestInvitation.tsx:65–91`, ternaires imbriqués et répétition de `loading`, `terminal-error`, `temporary-error`. Un rendu nommé avec `switch` sur les quatre états conserve texte, alertes et disponibilité du bouton.
- **ENCORE UTILISÉ** : `MeetingHistoryDetailView.tsx:98–107`, calcul imbriqué `pending/failed/idle` ; préserver le traitement particulier de 401/404/409.
- **ENCORE UTILISÉ** : `meetingHistoryApi.ts:32–37,205–214,430–438`, normalisation et fusion conditionnelles imbriquées. Fonctions nommées possibles ; le changement des contrats anciens est une autre PR, conditionnelle.
- **ENCORE UTILISÉ** : listes d'états d'enregistrement dans `Conference` et `Lobby`. Les cadences ne sont pas identiques ; ne pas les fusionner arbitrairement sous un helper paramétrable.

## 5. Doublons frontend / meet-api / Platform

| ID | Constat | Classement et frontière à conserver |
| --- | --- | --- |
| X01 | `api/apiUrl.ts`, `api/mediaUrl.ts`, `rooms/api/redeemGuestInvitation.ts` répètent le choix/nettoyage de l'origine API. | **ENCORE UTILISÉ**, duplication locale exacte. Petit helper d'origine possible, mais faible gain ; ne pas créer un client HTTP universel. Pas prioritaire. |
| X02 | `useHasRecordingAccess` / `useHasFeatureWithoutAdminRights` recopient trois lectures de capacités et inversent le rôle admin. | **ENCORE UTILISÉ**. Une extraction n'est justifiée que si elle réduit vraiment la complexité ; surtout ne pas déplacer l'autorisation backend dans ces hooks UI. |
| X03 | Platform `src/lib/meet/contracts.ts` expose anciens statuts ET projections ; `service.ts:387–388` les produit ; Meet `meetingHistoryApi` reconstruit les projections en leur absence. | **ENCORE UTILISÉ**. Double représentation prouvée ; retrait des anciens formats **INCERTAIN** sans capture des réponses et inventaire des consommateurs. Autorité métier : Platform/Core ; adaptation d'affichage : frontend ; meet-api conserve OAuth, bornes HTTP et handoff. |
| X04 | Platform `service.ts:888–909` choisit transcript canonique/revu/natif ; Meet reçoit puis fusionne revisions/digests. | **ENCORE UTILISÉ** dans les snapshots examinés. Ce ne sont pas deux algorithmes équivalents : choix de source côté serveur, protection contre réponses obsolètes côté client. Aucun déplacement du choix métier vers le navigateur. |
| X05 | Platform `service.ts:146–176` retente le mode transcrit puis retombe sur recorded-only après 404 ; Meet affiche/active selon les projections de consentement. | **ENCORE UTILISÉ** dans Platform audité ; nécessité du fallback staging **INCERTAINE**. Retrait dépend d'une preuve que la voie post-transcription promise est admise, pas d'un nettoyage local Meet. |
| X06 | Contrats signés room/recording/transcription et DTO frontend répètent des identifiants, états et validations. | **ENCORE UTILISÉ**. Validation de sécurité côté serveur et parsing d'affichage côté client sont nécessaires à leurs frontières. Unifier les fixtures de contrat si une divergence est reproduite ; ne pas créer un nouveau framework de schémas multilangage. |

Aucune PR Platform n'est créée ni prescrite vers son `develop` dans ce chantier Meet. Si une suppression exige un changement Platform/Core, elle reste suspendue jusqu'à coordination et définition explicite de sa branche d'intégration. Les instantanés lus ne constituent pas un audit complet de Platform.

## 6. Ordre des petites PR

### Règles communes avant et après chaque PR

Avant de coder : relever le SHA d'intégration, le comportement attendu, ses cas limites, les tests existants et la preuve fonctionnelle applicable. Exécuter cette référence avant le changement. Ne modifier aucun fichier de transcription live/post pour une simple préférence de structure.

Chaque PR a un seul objectif et comprend sa suppression réelle, ses tests ciblés et sa preuve après changement. Mesurer les branches alternatives, duplications, états et couches retirés ; déplacer des lignes sans rendre une responsabilité plus lisible ne suffit pas. Pas de nouveau flag, fallback, provider générique, adaptation de compatibilité ou variation hypothétique. Ne pas forcer un objectif de lignes négatives au détriment de la lisibilité.

Après création de la PR seulement, `review-fix-loop` peut corriger un défaut de cette PR si la revue fournit une reproduction ou une preuve précise. Les suggestions architecturales ou hypothétiques ne déclenchent aucun chantier. Aucun merge dans `develop`.

### Première vague : indépendante des décisions de bascule

| PR | Responsabilité unique / périmètre | Comportement et vérification avant/après | Gain attendu et preuve fonctionnelle |
| --- | --- | --- | --- |
| 01 | Retirer D01–D04, utilitaires et export frontend sans consommateur. | Répéter les recherches ; build TypeScript/Vite et lint ciblé ; tests preview/participants déjà présents selon leurs dépendances. | Deux fichiers et deux exports inutiles en moins. Ouvrir une réunion à deux clients, épingler un participant, changer caméra et afficher les sous-titres ; aucun changement visible. Aucun nouveau test miroir de la suppression. |
| 02 | Rendre explicites les états de `GuestInvitation`. Un seul rendu nommé / switch, sans modifier `redeemGuestInvitation`. | Ajouter/cibler un test de comportement couvrant invitation absente, requête en cours, refus terminal 404, erreur temporaire puis succès ; tests backend guest/share inchangés. | Plus de ternaire imbriqué ni répétition dispersée des états. Preuve navigateur : invitation valide, expirée, puis erreur transitoire reproduite ; aucun double redeem. |
| 03 | Remplacer uniquement les ternaires imbriqués de normalisation/fusion dans `meetingHistoryApi`. Aucun retrait de contrat. | `meetingHistoryApi.test.ts`, `useMeetingHistory.test.ts`, `MeetingHistoryBackendContract.test.tsx` ; mêmes projections et bodies pour mêmes réponses et ordre de réception. | Décisions nommées, pas de nouveau format accepté. Historique réel avec résultat disponible/partiel/en cours et réponse obsolète simulée ; pas de synthèse incohérente. |
| 04 | Isoler la présentation de la transcription historique hors de `MeetingHistoryDetailView`. Conserver parsing, groupement et données à comportement identique. | `MeetingHistoryFlow`, `MeetingHistorySummaryRequest`, formatage et contrat backend. Le calcul `SummaryRequestState` peut être traité dans une PR distincte si nécessaire, pas mélangé à cette extraction. | Une section métier directement navigable, aucune API de composant générique. Vérifier noms, ordre des tours, timestamps, troncature, navigation clavier et contenu partiel. Aucun changement au producteur post-transcription. |
| 05 | Isoler les fonds personnels de `EffectsConfiguration` : upload, suppression et sélection de cette section. | Relever les tests preview/media existants ; vérifier les appels files (succès, type/taille refusés, limite atteinte), plus tests backend files si le contrat est concerné. | Sortir une responsabilité de ce fichier de 976 lignes, sans wrapper universel. Dans une réunion : fond local/invité et fond téléversé/utilisateur, suppression puis changement de caméra ; aucun arrêt de piste ou reconnexion induite. |
| 06 | Déplacer seulement `FileViewSet` vers un module API fichiers ; router/imports directs mis à jour. | Suite `core/tests/files`, permissions, schéma OpenAPI ; `makemigrations --check --dry-run` sans migration. Ne pas toucher à RoomViewSet ni créer de mixin de secours/réexport de compatibilité. | Retirer environ 350 lignes de responsabilités fichiers du module de routes général. Upload/lecture/suppression d'un fond et refus d'accès interutilisateur prouvés. Vérifier tous les imports et patch targets des tests. |
| 07 | Isoler l'activation d'enregistrement de `Conference` dans un hook local nommé. | `Conference.test.tsx`, consentements backend ; caractériser avant extraction connexion + média + droit hôte + décision, retry même request ID, épuisement et annulation à la fermeture. Ne pas changer le polling/lifecycle dans cette PR. | Une responsabilité retirée du composant, aucun nouvel état ni retry. Réunion réelle avec connexion lente, retry contrôlé puis fin : une activation effective, instance Room conservée et artefact final identique. PR plus risquée, après les précédentes. |

Ces PR partent chacune de l'intégration courante et ne doivent pas dépendre d'une branche de feature non intégrée. Les PR 03 et 04 ne changent pas le même contrat ; la seconde ne requiert pas de nouvelle API introduite par la première. Toute extraction dont la preuve fonctionnelle n'est pas disponible reste non intégrable.

### Deuxième vague : conditionnelle, sans obligation de modifier les transcriptions

| PR | Responsabilité unique | Condition d'ouverture et validation |
| --- | --- | --- |
| 08 | Retirer D05 et corriger C11, exclusivement le bridge live déjà déconnecté. | Preuve statique et test de non-abonnement déjà établis, complétés avant changement par une réunion live multilocuteur sur l'image de référence. Conserver le test de non-abonnement ; enlever seulement le test du helper mort. Relancer toute la suite `features/subtitle` et la même réunion/reconnexion après changement. Aucune modification des flux live actifs. |
| 09 | Remplacer la sélection dynamique marketing par Brevo direct. | Confirmer `MARKETING_SERVICE_CLASS` effectif et absence d'extension externe. Tests marketing + création d'utilisateur ; preuve d'appel sur un endpoint contrôlé sans envoyer de contact réel. Sinon différer. Conserver cache et désactivation marketing. |
| 10 | Retirer uniquement `SCREEN_RECORDING_BASE_URL`. | Prouver qu'aucun déploiement supporté ne le fournit ; comparer lien de notification et téléchargement authentifié avant/après ; tests notification/access. Retirer setting et doc associés dans la même PR. |
| 11 | Retirer uniquement le client Summary v1. | Confirmer endpoints déployés, aucun consommateur v1 et jobs en attente compatibles v2. Tests notification et service summary v2 ; transcription/synthèse réelle sur l'environnement qualifié. Sans cette preuve, ne pas toucher à ce chemin post-transcription. |
| 12 | Retirer les fallbacks de projection historiques frontend. | Capturer le contrat Platform déployé sur liste/détail/synthèse, y compris anciennes réunions ; inventaire des clients. Ne modifier que les branches de parsing prouvées obsolètes, conserver revision/digest/source et fusion anti-régression. Suites historique + ancien et nouveau résultat réel. |

Le nettoyage des flags, consentements, composite/VAD et providers n'est **pas** une PR globale 13. Il nécessite d'abord les preuves ci-dessous. Si elles justifient une intervention, rédiger un sous-plan : **une capacité ou une variante retirée par PR**, suppression de son gate/code/tests obsolètes/config correspondants en une tranche cohérente. Aucun fallback de retour ; retour à une image antérieure seulement si la base reste compatible. Une migration destructive réclame une stratégie de restauration adaptée, pas une promesse de rollback par image.

## 7. Zones risquées et preuves qui débloquent une suppression

1. **Identifier la référence réellement déployée.** Obtenir un relevé sans secrets des digests frontend/meet-api/agents/workers/Egress/Platform/Core, migrations appliquées, providers et flags pertinents. Relier ce relevé au scénario réussi. Les templates versionnés ne suffisent pas.
2. **Captures et consentements.** Suivre une réunion enregistrée et une non enregistrée, un refus explicite, un invité et une arrivée tardive. Observer décision canonique, émission du token et autorisation de capture. Tant que C01 reste dans ce chemin, conserver ses composants et endpoints.
3. **Ancien composite et speaker evidence.** Établir pour les nouvelles réunions ET les artefacts historiques quel pipeline produit et lit la transcription. Vérifier tâches en attente, retries, métadonnées de locuteur et accès vidéo. Ne pas supprimer l'enregistrement vidéo si le produit l'utilise encore.
4. **Track Egress et migration 0050.** Vérifier l'état de la table native, les manifests et les objets, l'application de la migration et les consommateurs Core. La migration refuse une table non vide : cette précondition ne doit pas être contournée.
5. **Live.** Observer le provider actif, les formats réellement publiés et le cycle des dispatchs, dont les dispatchs préexistants. Préciser les garanties d'ordre/identité avant tout retrait de provider, adoption legacy ou fallback text-stream.
6. **Historique et Platform.** Vérifier les contrats et sources réelles. Un transcript natif peut encore être la seule source de certaines anciennes réunions ; la sélection canonique/revue peut répondre à un besoin métier actuel.
7. **Compatibilités périphériques.** Firefox/proxy, préférences navigateur anciennes, endpoints externes, SDK/addons/RoomKit/SIP et mode sans Celery doivent être inventoriés avant retrait. L'absence d'activité durant une seule réunion ne prouve pas leur abandon.

## 8. Tests protégeant les parcours

| Parcours | Tests existants à cibler | Preuve fonctionnelle requise pour une PR qui le touche |
| --- | --- | --- |
| Création/handoff/invité | Frontend `CreateMeetingMenu.test.tsx`, `createGuestInvitationShare.test.ts`, `Lobby.test.tsx` ; backend `test_mastrao_meeting_history.py`, `test_mastrao_host_handoff.py`, `test_mastrao_guest_invitation.py`, `test_mastrao_guest_invitation_share.py`, `test_mastrao_room_adapter.py`. | Créer une réunion, répéter la même commande idempotente, ouvrir le lien sur un second appareil, refuser lien expiré/autre identité sans exposer de token. |
| Visio/preview/lifecycle | `Conference.test.tsx`, `VideoPreviewLifecycle.test.tsx`, `MediaControls.test.tsx`, `MeetingLifecycleProvider.test.tsx`, `EndMeetingButton.test.tsx` ; backend rooms/lobby/close/livekit_events. | Audio bidirectionnel, caméra, partage, mute, changement de périphérique, perte/reprise réseau ; quitter ≠ terminer ; ancienne URL ne recrée pas la salle. |
| Consentements/capture | `RecordingConsent.test.tsx`, `NativeRecordingConsent.test.tsx` ; backend `test_mastrao_recording_consent.py`, `test_mastrao_native_preentry.py`, `test_mastrao_native_grant_interop.py`, `test_mastrao_native_capture.py`, tests token/RTC. | Hôte/invité, accepter/refuser/retirer selon le contrat actuel, aucun démarrage non autorisé ; connexion média avant activation ; double livraison sans double capture. |
| Live multilocuteur | Tout `features/subtitle` (provider, contrat, reducer, cache locuteurs, panneau, contrôle) ; `src/agents/tests/test_live_transcriber.py` ; backend `test_subtitle_control.py`, `services/test_subtitle*.py`, `rooms/test_api_rooms_subtitle.py`. | Deux puis trois participants, alternance, chevauchement, homonymes, arrivée tardive, mute et reconnexion ; noms exacts, final remplace interim, pas de doublons/locuteur fantôme ; arrêt et fermeture nettoient les agents. |
| Post-transcription | Backend `test_mastrao_transcription.py`, `test_mastrao_transcription_attempt.py`, `test_mastrao_transcription_flac.py`, `test_mastrao_speaker_mapping.py`, `test_mastrao_speaker_evidence.py` ; native capture/drain/stop/source_transfer/asr/task_routing selon le chemin touché. | Réunion réelle courte avec phrases attendues et horodatages, deux/trois voix et chevauchements ; résultat nominatif, origine temporelle exacte, une seule finalisation, historique/synthèse disponibles ; replay d'un webhook/receipt contrôlé sans duplication. |
| Historique | `meetingHistoryApi`, `useMeetingHistory`, `MeetingHistoryBackendContract`, `MeetingHistoryFlow`, `MeetingHistorySummaryRequest`, formatage ; backend history. Platform : `src/__tests__/meet/service.test.ts` et tests de projections Core, si un contrat change après coordination. | Réunion ancienne et nouvelle, partielle/complète/vide/échouée ; réponse retardée ; pas de retour à une ancienne révision ni de synthèse liée à un mauvais digest. |
| Déploiement/config | `python3 -m unittest discover -s tests -t . -v` ; scripts ciblés `src/helm/tests` ; migrations Django et validations de configuration. | Build depuis le SHA exact ; vérifier workers/queues/image et migration sur environnement isolé. Ne pas publier automatiquement sur develop/staging depuis la branche d'intégration. |

Avant/après une PR risquée, conserver un dossier de preuve sans données sensibles : SHA/digests, scénario et résultat attendu, compteurs de participants/pistes/jobs, états et timestamps, logs expurgés, résultat réel. Comparer au même scénario de référence. Des tests avec fakes ne prouvent pas une transcription réelle.

Commandes de référence (depuis les sous-répertoires indiqués, dépendances verrouillées et services de test isolés) :

```sh
# Racine
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v

# src/frontend
npm ci --ignore-scripts --no-audit --no-fund
npm test -- src/features/subtitle src/features/meetingHistory
npm run build
# Lint ciblé sur les fichiers changés ; lint/check globaux exigés par CI.
npm run lint
npm run check

# src/backend : environnement Test configuré comme .github/workflows/meet.yml,
# PostgreSQL/Redis/S3 de test ; jamais les bases staging.
uv sync --locked --all-extras
uv run pytest core/tests/test_mastrao_meeting_history.py core/tests/test_mastrao_recording_consent.py
uv run python manage.py makemigrations --check --dry-run

# src/agents : contrat de transport, pas appel ASR réel
uv sync --locked
uv run python -m unittest discover -s tests -v
```

Remplacer les fichiers pytest par la sélection correspondant à la PR ; ne pas lancer systématiquement une qualification fournisseur pour une suppression d'export. Les jobs frontend de `meet.yml` inspectés exécutent lint/format ; ne pas présumer que les 151 tests Vitest ci-dessous sont automatiquement une gate distante. Joindre leur résultat à la PR ou faire du branchement CI une proposition séparée si nécessaire.

## 9. Vérifications effectivement exécutées pendant cet audit

- `git fetch origin`, inspection de la branche/base, du graphe d'appels et des points d'entrée ; lecture des instructions et du plan existant.
- Installation frontend depuis le lock avec `npm ci --ignore-scripts --no-audit --no-fund` : succès. Node local **26.0.0**, npm **11.12.1** ; avertissement d'engine pour `i18next-parser` (attend 18/20/22). La future validation reproductible doit utiliser la version Node retenue par CI.
- Contrats racine : **62 tests réussis**.
- Frontend : **22 fichiers, 151 tests réussis**. Sélection exacte :

```sh
npm test -- \
  src/features/subtitle \
  src/features/rooms/components/Conference.test.tsx \
  src/features/rooms/components/Lobby.test.tsx \
  src/features/rooms/components/RecordingConsent.test.tsx \
  src/features/rooms/components/NativeRecordingConsent.test.tsx \
  src/features/rooms/contexts/MeetingLifecycleProvider.test.tsx \
  src/features/meetingHistory \
  src/features/home/components/CreateMeetingMenu.test.tsx \
  src/features/rooms/livekit/components/controls/EndMeetingButton.test.tsx
```

- Non exécutés : suites backend Django, agent ASR, Platform, build/lint globaux et scénarios navigateur/réunions réelles. Aucun service PostgreSQL/Redis/S3 de qualification ni déploiement staging n'a été établi dans ce worktree pour cet audit documentaire.
- Les succès ci-dessus sont une référence locale, pas une certification de non-régression audio/ASR réelle ni une preuve d'inutilisation des chemins conditionnels.

## Décision proposée

Commencer par les suppressions frontend D01–D04, puis les décisions d'affichage et extractions cohésives de la première vague. D05 est un reliquat démontré, mais reste dans une PR live isolée avec les preuves adaptées. Conserver les consentements et les pipelines tant que leur remplacement n'est pas observé de bout en bout. Ne pas lancer une réécriture globale ou transformer le plan par piste existant en autorisation implicite de bascule.

**Mise à jour du plan le 3 octobre 2026.** Une reprise avait été autorisée,
puis une correction de processus a rétabli la limite « audit et plan
uniquement ». Toutes les PR ci-dessous restent des propositions, sans
autorisation courante de les implémenter. Les résultats de la section 9
restent ceux de l'audit initial.

## 10. Axe transversal : extinction de tous les flags temporaires

Objectif : aucun flag de rollout temporaire dans Meet. Pour chaque domaine,
établir le comportement normal, le conserver directement et retirer ensemble
la branche alternative, le flag, ses variables d'environnement, sa projection
frontend, ses tests de bascule et le code devenu mort. Conserver les tests du
comportement normal et des refus métier ; un test d'autorisation n'est pas un
test de flag jetable.

Une politique d'autorisation, consentement, admission, isolation ou topologie
ne doit pas disparaître avec un flag. Réutiliser sa configuration métier
durable existante. Seulement si elle manque réellement, remplacer le contrôle
par une configuration décrivant ce besoin actuel, sans alias de transition,
sans nouveau chemin et sans simplement renommer `enabled` en `policy`.

La cible indiquée ci-dessous est une décision de simplification proposée,
pas une affirmation sur les cohortes PostHog ou la configuration staging.
Chaque ligne non prouvée reste **INCERTAINE** pour sa suppression, même si son
lecteur est **ENCORE UTILISÉ**. Aucun flag encore présent n'est déclaré
manifestement obsolète et supprimable par ce premier lot.

**Priorité de la consigne validée : ne modifier ni le live ni la
post-transcription sans régression reproduite.** La PR 08 (D05), les PR 11 et
12 si elles touchent ces comportements, les gates d'enregistrement/ASR et les
extractions d'activation susceptibles d'affecter leur production sont
suspendus à cette condition, en plus de leurs preuves précédentes. Le helper
live mort D05 est donc inventorié mais n'est pas supprimé dans le premier lot.
La validation staging déclarée ne constitue pas une régression reproduite.

### Matrice de retrait

Les identifiants `FF-*` désignent de futures PR distinctes vers
`develop-meet-simplification`, pas un lot monolithique. Un identifiant par
flag permet de les décider et de les annuler indépendamment ; les contrôles
qui forment une seule frontière indivisible doivent être traités ensemble
seulement après démonstration de cette nécessité.

| Flag / sélecteur | Comportement normal retenu ou à établir | Branche à supprimer après preuve | Politique durable éventuelle | Tests / preuve préalable | PR / état |
| --- | --- | --- | --- | --- | --- |
| `MASTRAO_MEETING_RECORDING_ENABLED` | Enregistrement gouverné par la décision canonique de réunion | Court-circuit du rollout local | Consentement, droit hôte et décision Core existants | Consentements, activation, refus, artefact final ; régression reproduite obligatoire | FF-01, suspendue |
| `MASTRAO_MEETING_RECORDING_START_ENABLED` | Démarrer seulement un enregistrement autorisé | Interdiction de rollout des nouveaux démarrages | Admission Core existante, distincte de l'accès aux artefacts | Start/stop, double activation, fermeture ; régression reproduite | FF-02, suspendue |
| `MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED` | Accès aux artefacts autorisés encore retenus | Masquage de rollout des téléchargements | Autorisation, rétention, isolation de l'organisation | Download/access, refus interorganisation et artefact historique | FF-03, à prouver |
| `MASTRAO_NATIVE_CAPTURE_START_ENABLED` | Capture native uniquement si le pipeline normal déployé l'exige | Lane native désactivée ou pipeline obsolète, à déterminer | Autorité sur la piste et consentement | Migration 0050, capture réelle, manifest ; régression reproduite | FF-04, suspendue |
| `MASTRAO_NATIVE_PREENTRY_ENABLED` | Préentrée selon le consentement réellement exigé | Choix local entre anciens parcours | Décision canonique et grants existants | Hôte/invité/refus/arrivée tardive ; régression reproduite | FF-05, suspendue |
| `MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED` | Transfert unique des sources autorisées retenues | Lane concurrente désactivée | Rétention, claims et intégrité existants | Receipt perdu, retry, manifest ; régression reproduite | FF-06, suspendue |
| `MASTRAO_NATIVE_ASR_ENABLED` | ASR admis pour la source canonique | Court-circuit de rollout ASR | Budget/admission/provider autorisé dans Core | Résultat réel et idempotence ; régression reproduite | FF-07, suspendue |
| `ROOM_SUBTITLE_ENABLED` | Live multilocuteur validé, accessible aux participants autorisés | Désactivation globale de rollout | Contrôle ON/OFF utilisateur, droits et fermeture existants | Provider/reducer/backend, réunion 2–3 personnes ; régression reproduite | FF-08, suspendue |
| `transcription-summary` (PostHog) | Accès UI selon le mode et les droits effectifs | Cohorte distante et bypass quand analytics absent | Capacités serveur et rôle existants | UI avec/sans analytics, hôte/invité ; régression reproduite | FF-09, suspendue |
| `summary-enabled` (PostHog backend) | Synthèse selon la décision produit canonique | Décision automatique pilotée par cohorte | Demande métier et autorisation Core | Notification, synthèse/historique ; régression reproduite | FF-10, suspendue |
| `screen-recording` (PostHog) | Accès à l'enregistrement selon autorité serveur | Cohorte distante et bypass analytics | Rôle et capacité enregistrement existants | Permissions UI/API, consentement ; régression reproduite si pipeline touché | FF-11, à prouver |
| `metadata-collector` (PostHog) | Collecte nécessaire au pipeline retenu seulement | Cohorte de collecte | Finalité/rétention actuelles | Speaker evidence, absence de collecte interdite ; régression reproduite | FF-12, suspendue |
| `METADATA_COLLECTOR_ENABLED` | Même collecte normale que FF-12 | Dispatch désactivé ou collecteur remplacé, selon preuve | Finalité et rétention | Egress et attribution réelle ; régression reproduite | FF-13, suspendue |
| `METADATA_COLLECTOR_ENABLE_VAD` | VAD si requis par le pipeline validé | Variante VAD/non-VAD obsolète | Aucune nouvelle politique | Chevauchement et locuteurs réels ; régression reproduite | FF-14, suspendue |
| `face-landmarks` (PostHog) | Effets explicitement choisis par l'utilisateur sur matériel supporté ; exposition normale à confirmer | Cohorte distante et bypass analytics | Support matériel et choix utilisateur existants | Effets OFF/ON, rendu, charge, mobile, absence de capture supplémentaire | FF-15, à prouver |
| `noise-reduction` (PostHog) | Réduction disponible sur matériel supporté, choisie par l'utilisateur | Cohorte distante et bypass analytics | Capacité matérielle et préférence existantes | Micro/bascule/latence/CPU et reconnexion, desktop/mobile | FF-16, à prouver |
| `candidate-polling` (PostHog) | Télémétrie de connexion strictement nécessaire ; déterminer si polling doit être conservé | Cohorte et éventuellement polling complet | Consentement télémétrie existant ; ne pas envoyer plus de données | Analytics désactivé, mobile, charge et erreurs `getStats` | FF-17, à prouver |
| `RECORDING_ENABLE` | API d'enregistrement conforme aux droits et au produit supporté | Gate générique de rollout | Autorisation de l'organisation et du rôle, si applicable | API start/stop, permissions et UI ; régression si transcription affectée | FF-18, à prouver |
| `RECORDING_STORAGE_EVENT_ENABLE` | Réception des événements du stockage configuré | Surface désactivée sans besoin de topologie | Topologie des événements ; signature/auth conservées | Événement valide, faux token, replay, achèvement | FF-19, à prouver |
| `RECORDING_ENCODING_ENABLED` | Un profil d'encodage qualifié pour l'enregistrement produit | Bascule preset/personnalisé | Paramètres d'encodage actuels seulement | Image Egress, audio/vidéo, taille/charge ; régression si post affectée | FF-20, à prouver |
| `FILE_UPLOAD_ENABLED` | Fonds personnels disponibles selon droits et quotas du produit | Activation globale temporaire | Restrictions taille/MIME/quotas/permissions existantes | Files API et fond personnel réel | FF-21, à prouver |
| `CONNECTION_TEST_ENABLED` | Diagnostic proposé si composant du produit normal | Masquage temporaire de l'outil | Isolation, durée de vie et quotas des salles de diagnostic | Diagnostics API, nettoyage et absence d'effet sur une réunion | FF-22, à prouver |
| `EXTERNAL_API_ENABLED` | Exposer uniquement les API contractuellement supportées | Gate temporaire ou surface non supportée, après inventaire clients | Topologie et authentification/scopes existants | Routes et contrats clients externes | FF-23, à qualifier |
| `APPLICATION_ENABLED` | Applications autorisées selon politique d'intégration | Gate temporaire | Autorisation/scopes existants | API application, jetons et refus | FF-24, à qualifier |
| `ADDONS_ENABLED` | Addons supportés et authentifiés | Gate temporaire ou produit abandonné prouvé | Politique d'intégrations si besoin actuel | Session addon/expiration/révocation | FF-25, à qualifier |
| `ROOMKIT_ENABLED` | RoomKit seulement si équipement supporté | Gate de rollout ou surface abandonnée | Admission serveur-à-serveur/topologie | Join RoomKit, jeton invalide, portée salle | FF-26, à qualifier |
| `ROOM_TELEPHONY_ENABLED` | Téléphonie selon topologie réellement disponible | Booléen redondant avec une topologie établie | Trunk/routage SIP actuels | SIP join/leave, absence de trunk, permissions | FF-27, à qualifier |
| `LIVEKIT_FORCE_WSS_PROTOCOL` | URL de signalisation unique qualifiée | Conversion conditionnelle | URL LiveKit canonique existante | Navigateurs supportés, proxy et reconnexion | FF-28, à prouver |
| `LIVEKIT_ENABLE_FIREFOX_PROXY_WORKAROUND` | Connexion Firefox normale sur la matrice supportée | Amorçage WebSocket si désormais inutile | Aucune nouvelle compatibilité | Reproduction avec/sans proxy et traces de connexion | FF-29, à prouver |
| `LIVEKIT_EXPLICIT_ROOM_CREATION` | Création unique avec les invariants de salle requis | Création implicite concurrente si non supportée | Cycle de vie et autorité actuels | Création/idempotence/fermeture et refus | FF-30, à qualifier |
| `CELERY_ENABLED` | Tâches produit sur queues/workers déclarés ; mode test explicitement isolé | Fallback synchrone si plus aucun environnement supporté n'en dépend | Topologie de workers ; mécanisme de test existant | Webhooks non bloquants, queues/retry ; régression avant touchers transcription | FF-31, à qualifier |
| `SIGNUP_NEW_USER_TO_MARKETING_EMAIL` | Inscription marketing seulement si autorisée selon le produit | Bascule technique si elle double une vraie politique | Consentement marketing, ne jamais activer implicitement | Signup sans envoi réel et décision consentement | FF-32, politique à établir |
| `FRONTEND_IS_SILENT_LOGIN_ENABLED` | Session normale selon contrat OIDC et invitation | Variante silencieuse obsolète éventuelle | Parcours auth/invité et choix explicite existants | Session expirée, invité, compte existant, logout | FF-33, à prouver |
| `FRONTEND_USE_FRENCH_GOV_FOOTER` | Identité visuelle Mastrao du produit | Footer institutionnel si hors périmètre supporté | Branding durable seulement si plusieurs produits actuels | Configuration effective, footer et liens légaux | FF-34, à prouver |
| `FRONTEND_USE_PROCONNECT_BUTTON` | Fournisseur d'identité réellement supporté | Variante de bouton obsolète | Identité du fournisseur OIDC si besoin actuel | Connexion/déconnexion et libellé accessible | FF-35, à prouver |
| `STT_PROVIDER`, `ROOM_SUBTITLE_PROVIDER`, `ENABLE_SILERO_VAD` | Provider et VAD du live validé | Variantes obsolètes uniquement si justifiées | Profil fournisseur actuel ; aucune nouvelle abstraction | Contrat audio/transport et réunion réelle ; régression reproduite | FF-36, suspendue ; scinder par variante prouvée |
| `MASTRAO_ASR_QUALIFICATION_MODE`, `SUMMARY_SERVICE_VERSION` | Qualification isolée ; service summary réellement utilisé | Qualification en prod / client v1 obsolète, selon preuve | Environnement de qualification et contrat service actuels | Jobs en attente, contrat v2, résultat réel ; régression reproduite | FF-37, suspendue ; deux PR si nécessaires |
| `MASTRAO_MEETING_TRANSCRIPTION_ENABLED` (ancien) | Post-transcription déjà intégrée au lifecycle gouverné | Déjà supprimée, aucune réintroduction | Admission/ASR/Celery existants | Absence par `git grep`, changelog du 2 octobre | Terminé avant ce chantier ; aucun nouveau diff |

### Contrôles durables exclus du retrait aveugle

L'inventaire couvre aussi les booléens qui ne représentent pas un rollout :
`ALLOW_UNSECURE_USER_LISTING`, `FILE_UPLOAD_APPLY_RESTRICTIONS`,
`EXTERNAL_API_ALLOW_PUBLIC_ACCESS`, `APPLICATION_ALLOW_USER_CREATION`,
`ALLOW_UNREGISTERED_ROOMS`,
`AUTHENTICATED_PARTICIPANTS_CAN_EDIT_DISPLAY_NAME`,
`RECORDING_ENABLE_STORAGE_EVENT_AUTH`, `LIVEKIT_VERIFY_SSL`,
`EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `CORS_ALLOW_ALL_ORIGINS`, tous les réglages
OIDC de création/identification/PKCE/nonce/TLS/tokens/redirect et
`ALLOW_LOGOUT_GET_METHOD`. Ce sont des frontières de sécurité ou des politiques
actuelles à examiner individuellement ; pas de nouvelle configuration par
principe et pas de suppression dans ce lot.

`CELERY_TASK_ALWAYS_EAGER` est un mode d'exécution de test ;
`SPECTACULAR_SETTINGS_ENABLE_DJANGO_DEPLOY_CHECK`, `USE_SWAGGER`,
`FRONTEND_SILENCE_LIVEKIT_DEBUG`, `VITE_ANALYZE`, `VITE_BUILD_SOURCEMAP`
sont des options d'outillage/observabilité. Les préférences accessibles,
caméra/micro, effets, notifications et ON/OFF sous-titres sont des choix
utilisateur. Aucun de ces contrôles n'est à convertir en flag de remplacement.

### Premier lot sûr et procédure de revue

PR 01 : supprimer D01–D04 seulement (deux fichiers inutilisés, une fonction et
un export d'icône avec son import devenu inutile). Les fichiers de transcription,
les flags, le backend et les dépendances restent intacts. Refaire les recherches
de consommateurs, comparer un build avant/après, lancer les tests frontend et
les contrôles de format/lint applicables. La preuve adaptée à cette suppression
est l'absence de consommateurs et l'équivalence des artefacts exécutables si
elle est obtenue ; une capture de preview ne serait pas une preuve de visio.
Ne pas prétendre à une réunion staging exécutée durant ce lot.

Lancer `review-fix-loop` uniquement après création de la PR :

- **PROVEN** : reproduction ou trace précise montrant le défaut du diff ; corriger.
- **PROBABLE** : mécanisme démontré par le code et impact élevé sur un parcours
  réel, sans reproduction complète ; vérifier puis corriger uniquement ce défaut.
- **SPECULATIVE** : variation hypothétique, préférence ou nouvelle architecture ;
  documenter le rejet, sans élargir le diff.

Ne fusionner aucune PR dans `develop`. Attendre la validation explicite de
Matthias avant tout code, commit, publication ou revue de PR. Les lots
conditionnels restent ensuite soumis à leurs preuves propres.

## 11. Restitution décisionnelle : risque et volume des lots proposés

Le volume ci-dessous est une estimation de fichiers de production à toucher,
hors ajustements ciblés de tests/docs/configuration. Ce n'est ni un engagement
de suppression ni un objectif de lignes. « Déplacer » ne signifie pas
« supprimer » : une extraction doit réduire les responsabilités à parcourir,
et non multiplier les couches. Les validations détaillées des sections 6, 8
et 10 s'appliquent avant/après chaque lot.

| Ordre / lot | Décision proposée | Volume estimé | Risque | Validation qui conditionne le lot |
| --- | --- | --- | --- | --- |
| 01 — D01–D04 | Autoriser en premier le retrait des exports/utilitaires sans consommateur | 4 fichiers, 46 lignes supprimées, dont 2 fichiers entiers ; pas de logique ajoutée | Faible | Recherche imports/réexports/dynamique, build avant/après, lint/format ciblés, tests frontend. Équivalence des artefacts à établir, non encore vérifiée. |
| 02 — Invitation | Un rendu nommé des états, sans changement du redeem | 1–2 fichiers, environ 30–70 lignes réorganisées | Faible à modéré | Absence de lien, chargement, 404, erreur temporaire/retry et succès ; parcours invité réel. |
| 03 — Parsing historique | Remplacer les ternaires, conserver tous les contrats | 1 fichier, environ 30–80 lignes réorganisées | Modéré | Projections partielles, réponses obsolètes, atomicité revision/digest et source synthèse. Suspendre si changement du comportement post-transcription nécessaire. |
| 04 — Présentation historique | Extraire une seule section de présentation | 2–3 fichiers, environ 100–220 lignes déplacées | Modéré | Ordre des tours, noms, timestamps, contenu partiel, clavier ; aucune modification du producteur ni du parsing métier. |
| 05 — Fonds personnels | Séparer upload/suppression/sélection du reste des effets | 2–4 fichiers, environ 150–300 lignes déplacées | Modéré | Upload/quotas/MIME/refus et caméra pendant la réunion, sans recréer de piste/Room. |
| 06 — FileViewSet | Séparer uniquement la responsabilité fichiers | 2–4 fichiers, environ 350 lignes déplacées | Modéré | Tests files/permissions/OpenAPI, migrations inchangées, essai upload/accès interutilisateur. |
| 07 — Activation | Ne pas commencer en l'absence de nécessité démontrée | 2–3 fichiers, environ 80–130 lignes déplacées | Élevé | Régression reproduite si affecte transcription ; idempotence, média publié, retry, fermeture et réunion réelle. |
| 08 — D05 live | Conserver ce reliquat inventorié tant que la règle de non-intervention s'applique | 3–4 fichiers, environ 25–50 lignes de production supprimables | Faible impact direct, domaine sensible | Régression reproduite exigée avant intervention ; provider/contrat/reducer et live réel multilocuteur. |
| 09 — Marketing | Simplifier seulement après inventaire des overrides | 2–3 fichiers, environ 25–45 lignes supprimables | Modéré | Brevo seul réellement configuré, cache/erreurs préservés, signup sans envoi réel. |
| 10 — URL historique | Retirer seulement l'ancien nom de configuration | 2 fichiers, environ 10–20 lignes supprimables | Modéré | Inventaire environnements et lien de téléchargement réel, autorisation conservée. |
| 11 — Summary v1 | Différer tant que contrat externe et règle transcription bloquent | 2–3 fichiers, environ 70–110 lignes supprimables | Élevé | Régression reproduite, contrat v2 déployé, jobs antérieurs et synthèse réelle. |
| 12 — Projections anciennes | Différer jusqu'à preuve de tous les producteurs/consommateurs | 1–3 fichiers, environ 20–70 lignes supprimables | Élevé | Anciennes réunions, liste/détail/synthèse, digest/révision ; pas de retrait d'un contrat encore consommé. |

### Risque et volume de la matrice de flags

Les regroupements suivants évitent de répéter une même estimation ; ils
**n'autorisent pas une PR groupée**. Le volume est indiqué **par identifiant
FF**, sauf mention contraire. Les comportements normaux, branches à supprimer,
politiques durables et tests propres à chaque flag figurent dans la matrice
précédente. Aucun retrait n'est décidé à partir de cette seule estimation.

| Lots de la matrice | Risque estimé | Volume estimé par PR | Preuve déterminante |
| --- | --- | --- | --- |
| FF-01, FF-02 | Élevé | 3–7 fichiers de code/configuration chacun | Consentement/admission et régression reproduite avant toucher au pipeline. |
| FF-03 | Élevé | 2–5 fichiers | Accès historique, organisation, rétention et configuration réelle. |
| FF-04, FF-05, FF-06, FF-07 | Très élevé | 3–8 fichiers chacun ; retrait d'un pipeline complet hors de cette estimation | Migration/état persistant, décision canonique, captures réelles et régression reproduite. |
| FF-08, FF-09, FF-10 | Élevé | 2–6 fichiers chacun | Live/synthèse validés, droits et régression reproduite. |
| FF-11 | Élevé | 2–4 fichiers | Capacité serveur/consentement ; régression reproduite si transcription concernée. |
| FF-12, FF-13, FF-14 | Élevé | 2–6 fichiers chacun | Nécessité réelle de collecte/VAD, attribution et régression reproduite. |
| FF-15 | Modéré | 2–4 fichiers | Exposition produit des effets, support matériel et charge. |
| FF-16 | Élevé pour la qualité audio | 2–4 fichiers | Qualité audio/latence/CPU, préférences et support matériel. |
| FF-17 | Modéré à élevé | 2–4 fichiers | Nécessité du polling, données collectées et consentement télémétrie. |
| FF-18 | Élevé | 3–6 fichiers | Frontière d'autorisation, salle générique/Mastrao et consentement. |
| FF-19 | Élevé | 2–5 fichiers | Topologie événements, authentification, replay et finalisation. |
| FF-20 | Élevé | 3–5 fichiers | Encodage réellement qualifié, lecture des artefacts et charge Egress. |
| FF-21 | Modéré | 3–5 fichiers | Quotas, droits et fonds personnels, y compris refus. |
| FF-22 | Modéré | 3–5 fichiers | Diagnostic isolé, expiration/cleanup et absence d'effet sur les salles produit. |
| FF-23, FF-24, FF-25, FF-26 | Élevé | 3–7 fichiers chacun ; suppression d'un produit hors de cette estimation | Consommateurs externes et politique d'intégrations explicites. |
| FF-27 | Élevé | 3–6 fichiers | Téléphonie réelle et topologie SIP. |
| FF-28, FF-29 | Élevé | 2–4 fichiers chacun | Matrice navigateurs/proxy, connexion et reconnexion réelles. |
| FF-30 | Élevé | 3–6 fichiers | Création unique, lifecycle et refus, aucun rattrapage par salle alternative. |
| FF-31 | Très élevé | Volume à chiffrer après inventaire des tâches ; découpage requis si trop large | Topologies dev/test/runtime et toutes les tâches sans fallback synchrone. |
| FF-32 | Élevé pour la politique utilisateur | 2–4 fichiers | Politique marketing/consentement ; aucun envoi implicite. |
| FF-33 | Élevé pour l'authentification | 2–5 fichiers | OIDC, invité, expiration, déconnexion et retour. |
| FF-34 | Faible à modéré | 2–5 fichiers | Identité produit et liens légaux effectifs. |
| FF-35 | Modéré à élevé | 2–5 fichiers | Fournisseur d'identité supporté et connexion réelle. |
| FF-36 | Très élevé | 3–8 fichiers par variante prouvée, à scinder ; pas de bascule groupée | Provider live/VAD, régression reproduite et preuve audio multilocuteur. |
| FF-37 | Élevé | 2–5 fichiers par sujet ; qualification et version summary séparées | Isolation de qualification, contrat de service et régression reproduite. |

Ordre de décision recommandé : valider d'abord 01, puis 02 et les extractions
de présentation réellement utiles (03–06), une par une. En parallèle de la
planification, obtenir les preuves pour FF-15/17/21/22/34, sans présumer que
leur activation est le comportement produit normal. Les autres suppressions
restent différées tant que leurs dépendances et contrats ne sont pas établis.
Tous les lots touchant la transcription restent soumis à la reproduction
d'une régression ; aucun audit de structure ne lève cette contrainte.

## 12. État exact après la correction de processus

Avant réception de la consigne d'arrêt, les changements suivants avaient été
effectués uniquement dans le worktree isolé :

- suppression de `useFocusToggleParticipant.ts` (32 lignes) ;
- suppression de `getParticipantIsAuthenticated.ts` (8 lignes) ;
- suppression de `isChromiumBased` dans `utils/livekit.ts` (4 lignes) ;
- suppression de `CaptionsIcon` et de son import Heroicons (2 lignes) ;
- ajout de trois lignes dans `CHANGELOG.md`.

Ces **cinq fichiers ont tous été restaurés exactement depuis HEAD** à la
réception de la correction. `git diff --exit-code` est vide ; `git status`
ne montre que le présent plan non suivi. Les fichiers mis à la Corbeille n'ont
pas été vidés. Aucune suppression ne subsiste dans le dépôt.

La branche active est de nouveau `develop-meet-simplification`, au SHA
`87582bcea283d41abec857d9613d8adfa6a3258e`. La branche locale de travail
`codex/meet-simplification-dead-frontend` existe au même SHA, sans commit
propre ; elle n'a pas été poussée. Aucun commit, push, PR, revue de PR,
déploiement ou merge n'a eu lieu. Le checkout initial sale sur `main` n'a
pas été modifié.

Preuves additionnelles obtenues **avant** la suppression interrompue :

- build frontend de référence réussi avec Node 26.0.0 ;
- suite frontend complète : **49 fichiers, 245 tests réussis** ;
- empreintes SHA-256 de **194 fichiers** du build de référence conservées
  dans `/tmp/meet-simplification-baseline-dist.json` ; comparaison après
  modification **non effectuée** puisque l'implémentation a été arrêtée ;
- Node 22 installé localement ne démarre pas (bibliothèque `libsimdjson.29`
  manquante) ; aucun correctif de l'environnement système entrepris.

Les dépendances, fichiers générés ignorés et logs temporaires de référence
restent locaux. Les 62 tests de contrats de l'audit restent réussis ; aucune
réunion staging ni qualification ASR réelle n'a été exécutée dans ce chantier.

**Cette pause historique est levée uniquement pour la vague définie ci-dessous.**


## 13. Décision validée : documentation puis lots 01–04 uniquement

Cette décision remplace les pauses antérieures et restreint la feuille de route.
Les autres lots et la matrice FF restent un inventaire, sans autorisation de code.

1. **PR documentation** : versionner l'audit et cette décision, sans code produit.
2. **Lot 01 — D01–D04** : supprimer seulement `useFocusToggleParticipant.ts`,
   `getParticipantIsAuthenticated.ts`, `isChromiumBased`, `CaptionsIcon` et
   son import devenu inutile. Répéter la preuve d'absence de consommateurs ;
   diff-check, typecheck/build, lint ciblé et tests frontend concernés.
   Aucune réunion humaine ou staging n'est exigée pour ce code mort prouvé.
3. **Lot 02 — GuestInvitation** : expliciter le rendu avec une fonction nommée
   ou un switch ; retirer les ternaires/conditions répétées sans changer
   `redeemGuestInvitation`, le réseau ou les états métier. Tests composant :
   invitation absente, chargement, refus terminal, erreur temporaire, succès.
4. **Lot 03 — meetingHistoryApi** : remplacer seulement les conditions et
   ternaires imbriqués de normalisation/fusion par des fonctions nommées.
   Mêmes entrées → mêmes sorties, prouvées par les tests existants et ciblés.
   Aucun nouveau format, fallback ou chemin de compatibilité.
5. **Lot 04 — MeetingHistoryDetailView** : extraire uniquement la présentation
   de la transcription historique dans un composant feature-local cohésif.
   Conserver parsing, groupement, données, comportement et primitives actuels.
   Vérifier flux historique, noms, ordre, timestamps, contenu partiel et navigation.

Pour chaque PR : partir de la dernière tête distante de
`develop-meet-simplification`, appliquer `clean-code`, mesurer les branches,
duplications ou responsabilités retirées. Après création seulement, appliquer
`review-fix-loop` sur le diff complet au SHA exact, avec revue indépendante
Standards/Spec et verdict style distinct. Corriger les findings PROVEN et les
PROBABLE à fort impact, pas les SPECULATIVE (HYPOTHETICAL dans le skill).
Les tests et la CI doivent être verts et aucun finding bloquant non résolu ne
doit rester avant une fusion. Après fusion autorisée dans l'intégration,
actualiser la référence distante et repartir de cette nouvelle tête.

Le changelog peut recevoir la courte entrée requise par CI pour chaque lot.
Aucun changement de CI, dépendance ou architecture n'est autorisé pour rendre
un lot artificiellement vert. Si une correction nécessaire sort du périmètre,
s'arrêter et exposer la preuve, sans passer au lot suivant.

Interdictions : D05, effets vidéo, FileViewSet, activation d'enregistrement,
flags, live, pipelines post-transcription, consentement, capture, ASR et
production de résumé. Les lots 03–04 touchent uniquement le code d'affichage
et d'adaptation déjà désigné, jamais les producteurs. Aucun lot 05–12, aucune
fusion de l'intégration vers `develop`, aucun déploiement staging.

Arrêt et restitution cumulative après le lot 04, ou dès qu'une modification
nécessaire dépasse cette autorisation. La suppression d'une compatibilité de
la matrice FF ne peut pas être déduite de l'autorisation de cette vague.
