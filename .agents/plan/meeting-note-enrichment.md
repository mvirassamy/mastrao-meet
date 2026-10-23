# Enrichissement manuel des notes personnelles après réunion

Conception : 22 octobre 2026. Exécution APEX locale autorisée les 22–23 octobre 2026 ; voir le suivi en fin de document. Aucun push, merge ni changement de staging.

## 1. Objectif et décisions

Depuis « Mes notes » dans le détail d’une réunion terminée, permettre à l’auteur de demander des compléments fondés sur la transcription, de les relire et de les ajouter explicitement. Le texte personnel d’origine reste intact et le dernier ajout peut être annulé sous conditions.

### Décisions déjà validées par l’utilisateur

- Déclenchement manuel par un bouton ; aucun enrichissement automatique.
- Préservation du texte original.
- V2 de l’assistant avec Mastra reportée ; aucun chantier Mastra pour cette fonctionnalité.
- Réglages avancés de visibilité et de partage reportés.
- Travail exclusivement local ou sur branches isolées. **Aucun merge, push, déploiement ou changement de `develop` ou de staging dans ce chantier de planification.**

### Recommandations de ce plan, à distinguer des décisions déjà validées

- Première version dans l’historique, après l’appel, pour les notes persistantes d’un compte authentifié.
- Proposer **des ajouts seulement**, modifiables dans une prévisualisation, puis les ajouter sous les notes. Ne pas réécrire l’original.
- Une seule proposition en cours et une annulation du dernier enrichissement accepté ; aucun historique général.
- Afficher les passages justificatifs et distinguer une proposition retouchée par l’utilisateur. Ces éléments de relecture sont des recommandations de conception, pas des exigences déjà validées séparément.
- Utiliser la transcription de cette réunion et les seules notes de leur auteur. Ne pas incorporer le dossier, les documents, les autres réunions ou les conversations des assistants.
- Les notes temporaires des invités restent inchangées et hors de ce parcours IA.

## 2. Options étudiées et compromis

| Option | Bénéfice | Coût ou risque | Choix proposé |
| --- | --- | --- | --- |
| Compléments séparés, prévisualisation puis ajout | L’utilisateur vérifie seulement les nouveautés ; original inchangé | Peut produire un bloc supplémentaire moins bien intégré ; doublons à éviter | **V1 recommandée** |
| Réécriture complète avec original conservé | Meilleure structure et meilleure fluidité | Relecture de tout le document ; risque de modifier une intention personnelle | Alternative si la restructuration devient le besoin principal |
| Suggestions ancrées dans chaque passage, acceptées individuellement | Contrôle précis et meilleure intégration | Gestion des ancrages, modifications concurrentes et acceptations multiples | À différer jusqu’à un besoin observé |

La meilleure objection à la V1 est qu’un ajout en bas de page n’améliore pas la structure des notes. Elle est valable : cette version privilégie la complémentation fiable. Si les premiers essais montrent surtout un besoin de reformulation, réexaminer la deuxième option avant d’élargir l’éditeur.

| Regard | Conséquence pour le plan |
| --- | --- |
| Pragmatique | Réutiliser l’éditeur, l’autosave et le transport LLM actuels. |
| Qualité | Protéger le texte exact, la provenance et les modifications concurrentes. |
| Sceptique | Une citation valide ne prouve pas que le modèle a correctement interprété la phrase. |
| Architecture | Une opération privée dédiée ; aucun élargissement du contexte des assistants existants. |
| Utilisateur débutant | Montrer ce qui sera ajouté, où, et quand cela est effectivement enregistré. |

Confiance : élevée sur les frontières de confidentialité et l’intégration technique ; moyenne sur la préférence « ajouts » contre « réécriture », à éprouver avec des notes réalistes.

## 3. Parcours et textes d’interface

1. Dans l’onglet **Mes notes**, conserver le champ et l’état de sauvegarde actuels. Ajouter « **Enrichir mes notes** » et une explication : « Propose des compléments à partir de la transcription. Vos notes restent privées. »
2. Au clic, terminer la sauvegarde en cours. En cas d’échec, rester sur les notes avec l’erreur de sauvegarde ; ne pas appeler le modèle.
3. Afficher « Préparation des compléments… » et « Annuler ». Les notes restent consultables et modifiables. Une modification rend la proposition issue de l’ancienne version inapplicable.
4. Présenter uniquement les ajouts dans une zone de texte distincte : « Proposition — vérifiez avant d’ajouter ». L’original reste visible. Afficher à côté les passages justificatifs de la transcription.
5. Actions : « **Ajouter les compléments** » et « Annuler ». Si l’utilisateur modifie la proposition, signaler « Proposition modifiée par vous » ; les références restent celles de la proposition initiale et ne certifient pas ses modifications.
6. Après confirmation serveur, ajouter exactement `original + "\n\nCompléments de la réunion\n" + ajout_approuvé`. Ne pas normaliser, nettoyer ou reformater le préfixe original. Le libellé est localisé avec les textes de l’interface.
7. Afficher « Compléments ajoutés » et « Annuler le dernier enrichissement ». La confirmation ne précède jamais la persistance.

Pas de complément utile : « Aucun complément proposé à partir des passages analysés. » Aucune écriture des notes, aucun bloc vide, aucune incrémentation de leur révision.

Proposition périmée : « Vos notes ou la transcription ont changé. Relancez l’enrichissement pour utiliser la dernière version. » Aucune fusion automatique.

L’annulation du dernier ajout restaure le texte sauvegardé avant cet ajout, uniquement si aucune édition locale ou serveur ne l’a suivi. Sinon : « Vos notes ont changé depuis cet ajout ; l’annulation automatique n’est plus disponible. » Ne jamais écraser ces nouvelles modifications.

## 4. Éligibilité et contexte envoyé au modèle

Le serveur décide de l’éligibilité ; masquer un bouton ne constitue pas un contrôle d’accès.

- Réunion effectivement terminée, note persistée et non vide hors espaces, auteur acteur autorisé par les règles actuelles.
- L’acteur possède cette note et dispose du droit courant de lire la transcription. Un droit de lecture sur le dossier ou un rôle administrateur ne donne pas accès aux notes d’autrui.
- Ne pas étendre les droits actuels aux autres participants. Le contrat signé inspecté lie notamment l’accès auteur au créateur de la réunion ; une extension de cette règle serait un autre sujet.
- Être connecté dans le navigateur ne suffit pas : une note peut relever d’un grant invité. Les documents invités sont refusés côté Core pour cette opération.
- Au moins une source de transcription autorisée, non vide, de version identifiable et disponible après l’appel. Refuser l’absence de passages utilisables ou un traitement encore en cours/une actualisation attendue ; la synthèse n’est pas un prérequis. Le statut produit `available` est le cas nominal, sans exiger une couverture exhaustive de l’audio.
- Distinguer une transcription canonique incomplète d’un affichage paginé ou tronqué : l’éligibilité finale dépend du lecteur Core, pas du seul tableau de segments du navigateur.

**Entrée du modèle :** texte de la note de cet auteur, passages autorisés de cette seule transcription, identifiants de citations, couverture, langue et version du profil. Aucun autre contexte. Lire le contenu côté Core ; ne pas accepter un document original ou une transcription arbitraires fournis par le navigateur.

Pour les longues réunions, réutiliser une sélection bornée de passages guidée par les notes. Si seuls certains passages sont envoyés, afficher « Compléments proposés à partir de passages sélectionnés » ; ne jamais revendiquer une couverture exhaustive. Le manifeste conserve la version des sources et les segments effectivement envoyés.

**Cas natif indispensable :** le contrat actuel conserve `coverage: partial_sources` et des groupes temporels indépendants, même après stabilisation. Accepter ces sources lorsqu’elles sont disponibles et que le traitement n’est plus en cours, avec l’avertissement « Sources partielles : les compléments portent uniquement sur les passages disponibles ». Distinguer cette limite intrinsèque d’une sélection réduite pour le modèle ; conserver les deux dans le manifeste. Ne pas fabriquer une chronologie globale à partir des temps relatifs de plusieurs groupes. La source partielle, à elle seule, ne désactive donc pas le bouton.

Les notes complètes doivent tenir dans le budget d’entrée avec cette sélection. Si elles ne tiennent pas, refuser clairement avant l’appel ; ne pas tronquer silencieusement les notes. Aucun nouveau moteur RAG ou traitement distribué pour ce cas.

### Règles de génération

- Produire seulement des informations utiles absentes des notes ; ne pas recopier les blocs déjà présents, y compris les enrichissements précédents.
- Préserver les nuances, négations, incertitudes et contradictions. Une proposition ne devient pas une décision.
- Ne pas inventer responsable, échéance, montant ou identité à partir d’un simple libellé de locuteur.
- Chaque ajout factuel doit s’appuyer sur au moins un segment envoyé. En cas de contradiction avec les notes, la signaler explicitement plutôt que corriger l’original.
- Notes et transcription sont des données, jamais des instructions système. Aucun outil, recherche externe, envoi d’email, publication ou création de tâche.
- Sortie structurée bornée : résultat avec compléments ou sans complément, texte proposé et identifiants de citations. Rejeter les sorties invalides, tronquées ou citant un segment absent.
- La validation des références garantit leur provenance ; la relecture humaine et les cas d’évaluation vérifient la fidélité du sens.

## 5. Socle constaté et architecture proposée

Inspection statique : Meet `b37ec25c28214ebc26a658004a67693caf77b795` ; Platform/Core `1c4379775c3fb276f0631f20d41618b22f78f556`. Il ne s’agit pas d’une vérification de l’état actuel de staging. Revalider ces points sur les sources retenues avant une future implémentation.

| Socle existant | Réutilisation ou changement ciblé |
| --- | --- |
| Meet `MeetingPersonalNotesSection` compose l’éditeur dans l’historique | Y composer les commandes et la prévisualisation d’enrichissement. Ne pas gonfler la vue complète de réunion. |
| `PersonalNotesController` possède autosave, conflits et `flush()` | Exposer un instantané **confirmé** contenu/révision et intégrer la réponse d’application dans sa référence de sauvegarde. Garder un seul propriétaire de l’autosave. |
| `personalNotesApi` lit/écrit avec `expected_revision` | Conserver le contrat courant ; ajouter un client distinct pour l’opération IA et son cycle plus long. |
| Core `meeting_personal_notes` conserve contenu courant et révision | Ajouter une seule table compagnon pour la proposition et la restauration du dernier ajout. Aucun historique des notes aujourd’hui. |
| Assistant : Core → `ModelGatewayClient` → gateway signé → fournisseur configuré | Réutiliser ce transport avec une opération/profil d’enrichissement dédié. Ne pas envoyer les notes dans le chat existant. |
| Synthèse de réunion : autre producteur, appel OpenAI direct depuis Core dans la version inspectée | Ne pas le confondre avec le transport de l’assistant ; ne pas créer un troisième client fournisseur. |

Flux proposé :

`Mes notes → API Meet → commandes signées Core → autorisations + note/source figées → gateway existant → proposition privée → validation utilisateur → application transactionnelle Core → éditeur synchronisé`.

**Design pattern : aucun nouveau pattern GoF choisi.** Un service applicatif dédié, un contrat typé et le transport déjà en place suffisent. Le contrôle optimiste de révision existant et des reçus d’opération traitent la concurrence et les répétitions ; pas de Strategy multi-fournisseur, Factory, Observer, event bus, moteur de workflow ou Mastra ajouté pour anticiper la suite.

### Contrats à ajouter

Définir ensemble les commandes signées et leur façade Meet avant de coder les écrans. Le préfixe public naturel reste `/api/v1.0/meetings/history/{meeting_ref}/personal-note/` ; les noms définitifs des sous-routes sont à aligner sur les conventions du dépôt.

| Opération logique | Entrée utile | Résultat |
| --- | --- | --- |
| Générer | Identifiant idempotent, réunion, révision attendue des notes et version d’état attendue | Proposition identifiée, état, version d’opération, sources et limites de couverture |
| Relire l’état | Réunion, éventuellement identifiant d’opération | Proposition courante et possibilité d’annuler le dernier ajout, sans nouvel appel LLM |
| Appliquer | Proposition, versions attendues, texte des **seuls compléments approuvés**, identifiant idempotent | Reçu avec révision/digest appliqués ; lecture de la note courante pour synchroniser l’éditeur |
| Abandonner | Proposition et version attendue | Proposition abandonnée ; notes intactes |
| Annuler le dernier ajout | Identifiant du dernier ajout, révision attendue, identifiant idempotent | Original restauré avec nouvelle révision |

Core déduit tenant, acteur, note et réunion depuis l’autorité vérifiée. Aucun `owner_id` ou texte original de remplacement provenant du client. Le texte approuvé peut différer de la suggestion : le borner, le marquer comme modifié et ne pas le présenter comme intégralement vérifié par les citations du modèle.

Pour signer l’application, reprendre le principe de `personalNoteRequestDigest` : lier les **octets UTF-8 exacts de l’ajout approuvé** par leur digest à l’enveloppe canonique et au reçu. Ne pas passer cet ajout utilisateur tel quel dans la canonicalisation NFC réservée à l’enveloppe : cela rejetterait notamment certains accents décomposés ou altérerait le texte approuvé.

### Données minimales et rétention

Une table compagnon de la note, avec clé tenant/note, FK, RLS et résolution du propriétaire avant accès. Deux emplacements indépendants et une version d’état pour les transitions :

- **Proposition courante :** identifiants et digest de requête, état/échéance, copie exacte du texte de base en colonne `text`, révision/digest de base, ajout proposé, manifeste borné des sources/citations/couverture, profil et version du prompt.
- **Dernier ajout accepté :** original exact avant application, identifiant de proposition, révision/digest avant et après application, reçus minimaux d’application et d’annulation. Pas de copie complète supplémentaire de la note enrichie.

Générer B ne supprime pas la possibilité d’annuler A. Seule l’application réussie de B remplace le snapshot de A. Annuler B restaure donc les notes qui contenaient A ; pas d’annulation en cascade.

Portée de la garantie : le texte courant reste inchangé lors de chaque ajout et la version **avant le dernier ajout accepté** est récupérable après rechargement. Ce dispositif ne constitue pas un archivage perpétuel de la toute première version face aux éditions manuelles ultérieures.

Au plus une proposition et un snapshot de restauration par note. Remplacement ou abandon supprime le texte de la proposition précédente ; les métadonnées minimales nécessaires aux reçus restent bornées. Le snapshot reste jusqu’au prochain ajout accepté ou à l’effacement explicite des notes. Un effacement volontaire purge aussi les copies de contenu de cette fonctionnalité dans la même transaction. Une suppression de note entraîne celle de sa table compagnon. Aucun historique privé caché ou conservation indéfinie dans les journaux.

Vider entièrement la zone de notes puis confirmer sa sauvegarde, y compris par l’autosave existant, compte comme cet effacement explicite. Invalider alors l’opération courante : une génération déjà engagée ne peut recréer la proposition ou le snapshot purgés à son retour.

Conserver le compteur monotone de version d’état après abandon ou effacement, sans conserver les textes purgés. La génération exige cette version attendue : une ancienne commande remplacée ne peut pas reprendre la place d’une proposition plus récente, même si la révision des notes n’a pas changé.

### Sauvegarde, concurrence et résultats incertains

1. **Avant génération :** `flush()` doit confirmer tout le brouillon courant, y compris les frappes arrivées pendant une écriture. Relire l’état après l’attente. Core fige la version réelle, réserve la proposition et le dispatch durable avant la sortie fournisseur.
2. **Pendant l’appel :** aucune transaction SQL maintenue ouverte. Toute réponse reste une proposition séparée. Changer de réunion, de compte ou abandonner neutralise les réponses tardives ; abandonner ne promet pas d’annuler une dépense fournisseur déjà engagée.
3. **Après génération :** recontrôler autorité, notes et sources avant de proposer une application. Une ancienne réponse n’écrase jamais une proposition plus récente.
4. **Application :** sérialiser avec l’autosave, terminer les écritures locales puis revérifier l’absence de nouvelles frappes. Dans une seule transaction Core, vérifier propriétaire, version d’opération, révision/digest des notes et identité/version/digest des sources, construire l’ajout, vérifier sa taille, écrire sous CAS et enregistrer le snapshot/reçu.
5. **Fraîcheur réelle :** appeler un lecteur qui ouvre sa propre transaction juste avant l’écriture ne suffit pas. Vérifier les sources dans la transaction d’application ; reprendre les mécanismes d’autorité natifs existants là où ils s’appliquent.
6. **Retour dans l’éditeur :** mettre à jour contenu et référence de sauvegarde du contrôleur ensemble, avant reprise de l’édition. Un ancien autosave ne doit pas écraser l’enrichissement.
7. **Annulation du dernier ajout :** aucune frappe locale non sauvegardée et révision/digest serveur identiques au résultat appliqué. Restaurer le snapshot serveur ; incrémenter la révision, sans revenir à l’ancien numéro. La transcription n’a pas à rester disponible pour cette restauration privée.
8. **Répétition :** même identifiant et mêmes arguments encore retenus → résultat déjà enregistré, sans second appel ni second ajout. Identifiant réutilisé avec d’autres arguments ou opération remplacée → conflit explicite. Ne pas annoncer une idempotence illimitée de tous les anciens résultats.
9. **Réponse perdue :** relire le reçu/état avant toute tentative. Si le résultat fournisseur demeure inconnu, ne pas relancer automatiquement ; présenter l’incertitude. Les écritures de notes actuelles avec CAS ne fournissent pas, à elles seules, ce contrat d’idempotence.

Un rejeu d’application retourne son reçu historique, **jamais une ancienne note à réinjecter dans l’éditeur**. Après reconnaissance du reçu, relire le document courant : il peut avoir été modifié depuis l’ajout. Il n’est pas nécessaire de conserver une copie complète de chaque résultat historique.

Pendant **Appliquer** ou **Annuler le dernier enrichissement**, bloquer brièvement la saisie et toute autre mutation après le dernier `flush`, jusqu’à réconciliation de la réponse avec le contrôleur. Le texte reste lisible et copiable. Si la réponse d’écriture est perdue, afficher « Vérification de l’enregistrement » et permettre de relancer la lecture ; ne pas débloquer une édition qui pourrait ensuite être écrasée par une ancienne réponse. Ce verrou court ne concerne pas la génération LLM, pendant laquelle la saisie reste libre.

### Limites techniques à respecter

La note actuelle accepte 100 000 points de code Unicode et 400 000 octets UTF-8, sans NUL ni surrogate isolé. Vérifier la note finale, séparateur inclus ; aucun rognage automatique. Une édition de proposition vide ne s’applique pas.

Le gateway inspecté borne la requête canonique à 65 536 octets et les chaînes à 16 384 unités UTF-16, avec NFC. La sélection assistant est actuellement bornée à 12 000 octets JSON ; son profil utilise 2 048 tokens de sortie et un texte limité à 8 000 unités UTF-16. **Ce sont des limites de l’existant, pas la capacité de toutes les notes.**

Créer des bornes explicites pour le profil d’enrichissement à l’intérieur du transport courant ; les figer avec des tests de charge utile avant l’UI. Une normalisation NFC destinée au modèle ne modifie ni le texte sauvegardé ni son digest UTF-8 exact. Ne pas augmenter les limites partagées ou les délais globaux par défaut.

L’autorité du gateway est actuellement bornée à 120 secondes et le timeout fournisseur de son entrée à 15 secondes. Les opérations IA ont leur cycle d’attente propre ; ne pas augmenter pour elles le timeout de l’autosave. Aucune nouvelle enveloppe financière, gestion d’abonnement ou configuration de modèle côté navigateur.

## 6. Confidentialité et non-régression

- Proposition, originaux et reçus de contenu restent accessibles au seul auteur selon les droits courants. L’organisateur ne récupère pas les notes d’un autre participant.
- Aucun ajout aux sources partagées de l’assistant réunion ou de l’agent dossier, même lorsque la réunion est classée dans un dossier partagé.
- Aucun contenu de note, transcription, proposition ou prompt dans les logs, erreurs, traces et analytics. Diagnostics limités aux étapes, codes, durées et identifiants opaques/digests adaptés à l’existant.
- Pas de persistance des notes dans `localStorage` ni de second stockage Django. Core demeure la référence.
- Le fonctionnement des notes simples, temporaires invités, transcription, synthèse, classement et assistants existants reste inchangé en l’absence de clic sur l’enrichissement.

## 7. Découpage pour une future délégation

Ces lots décrivent la future implémentation ; aucun agent ne doit la commencer sur la seule base de ce document. Chaque lot travaille sur une branche/worktree isolé. Pas de merge ou livraison staging sans nouvelle instruction utilisateur.

| Lot / agent | Responsabilité et livrable | Dépendance |
| --- | --- | --- |
| A — Contrats et données Core | Figer DTO, erreurs, bornes, transitions, table compagnon, règles de rétention et migration locale ; raccorder l’effacement au ledger d’écriture courant pour purger les textes/invalider les générations dans la même transaction ; tests de propriétaire, CAS et reçus | Point de départ, revue courte du contrat avant B/C |
| B — Inférence privée | Nouveau profil/opération dans le gateway existant, sélection de contexte, validation des citations, génération et cas sémantiques ; aucune mutation des notes depuis le modèle | Contrats A ; peut avancer en parallèle de C |
| C — Intégration Meet | Façade signée, client, composant de prévisualisation, contrôleur/autosave, messages et accessibilité ; d’abord sur réponses simulées | Contrats A ; aucun accès direct au fournisseur |
| D — Qualification indépendante | Préparer les cas pendant A/B/C, puis vérifier le parcours connecté local, isolation à deux comptes, conflits, répétitions et restauration | Intégration A+B+C pour les preuves finales |
| Orchestrateur | Réconcilier les contrats, relire simplicité/confidentialité, corriger les défauts remontés et fournir les preuves | Ne délègue pas à plusieurs agents les mêmes fichiers simultanément |

Avant de coder, actualiser les sources sur les branches isolées sans modifier les branches partagées ; choisir le numéro de migration libre alors disponible. Ne pas réutiliser un numéro historique comme 030–033. La migration est testée uniquement sur PostgreSQL jetable local dans ce chantier.

## 8. Critères d’acceptation et preuves attendues

| Cas | Résultat attendu |
| --- | --- |
| Notes sauvegardées + réunion terminée + transcription exploitable | Génération seulement après clic ; proposition séparée ; rien appliqué d’office |
| Notes vides, appel actif, source absente ou encore en traitement | Motif lisible ; aucun dispatch fournisseur |
| Sources natives stabilisées avec `partial_sources` | Enrichissement possible, couverture partielle annoncée, citations par source/groupe sans chronologie globale inventée |
| Synthèse absente mais transcription exploitable | L’enrichissement reste possible |
| Longue transcription avec sélection de passages | Couverture limitée annoncée ; toutes les citations viennent des segments envoyés |
| Note trop grande pour le transport | Refus avant dispatch, sans altérer ou tronquer les notes |
| Double clic / même commande / réponse perdue / rejeu d’une commande remplacée | Au plus un dispatch revendiqué et un ajout ; état relu avant reprise ; ancienne version d’état refusée ; reçu historique sans réinjection d’une ancienne note |
| Sauvegarde en vol avec nouvelles frappes | Génération sur la dernière version confirmée ou blocage ; aucun départ sur un ancien brouillon |
| Édition pendant génération ou prévisualisation, autre onglet, source actualisée | Proposition ancienne refusée sans perte de saisie ni écrasement |
| Apply contre autosave concurrent | CAS et contrôleur cohérents ; aucun ancien autosave ne rétablit le texte précédent |
| Proposition modifiée par l’utilisateur | Seuls les compléments sont transmis pour application ; indication de modification, pas de fausse certification par citation |
| Apply réussi puis rechargement | Préfixe original strictement identique, ajout unique, snapshot privé et annulation encore disponibles |
| Générer/abandonner B après apply A | Snapshot de A conservé ; une génération ne détruit pas le dernier Undo |
| Undo immédiat puis Undo après frappe locale ou édition serveur | Restauration exacte dans le premier cas ; refus sans perte dans les autres, nouvelle révision monotone |
| Résultat sans complément / JSON invalide / citation inventée / timeout / quota | Aucun faux succès ni écriture des notes ; état et possibilité de reprise cohérents |
| Deux comptes, deux tenants, organisateur, acteur utilisant un grant invité | Aucun accès transversal à la note, proposition ou snapshot ; refus d’enrichissement invité |
| Annulation, changement de compte/réunion, réponse tardive | Notes intactes ; aucune proposition appliquée dans une autre portée |
| Effacement volontaire ou suppression des notes | Copies de contenu purgées selon les règles définies, pas de récupération cachée |
| Accents décomposés, emoji, sauts de ligne, taille proche du plafond | Original exact conservé ; comptages Unicode/octet et transport corrects |
| Négation, proposition non confirmée, ancienne valeur, responsable absent, injection dans la note | Aucune inversion de sens, fait ou identité inventés ; aucune instruction embarquée exécutée |
| Clavier, lecteur d’écran, mobile | Actions nommées, progression annoncée, focus prévisible, état non transmis uniquement par couleur |

Tests à prévoir : unités contrôleur/contrats/sélection/sorties, intégration Core sur PostgreSQL jetable, intégration gateway avec fournisseur simulé et parcours navigateur local authentifié avec deux comptes de recette. Ne pas présenter les simulations comme une preuve de qualité GPT réelle.

Une qualification future avec le fournisseur configuré doit utiliser des notes et une transcription fictives réalistes dans un environnement local ou privé autorisé, avec le modèle effectivement identifié et les règles d’egress existantes. **Aucun appel GPT réel ni staging dans la présente tâche de planification.**

Définition de terminé pour la future feature : parcours complet démontré, confidentialité et conflits testés, original récupérable après rechargement, limites et couverture visibles, aucune régression des notes simples. Une jolie prévisualisation seule ne suffit pas.

## 9. Hors périmètre et points à confirmer

Hors périmètre : enrichissement automatique, enrichissement pendant l’appel, réécriture complète, suggestions par passage, notes invité persistantes, collaboration, partage avancé, accès des assistants aux notes, V2 Mastra, publication du compte rendu depuis Meet, changement ASR, budget/abonnements et CI/CD.

La direction recommandée est suffisamment précise pour discuter du produit sans commencer à coder. Deux validations doivent précéder l’implémentation :

1. Confirmer le compromis produit **compléments ajoutés en bas**, plutôt que réécriture du document entier.
2. Figer entre Core/gateway/Meet le contrat détaillé, les bornes exactes du nouveau profil et les noms des commandes sur la version du code qui sera retenue. Cela relève d’une courte revue technique, pas d’un nouveau système d’architecture.

## 10. Sources et repères pour les agents

Granola : [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes), [Chatting with your meetings](https://docs.granola.ai/help-center/getting-more-from-your-notes/chatting-with-your-meetings). Leur documentation décrit un premier enrichissement automatique et une régénération manuelle ; elle ne prouve ni notre parcours d’acceptation ni une garantie d’historique immuable. Notre bouton exclusivement manuel est un choix utilisateur propre à Meet.

Repères Meet, à lire dans le worktree retenu :

- `docs/features/mastrao-personal-notes.md`
- `src/frontend/src/features/meetingHistory/components/MeetingPersonalNotesSection.tsx`
- `src/frontend/src/features/meetingHistory/api/types.ts`
- `src/frontend/src/features/meeting-notes/components/PersonalNotesProvider.tsx`
- `src/frontend/src/features/meeting-notes/components/PersonalNotesEditor.tsx`
- `src/frontend/src/features/meeting-notes/model/PersonalNotesController.ts`
- `src/frontend/src/features/meeting-notes/api/personalNotesApi.ts`
- `src/backend/core/mastrao_personal_notes.py`

Repères Platform/Core, aux versions d’inspection indiquées plus haut :

- `packages/meeting-runtime-contract/src/personal-notes.ts` et `personal-notes-digest.ts`
- `packages/context-runtime-contract/src/canonical-json.ts`
- `services/tenant-core/shared-schema/030-meeting-personal-notes.sql`
- `services/tenant-core/src/meetings/meeting-personal-notes-ledger.ts`
- `services/tenant-core/src/meetings/meeting-personal-note-authority.ts`
- `services/tenant-core/src/meetings/native-transcript-authority.ts`
- `services/tenant-core/src/context/meeting-assistant-context-reader.ts`
- `services/tenant-core/src/context/meeting-assistant-selection.ts`
- `services/tenant-core/src/context/meeting-assistant-inference.ts`
- `services/tenant-core/src/context/model-gateway-client.ts` et `model-operation-journal.ts`
- `services/llm-gateway/src/context-meeting-assistant-profile.ts`

## 11. Exécution APEX autorisée les 22–23 octobre 2026

L’utilisateur a demandé `/apex -axv` et `/goal` après remise du plan. L’implémentation, la revue indépendante et la vérification **locale** sont désormais autorisées. Les mentions précédentes « planification seulement » décrivent la phase initiale et ne bloquent pas cette exécution. Le gel de `develop` et de staging reste absolu : aucun push, merge, PR ou déploiement.

Analyse : risque élevé limité aux données privées, à la concurrence et à une migration locale ; les exigences de preuve sont les tests de refus/propriétaire, CAS, répétition, effacement et restauration, puis l’exercice du parcours local. L’état partagé et les identifiants réels ne sont pas utilisés pour les tests.

Choix autonome : retenir les compléments ajoutés en bas et une prévisualisation modifiable, conformément à la recommandation de ce plan. Pas de réécriture complète. La qualité réelle du fournisseur reste distincte des preuves locales avec fournisseur simulé.

Bases locales retenues : Meet `b37ec25c28214ebc26a658004a67693caf77b795` dans le worktree du plan ; Platform `a35bafa155f86ad2a1b170c6cbf88a849e43c6e5` dans `/root/projects/mastrao-platform-note-enrichment`, branche `codex/meeting-note-enrichment`. Cette dernière base inclut les changements develop présents dans les références locales sans déplacer `develop`.

Ordre : figer les DTO partagés ; déléguer séparément stockage Core et profil gateway ; implémenter la façade et l’UI Meet sur le contrat figé ; intégrer, relire puis qualifier localement. Le fichier de plan tient lieu de suivi durable : aucune issue GitHub n’est créée puisque la livraison est exclusivement locale.

## 12. Isolation de ce livrable

Document créé dans le worktree local `/root/projects/mastrao-meet-note-enrichment-plan`, branche `codex/plan-meeting-note-enrichment`, depuis Meet `b37ec25c28214ebc26a658004a67693caf77b795`. Le checkout partagé `/root/projects/mastrao-meet` et ses modifications préexistantes ne sont pas utilisés pour écrire ce plan. Depuis l’autorisation APEX, le code applicatif est modifié uniquement dans les deux worktrees isolés indiqués. Aucun secret, configuration partagée ou workload distant n’est modifié.

## 13. Suivi d’implémentation locale — 23 octobre 2026

- Analyse et contrats : terminés. Ajouts seuls, commande signée dédiée, proposition privée et un Undo du dernier ajout.
- Core et gateway : implémentés. Migration locale 040, sources natives et de référence vérifiées sous transaction, génération hors transaction, aucune relance automatique.
- Meet : pont OIDC/CSRF et interface de relecture intégrés. Les notes originales restent gérées par l’éditeur et son autosave existants.
- Revue backend indépendante : Spec PASS / Standards PASS après correction des refus fournisseur confirmés.
- Validation statique : types/build Core et gateway, types Platform et lint ciblé PASS sur sources figées.
- PostgreSQL jetable : 23 tests PASS, y compris natif stabilisé `partial_sources`, refus des sources instables/expirées/remplacées, concurrence, purge et restauration exacte.
- Régressions Django : 174 PASS, 3 qualifications externes désactivées.
- Contrats réunion/contexte : 300 / 85 PASS ; gateway : 65 PASS ; façade Platform : 47 PASS.
- Frontend : 892 tests PASS au gel précédent ; types/lint et 38 tests des fichiers concernés PASS après le dernier correctif. Build précédent PASS. Revue indépendante finale Spec PASS / Standards PASS ; les deux P2 de réponse perdue et annulation sont clos.
- Navigateur local sur API fictive : génération, édition, ajout, rechargement et Undo PASS ; texte original exact restauré et aucun débordement à 390 px.

Ajustement de simplicité retenu lors de la revue : le bouton d’abandon doit cibler une proposition connue du serveur, jamais prétendre annuler une requête non encore réservée. Aucun arrêt fournisseur ou annulation durable d’un travail non créé n’est promis. Le texte personnel reste éditable pendant la préparation.

Le rapport et les preuves sont dans `.agents/qualification/meeting-note-enrichment/README.md`. Les simulations locales ne prouvent pas la qualité GPT réelle ni un parcours avec les identités, le stockage distant et les services de staging connectés. Ces qualifications restent futures, après levée explicite du gel ; elles ne justifient aucune mutation distante pendant cette exécution.

Exécution APEX locale terminée : analyse, implémentation, corrections de revue et vérification locale documentées. Travail conservé non publié dans les deux branches isolées. La recette GPT réelle et tout déploiement restent hors de cette livraison locale.

Revue complémentaire demandée le 23 octobre 2026 : trois passages, deux lots de corrections et cinq défauts établis clos. Verdicts finaux général, Spec, Standards et style : **APPROVE**. Les 74 fichiers produit et leurs empreintes finales, les corrections et les preuves sont documentés dans `.agents/qualification/meeting-note-enrichment/review-fix-loop/README.md`. Le gel de develop et staging reste applicable ; aucun commit, push, PR, merge ou déploiement effectué dans cette boucle.

Publication autorisée le 23 octobre 2026 après la fin de la démo : préparer les PR Meet et Platform vers `develop`. Cette nouvelle autorisation lève la restriction de publication pour cette fonctionnalité. Elle ne constitue pas une preuve de qualification GPT réelle ou de déploiement staging. Le bilan de recette destiné aux PR figure dans `.agents/qualification/meeting-note-enrichment/summary.md` ; les journaux et captures bruts restent locaux.
