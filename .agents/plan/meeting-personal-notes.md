# Mes notes privées pendant une réunion

Date : 16 octobre 2026
Statut : implémenté localement ; compte rendu et limites de validation en section 14
Périmètre validé : ajouter uniquement les notes aux vues Messages et Transcript existantes.

## 1. Besoin et décisions acquises

Pendant une réunion, chaque participant doit pouvoir écrire ses propres notes, passer aux messages ou au transcript, puis retrouver ses notes après la réunion.

La correction de l’utilisateur fait autorité : Messages et Transcript existent déjà. Leur création, leur raccordement ASR et une refonte de la transcription ne font pas partie de cette fonctionnalité.

Décisions validées :

- Ajouter « Mes notes » dans le panneau Messages, à côté des vues existantes.
- Notes privées, accessibles uniquement à leur auteur. Le rôle d’organisateur, d’administrateur ou de membre du même cabinet ne donne aucun accès supplémentaire dans l’application.
- Sauvegarde durable et automatique, rattachée à la réunion métier.
- Implémentation sur mesure intégrée à Mastrao, en conservant Core comme source de vérité métier.
- Un document personnel par auteur et par réunion.

Le caractère privé est une règle d’autorisation du produit. Ce plan ne promet pas de chiffrement de bout en bout ni d’inaccessibilité aux opérateurs de la base de données.

## 2. Brainstorm et choix retenus

| Sujet | Options envisagées | Choix pour cette version |
|---|---|---|
| Organisation | Une page personnelle ou une liste de petites notes | Une page personnelle, proche de la capture fournie |
| Éditeur | Texte simple ou éditeur riche | Réutiliser `TextArea`, avec paragraphes et retours à la ligne ; les puces saisies restent du texte |
| Emplacement | Nouveau panneau principal ou vue du panneau Messages | Ajouter une vue « Mes notes » au conteneur existant |
| Sauvegarde | Bouton manuel ou sauvegarde automatique | Automatique, avec état visible et reprise après erreur |
| Identité | Identité métier ou connexion LiveKit | Identité métier vérifiée par Core |
| Stockage | Messages LiveKit, table Django ou données métier Core | Une table métier Core |
| Mise à jour depuis deux onglets | Dernière écriture gagnante ou contrôle de version | Contrôle de version avec conflit explicite |
| OpenWhispr | Reprise complète ou inspiration ciblée | Inspiration UX ; aucune dépendance à son application Electron |

Le champ texte couvre l’usage montré : noter des idées pendant la réunion. Ajouter Tiptap, la dictée vocale, une barre de mise en forme ou un éditeur collaboratif apporterait un périmètre supplémentaire sans besoin confirmé.

OpenWhispr a été inspecté lors de l’analyse initiale : ses vues notes/transcript/résumé sont utiles comme référence, mais ses dépendances Electron et son stockage SQLite ne correspondent pas directement à cette intégration. Références : [interface](https://github.com/OpenWhispr/openwhispr/blob/main/src/components/notes/NoteEditor.tsx), [dépendances](https://github.com/OpenWhispr/openwhispr/blob/main/package.json), [stockage](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/database.js). La reprise de code éventuelle nécessiterait de conserver les mentions de sa [licence MIT](https://github.com/OpenWhispr/openwhispr/blob/main/LICENSE).

## 3. Parcours et interface

### Pendant la réunion

1. Le participant ouvre le panneau Messages.
2. Les vues Messages et Transcript gardent leur comportement actuel ; « Mes notes » est ajouté à côté, en respectant les libellés et l’ordre du produit réel.
3. À la première ouverture, afficher une zone vide : « Notez vos idées pendant la réunion… ».
4. Afficher une indication courte et permanente : « Ces notes sont visibles uniquement par vous. »
5. L’utilisateur écrit librement ; aucun bouton Envoyer et aucune publication dans le chat.
6. Afficher « Modifications non enregistrées », « Enregistrement… », puis « Enregistré », selon l’état réel.
7. Changer de vue ou fermer le panneau conserve le brouillon et permet à la sauvegarde de se terminer.

Ne pas créer de document vide lors d’un simple affichage. La première écriture non vide crée le document. Une note ensuite vidée conserve une version vide afin de distinguer l’effacement volontaire de l’absence de chargement.

L’arrivée de messages ou de texte dans Transcript ne doit pas déplacer le curseur, réinitialiser la sélection ou remplacer le contenu de l’éditeur.

### Sortie et fin de réunion

- Lors d’un départ déclenché par l’utilisateur, attendre la sauvegarde des modifications avant la navigation, avec un état d’attente visible.
- En cas d’échec, conserver le brouillon et permettre de réessayer ou de partir explicitement en acceptant la perte des dernières modifications.
- Lors d’une fermeture de réunion imposée par l’hôte, conserver le brouillon en mémoire pendant la transition vers l’écran de sortie et tenter sa sauvegarde sous une autorisation de notes valide.
- L’autorisation de sauvegarde des notes ne doit pas dépendre du maintien d’un accès média à une salle ouverte.
- Ne pas compter sur un effet de démontage, `beforeunload` ou `sendBeacon` comme garantie de sauvegarde. Une fermeture brutale du navigateur peut perdre les modifications non encore confirmées.

### Après la réunion

- Depuis la surface de consultation de la réunion dans Mastrao, l’auteur retrouve le même document privé et peut continuer à le modifier.
- Utiliser l’identité propriétaire vérifiée et les droits métier ; un lien vers la réunion seul ne donne aucun accès aux notes.
- Le chemin exact de cette surface et son contrat d’authentification sont à identifier dans le dépôt Core/produit avant implémentation. La route `/feedback` de Meet ne prouve pas l’existence d’un lecteur de notes.

### Accessibilité et mobile

- Réutiliser les primitives d’onglets et de champ texte du projet.
- Navigation clavier des onglets, nom accessible du champ, focus visible et zone de saisie défilante sur petit écran.
- Annoncer les erreurs et la confirmation de sauvegarde sans annoncer chaque frappe.
- Ne pas forcer le focus vers Mes notes à l’arrivée d’un message ou d’un segment de transcript.

## 4. Architecture et limites des observations

Chemin recommandé :

```text
Mes notes dans Meet
    → API Meet liée à la session et à la réunion
    → validation de l’autorité de notes dans Core
    → document personnel durable dans Core

Consultation après réunion dans Mastrao
    → même document et même contrôle de propriété dans Core
```

Constats vérifiés dans ce checkout :

| Fichier | Utilité pour l’implémentation |
|---|---|
| `src/frontend/src/features/rooms/livekit/components/SidePanel.tsx` | Point de composition du panneau ; son contenu Chat peut être démonté |
| `src/frontend/src/features/chat/components/Chat.tsx` | Vue Messages actuelle à conserver |
| `src/frontend/src/features/chat/components/ChatProvider.tsx` | Chat LiveKit ; ne pas y stocker les notes |
| `src/frontend/src/stores/layout.ts` | État d’interface existant |
| `src/frontend/src/primitives/Tabs.tsx` | Primitives d’onglets disponibles |
| `src/frontend/src/primitives/TextArea.tsx` | Champ texte existant |
| `src/frontend/src/api/fetchApi.ts` | Accès API avec cookies et en-tête CSRF |
| `src/backend/core/models.py`, `MastraoRoomBinding` | Association entre salle technique et `meeting_ref` |
| `src/backend/core/mastrao_recording_session.py`, `_participant` | Exemple de résolution serveur des autorités hôte/invité |
| `src/backend/core/mastrao_host_grant.py` | Autorité hôte liée à la session ; durée de vie à distinguer de celle des notes |
| `src/backend/core/mastrao_guest_grant.py` | Invité anonyme côté Django avec autorité vérifiée |

Le package Django `core` et le service métier Mastrao Core sont distincts. Le schéma et les migrations actuels du service métier n’ont pas été vérifiés. Les noms de table, champs et routes proposés ci-dessous sont des contrats de conception à aligner sur ce service.

Le checkout inspecté ne montre pas exactement le regroupement Messages/Transcript décrit par l’utilisateur. Au démarrage de l’implémentation, retrouver la branche et le composant qui portent cette interface, puis y ajouter Notes. Cette différence ne justifie pas de recréer Transcript.

L’arbre de travail contient de nombreuses modifications préexistantes, notamment sur les captures natives. Les préserver et limiter le diff de cette fonctionnalité aux notes et à leur intégration.

## 5. Modèle de données proposé

Une seule nouvelle table métier : `meeting_personal_notes`.

| Champ logique | Type indicatif | Règle |
|---|---|---|
| `id` | Identifiant opaque | Identifiant stable du document |
| `organization_id` | Référence canonique | Organisation de la réunion, déterminée côté serveur |
| `meeting_ref` | Référence canonique | Réunion métier ; intégrité vérifiée dans Core |
| `owner_ref` | Référence canonique | Auteur durable ; jamais l’identifiant de connexion LiveKit |
| `content` | Texte Unicode | Contenu brut, rendu comme texte |
| `revision` | Entier positif | Incrément atomique à chaque changement accepté |
| `created_at` | Date serveur | Création |
| `updated_at` | Date serveur | Dernière modification |

- Contrainte unique sur `(organization_id, meeting_ref, owner_ref)`.
- Si Core représente séparément les identités hôtes et invitées, normaliser le propriétaire selon son modèle existant ; inclure le type dans l’unicité si les références ne sont pas globalement uniques.
- Aucun champ de visibilité : la confidentialité est fixe pour cette version.
- Aucune nouvelle table de transcript, de partage, de résumé ou d’historique de versions.
- Limite proposée : 100 000 caractères Unicode par document, contrôlée côté serveur et annoncée dans l’interface à l’approche de la limite.
- Conservation et suppression suivent une politique dédiée aux notes, alignée avec la politique métier des réunions et des comptes. Ne pas déduire leur durée de vie d’un TTL de média ou d’un grant navigateur. Confirmer la politique Core avant migration et livraison.

## 6. Identité et autorisations

Invariants :

1. Core détermine l’organisation, la réunion et le propriétaire à partir d’une autorité vérifiée ; ces valeurs ne sont pas choisies dans le corps envoyé par le navigateur.
2. Toute lecture et écriture vérifie la propriété, y compris après la fin de réunion.
3. L’organisateur et les autres participants ne peuvent ni lire, ni modifier, ni lister les documents d’autrui.
4. Les corps des notes ne sont jamais diffusés sur LiveKit, ajoutés aux métadonnées de salle, envoyés à l’analytics ou journalisés dans les erreurs.
5. La prise de notes fonctionne quand l’enregistrement et la transcription sont désactivés ou refusés.
6. Le contenu n’est jamais envoyé automatiquement à un résumé, à un LLM ou à une recherche partagée.

### Invités et identité durable

L’intention est de couvrir les auteurs hôtes et invités. Les invités sont anonymes pour Django ; `request.user.is_authenticated` ne suffit pas.

Vérifier dans Core la stabilité de `host_ref` et `guest_ref` lors d’une réentrée, d’un nouveau handoff et d’un renouvellement de session. Les mapper au propriétaire durable si ces références représentent seulement une présence ou une autorisation temporaire.

L’accès après réunion nécessite une preuve d’identité de notes distincte de la capacité d’accès média :

- Pour un compte Mastrao, utiliser la session authentifiée et le lien canonique avec le propriétaire.
- Pour un invité, réutiliser un mécanisme Core existant de continuité d’identité s’il garantit la propriété. À défaut, définir un accès personnel de notes limité à cet auteur et à cette réunion, conservé côté session serveur, avant de livrer ce parcours.
- Une invitation transférée, une nouvelle session ou un nom identique ne doivent pas automatiquement donner accès aux notes d’un autre auteur.
- Ne pas promettre de récupération invitée sur un autre appareil ou après suppression de la session sans mécanisme de récupération identifié.

L’identité et la récupération invitée sont des décisions à résoudre pendant la première tranche. Leur absence ne doit pas être masquée par un rattachement au créateur de la salle, au cookie seul ou à une référence envoyée par le client.

## 7. Contrat API proposé

Routes publiques Meet indicatives, relatives à la racine API existante :

| Opération | Route | Comportement |
|---|---|---|
| Lire mon document | `GET /rooms/{room_id}/personal-note/` | Résoudre la réunion et l’auteur côté serveur |
| Enregistrer mon document | `PUT /rooms/{room_id}/personal-note/` | Créer ou remplacer mon contenu avec contrôle de révision |

Requête d’écriture : `content` et `expected_revision`. Réponse : `content`, `revision`, `created_at`, `updated_at`, et `id` lorsqu’un document existe. L’absence de document est une lecture réussie avec `id: null`, contenu vide, dates nulles et `revision: 0`.

Règles d’écriture :

- Première création : `expected_revision = 0`, contenu non vide, unicité garantie en base.
- Mise à jour : comparer et incrémenter la révision dans une même transaction.
- Effacement : contenu vide accepté sur un document existant, avec nouvelle révision.
- Révision dépassée : `409`, avec accès au document courant uniquement après contrôle du propriétaire.
- Après une réponse perdue, relire le document avant de renvoyer une écriture. Si son contenu est celui du snapshot envoyé, considérer ce snapshot sauvegardé sans écraser les frappes plus récentes.
- Mauvaise identité ou mauvais périmètre : réponse d’accès refusé selon la convention du projet, sans divulguer l’existence d’une note tierce.
- Corps trop long ou invalide : erreur de validation explicite.
- Core indisponible : erreur temporaire ; conserver le brouillon côté interface.

Réutiliser le transport privé Meet → Core existant, avec un contrat de notes dédié et borné. Ne pas réutiliser un consentement d’enregistrement comme autorisation de notes. Protéger les écritures CSRF et initialiser correctement ce mécanisme pour les sessions invitées.

La consultation après réunion utilise les mêmes opérations métier Core via l’autorité de l’auteur. Vérifier que le chemin Meet utilisé pour une dernière sauvegarde n’est pas rejeté simplement parce que la salle est fermée.

## 8. Sauvegarde automatique et état frontend

Créer un contrôleur de notes au niveau de la réunion, au-dessus du contenu démontable du panneau. Le document et les opérations en cours ne doivent pas dépendre du montage de `Chat` ou de l’éditeur.

États utiles : chargement, prêt, modifications en attente, sauvegarde en cours, erreur et conflit. Les distinguer du contenu lui-même.

Comportement :

1. Charger le document avant d’autoriser une sauvegarde. Un chargement en échec ne devient jamais une note vide à écrire.
2. Mettre à jour le texte local immédiatement pendant la frappe.
3. Sauvegarder après 800 ms sans frappe et au plus tard après 5 secondes de modifications continues ; attendre la fin d’une composition IME.
4. N’avoir qu’une écriture en vol par document. Conserver le dernier texte local pendant la requête et envoyer ensuite les modifications restantes avec la révision confirmée.
5. Une réponse confirme uniquement le snapshot envoyé. Elle ne remplace jamais le texte saisi depuis son départ.
6. Une navigation entre les vues ou la fermeture du panneau déclenche la sauvegarde des modifications en attente, sans supprimer le contrôleur.
7. Après une erreur réseau, afficher « Notes non enregistrées » avec « Réessayer ». La reconnexion permet une reprise après relecture de la révision serveur.
8. En cas de conflit, conserver séparément le brouillon et la version serveur. Proposer « Charger la version enregistrée » ou « Enregistrer mon brouillon à la place », après lecture de la version courante et confirmation explicite pour cette dernière action. Ne pas fusionner automatiquement le texte.
9. En cas de session expirée ou d’autorité révoquée, suspendre les écritures, conserver le brouillon en mémoire et afficher la reprise d’authentification pertinente.
10. Réinitialiser l’état lors d’un changement d’auteur ou de réunion ; une réponse tardive de l’ancien document ne doit pas modifier le nouveau.

La première version utilise le serveur pour la durabilité et la mémoire pour le brouillon en cours. Pas de copie permanente en `localStorage`, de moteur de synchronisation hors ligne ou de cache inter-comptes. Afficher l’avertissement de sortie navigateur uniquement si des modifications restent non confirmées. Une fermeture forcée peut perdre la dernière fenêtre non sauvegardée.

## 9. Découpage du code envisagé

Les noms sont indicatifs et doivent suivre les conventions du checkout d’implémentation.

| Emplacement | Responsabilité |
|---|---|
| `features/meeting-notes/api/` | DTO et appels via `fetchApi` |
| `features/meeting-notes/hooks/usePersonalNote.ts` | Chargement, révision, sauvegarde et conflits |
| `features/meeting-notes/components/PersonalNotesProvider.tsx` | Durée de vie du document au niveau de la réunion |
| `features/meeting-notes/components/PersonalNotes.tsx` | Champ texte et indication de confidentialité |
| `features/meeting-notes/components/NoteSaveStatus.tsx` | État de sauvegarde et action de reprise |
| Conteneur Messages existant | Ajouter l’onglet avec les primitives disponibles |
| Backend Meet, module dédié aux notes | Autorité de session et transport vers Core |
| Service métier Core | Migration, propriété, transactions et accès après réunion |

Réutiliser React Query pour les échanges serveur si cela correspond aux usages du projet ; garder le brouillon éditable séparé du cache serveur. Utiliser Valtio seulement pour l’état d’interface existant. Ne pas stocker deux copies concurrentes du document dans deux stores globaux.

Appliquer `clean-code` dans le scope modifié : responsabilités explicites, composants locaux cohérents, primitives partagées existantes, aucune branche imbriquée pour le choix des vues. Si trois vues sont sélectionnées par le même état, utiliser la composition des onglets ou un `switch` nommé.

## 10. Tranches d’implémentation

### Tranche 1 — Confirmer les contrats d’intégration

- [x] Identifier le checkout qui contient l’interface Messages/Transcript utilisée par l’utilisateur.
- [x] Localiser le service Core actuel, son modèle de réunion, son stockage et ses règles de migration.
- [x] Confirmer le propriétaire durable pour un auteur hôte et un auteur invité.
- [x] Définir l’autorité de notes après fermeture de salle et le parcours de récupération invité.
- [x] Identifier la surface Mastrao de consultation après réunion et la politique de conservation.

Sortie : contrats et emplacements précis. Aucun nouveau transcript ni migration de contenu de transcription.

### Tranche 2 — Persistance privée utilisable de bout en bout

- [x] Créer la table métier et la contrainte d’unicité dans Core.
- [x] Implémenter lecture et écriture conditionnelle, avec propriété contrôlée côté Core.
- [x] Ajouter les endpoints Meet et le transport privé avec autorité vérifiée et CSRF.
- [x] Prouver via tests que deux participants de la même réunion restent isolés.

Sortie : un auteur peut créer, relire et mettre à jour sa note ; un autre auteur ne peut pas y accéder.

### Tranche 3 — Mes notes dans le panneau existant

- [x] Ajouter la vue au conteneur Messages existant.
- [x] Ajouter l’éditeur texte et l’indication de confidentialité.
- [x] Monter le contrôleur au niveau de la réunion et implémenter la sauvegarde automatique.
- [x] Gérer les erreurs, les réponses tardives, les conflits et les changements de réunion/auteur.
- [x] Ajouter les traductions dans les langues requises par le dépôt.

Sortie : prise de notes utilisable pendant la réunion avec sauvegarde confirmée, quel que soit l’onglet affiché.

### Tranche 4 — Continuité après la réunion

- [x] Finaliser la sauvegarde avant le départ volontaire.
- [x] Conserver le brouillon pendant une fermeture de salle et autoriser sa dernière sauvegarde selon le contrat de notes.
- [x] Présenter le même document dans la surface Mastrao après réunion, avec l’autorité de son auteur.
- [x] Vérifier séparément les parcours hôte et invité et documenter les limites de récupération invitée.

Sortie : la fin d’une connexion média ne supprime ni le document ni les droits personnels prévus.

### Tranche 5 — Vérification ciblée

- [x] Exécuter les tests métier d’autorisation, d’unicité et de révision.
- [x] Exécuter les tests frontend de sauvegarde, navigation et concurrence.
- [x] Effectuer les contrôles de types/lint requis pour les fichiers modifiés.
- [x] Vérifier le parcours navigateur avec deux auteurs de fixture, puis deux onglets du même auteur ; vérifier séparément les autorisations réelles sur PostgreSQL.
- [x] Vérifier la largeur mobile et la navigation clavier dans Chromium ; limites du clavier physique détaillées en section 14.
- [x] Confirmer que Messages et Transcript gardent leurs comportements et que les notes ne sont jamais publiées dans ces flux.
- [x] Vérifier le diff final et appliquer les exigences documentaires des dépôts réellement modifiés.

## 11. Critères d’acceptation

| Scénario | Résultat attendu |
|---|---|
| Première ouverture | Éditeur vide, confidentialité expliquée, aucune création inutile |
| Saisie puis pause | Sauvegarde confirmée par le serveur |
| Saisie continue | Sauvegardes régulières sans déplacer le curseur |
| Passage Notes → Messages → Transcript → Notes | Texte et statut conservés |
| Fermeture puis réouverture du panneau | Brouillon conservé et sauvegarde poursuivie |
| Rechargement après « Enregistré » | Contenu identique relu depuis Core |
| Nouvelle frappe pendant une requête | Aucun caractère perdu à la réponse |
| Deux premières écritures concurrentes | Un seul document créé |
| Deux onglets modifient le document | Conflit explicite, aucun écrasement silencieux |
| Réponse perdue après écriture réussie | Relecture et réconciliation, sans doublon ni perte de brouillon |
| Chargement initial échoué | Aucune écriture vide automatique |
| Interruption réseau ou Core indisponible | Brouillon conservé, erreur visible, reprise possible |
| Départ volontaire avec modifications | Sauvegarde avant navigation ou choix explicite après échec |
| Fermeture imposée de la réunion | Brouillon conservé pendant la transition ; autorisation de notes évaluée séparément du média |
| Consultation après réunion | Seul l’auteur retrouve et modifie son document |
| Autre participant, hôte ou administrateur produit | Accès aux notes de l’auteur refusé |
| Tentative avec une autre réunion/organisation/propriétaire | Rejet côté serveur |
| Invité admis et reconnu comme auteur | Accès limité à son document sous autorité vérifiée |
| Grant expiré ou révoqué | Aucune écriture autorisée avec l’ancienne autorité |
| Enregistrement/transcription désactivés | Prise de notes disponible |
| Note volontairement vidée | Effacement durable, sans réapparition de l’ancien texte |
| Changement de compte ou de réunion | Aucun contenu précédent affiché ou sauvegardé au mauvais endroit |

## 12. Hors périmètre et points à résoudre

Hors périmètre de cette version : partage des notes, accès organisateur, édition collaborative, dictée vocale, génération de résumé, envoi à une IA, liens horodatés vers le transcript, import de sélections du transcript, export, mise en forme riche et historique complet des versions.

Points identifiés lors de la conception, désormais précisés dans la section 14 et la documentation d'activation :

- Emplacement exact du conteneur Messages/Transcript et du service Core actuel.
- Stabilité des identités métier et continuité de propriété des invités.
- Accès personnel après réunion, y compris après fermeture de la salle.
- Politique de conservation et de suppression des notes.

Les choix UX et le périmètre sont établis. Ces points servent à préciser les contrats d’implémentation ; ils ne rouvrent pas le chantier Transcript.

## 13. Validation de ce document

La version initiale du plan était issue de l’analyse statique du checkout et des décisions utilisateur. Les sections 1 à 12 conservent cette conception initiale ; la section 14 décrit l'implémentation et ses preuves d'exécution.

## 14. Exécution APEX — 16 octobre 2026

Options : autonomie, revue contradictoire, vérification. Implémentation dans deux checkouts isolés, sans modification du checkout utilisateur initial.

Bases isolées : Meet `94d6f71c2`, Platform/Tenant Core `f17c9020`.

- [x] Retrouver l’interface existante : `MeetingConversationTabs` dans `SidePanel.tsx`, checkout Meet récent.
- [x] Localiser Core actuel : `services/tenant-core` du checkout Platform récent.
- [x] Table et opérations privées Core, contrats et tests d’autorisation.
- [x] API Meet liée à la session avec accès personnel après fermeture.
- [x] Contrôleur frontend et éditeur de notes avec tests de concurrence.
- [x] Intégration des onglets, de l’historique et du départ de réunion.
- [x] Validation ciblée, revue contradictoire, corrections et preuve navigateur ; limites détaillées ci-dessous.

Fichiers cibles : nouvelle fonctionnalité `features/meeting-notes`, `SidePanel.tsx`, `useSidePanel.ts`, `Conference.tsx`, `LeaveButton.tsx`, `MeetingHistoryDetailView.tsx`, API dédiée Meet, Tenant Core notes/contrats/DDL. Aucun chantier ASR ou transcript.

### Vérification intermédiaire

- Tenant Core : 208 tests ciblés, dont 7 tests sur PostgreSQL local avec migrations 001–030 ; contrats : 277 et 207 tests. Écriture HTTP réelle via Python de 100 000 emojis (1 201 434 octets sur le fil), relecture exacte, contrôle de version et isolation de deux tenants et de plusieurs auteurs.
- Meet API : 184 tests réussis, dont 74 dédiés aux notes ; sessions hôte/invité, CSRF, fermeture et continuité d'un invité connecté.
- Interface : suite complète initiale de 672 tests, puis correction de trois assertions devenues obsolètes ; 245 tests ciblés réussis après intégration, types et lint vérifiés. Les contrôles seront relancés sur les corrections de revue.
- Navigateur : appel LiveKit local réel, trois onglets existants + notes, fermeture du panneau, sauvegarde, départ vers l'écran de sortie, historique, conflit entre deux onglets, mobile sans débordement. L'API métier du navigateur est une fixture locale ; elle ne constitue pas une preuve de l'autorisation Core, vérifiée séparément sur PostgreSQL.
- Revue indépendante sécurité et lisibilité : aucun constat actionnable. Revue logique : navigation navigateur/outil contournant la protection du brouillon (P1), dépassement de limite coupant le suffixe existant (P2). Ces deux constats ont été corrigés : protection de la route sortante au niveau du routeur racine ; refus intégral des modifications dépassant la limite, y compris à la validation IME. Les régressions dédiées passent.

La procédure d'activation et les limites d'identité invité sont documentées dans `docs/features/mastrao-personal-notes.md` et `services/tenant-core/personal-notes.md` dans le checkout Platform associé. Aucune migration distante ni livraison n'a été exécutée.

### Modèle de données livré

Table `core.meeting_personal_notes` dans Tenant Core : `tenant_id`, `note_id`, `meeting_id`, `owner_actor_id` ou `owner_guest_ref` (exclusifs), `content`, `revision`, `created_at`, `updated_at`. Une unicité par réunion et propriétaire empêche les créations concurrentes en double. Les clés étrangères portent aussi le tenant. RLS forcée au niveau tenant et contrôle de propriétaire avant lecture/écriture ; les droits SQL runtime interdisent de modifier le propriétaire.

### Résolution de la revue

| Constat | Niveau | Correction | Preuve |
|---|---|---|---|
| Retour navigateur et navigation via l'outil commun pouvant démonter un brouillon | P1 | `GuardedRouter` garde la route sortante montée jusqu'à validation de la sauvegarde ou abandon explicite ; les liens ordinaires gardent leur garde avant navigation | Sept cas de routeur, inclus dans 63 tests d'intégration réussis |
| Insertion au début/milieu d'une note à la limite coupant le suffixe existant | P2 | Refuser la modification entière et restaurer le texte précédent ; restaurer aussi le brouillon avant composition IME | 49 tests de fonctionnalité réussis, dont insertions d'emojis et IME |

La seconde revue de lisibilité du routeur n'a relevé aucun constat actionnable. La suite PostgreSQL finale a été relancée via le script documentaire : 7/7 tests réussis, y compris la vraie requête HTTP Python de 1 201 434 octets. Le build frontend de production est réussi. Types et lint des deux projets sont vérifiés ; Prettier valide les 39 fichiers frontend modifiés.

### Vérification indépendante et limites

Le [rapport navigateur](meeting-personal-notes-browser-verification.md) conclut PASS sur les parcours exercés et contient 19 captures : appel LiveKit réel, sauvegarde/rechargement, changements d'onglet, sortie vers Feedback, historique, erreur réseau avec reprise/abandon, conflit 409 et confirmation, retour navigateur, navigation par la barre commune, deux auteurs de fixture, affichage à 390 px et refus d'une insertion au milieu d'un document de 100 000 caractères.

Les appels métier de ce navigateur utilisent une API de fixture. Cette exécution ne prouve donc pas un parcours complet déployé entre navigateur, Django et Core. L'autorisation, la persistance, les révisions concurrentes et la fermeture sont vérifiées séparément par les tests API et PostgreSQL réels. Les événements IME sont synthétiques ; le clavier mobile est approché par une réduction du viewport. La fermeture imposée de réunion est couverte côté code/tests, pas par un scénario navigateur avec un serveur distant.

La suite complète Core n'est pas déclarée verte : trois suites PostgreSQL préexistantes (`connectors/service`, `hermes-email/discovery`, `hermes-email/service`) appellent `initdb` sous root dans leurs helpers existants. Les 208 tests Core ciblés et les sept nouveaux tests PostgreSQL passent ; le nouveau helper lance PostgreSQL sous le compte local prévu. Aucun helper hors périmètre n'a été modifié.

Les notes invitées restent liées à la rédemption individuelle et à sa preuve valide. La fermeture de réunion n'efface pas le document ; aucune purge automatique ni récupération sur un autre appareil n'est ajoutée. La livraison devra appliquer la migration 030 et configurer les deux endpoints privés avant l'activation Meet.

### État des changements avant publication Git

Meet : `/root/.codex/worktrees/meeting-personal-notes/mastrao-meet` (base `94d6f71c2`). Platform/Core : `/root/projects/mastrao-platform-personal-notes`, branche `codex/meeting-personal-notes` (base `f17c9020`). Les changements restent locaux et non commités. Aucune migration distante, publication Git ou livraison n'a été exécutée.

### Résultats finaux

| Couche | Résultat |
|---|---|
| Frontend complet | 85 fichiers, 694 tests réussis |
| Meet API Django | 184 tests réussis, dont 74 dédiés aux notes |
| Core ciblé | 208 tests réussis, dont les sept tests PostgreSQL réels |
| Contrats | 277 et 207 tests réussis |
| Façade Platform | 78 tests ciblés réussis |
| Compilation et qualité | Build frontend de production, builds Core et prérequis, types Meet/Platform, lint frontend et 33 fichiers Platform, format des 39 fichiers frontend, diff sans erreur |
| Revue | Sécurité et lisibilité sans constat restant ; P1 et P2 corrigés et revérifiés |
| Navigateur indépendant | PASS sur les parcours documentés, API de fixture ; rapport et 19 captures associés |

Commande frontend finale : `npm test -- --maxWorkers=2 --testTimeout=15000`. Les premiers passages au délai par défaut de cinq secondes avaient rencontré deux dépassements sur des tests préexistants (calendrier et transcript). Le test calendrier a aussi réussi seul au délai par défaut ; aucun de ces deux tests ni leur code métier n'a été modifié. Les logs de cette session se trouvent dans `/tmp/meeting-notes-verification/`.

## 15. Vérification supplémentaire à la demande de l'utilisateur

Le [rapport de vérification sur le vrai chemin de notes](meeting-personal-notes-verify-real.md) apporte une nouvelle preuve locale : interface → Django réel → Core HTTP réel → PostgreSQL isolé. Les appels de notes ne sont plus des fixtures. Les critères locaux documentés passent, avec deux auteurs, un vrai conflit 409, sauvegarde/rechargement, reprise hors ligne, protection avant sortie, édition après fermeture et relecture SQL exacte. Les sept tests PostgreSQL ont été relancés avec succès. L'authentification initiale et les métadonnées restent préparées pour le banc ; aucune validation distante n'est revendiquée.
