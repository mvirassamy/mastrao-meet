# Vérification de Mes notes — 16 octobre 2026

**Verdict : PASS sur le périmètre local défini ci-dessous.** Aucune correction du code applicatif nécessaire pendant cette vérification.

Cette exécution remplace la limite principale de la vérification précédente : les requêtes de notes du navigateur ne sont plus simulées. Elles traversent l'interface du worktree, le vrai middleware et les vues Django, le serveur HTTP Core, les autorisations métier et une base PostgreSQL isolée avec les migrations 001–030.

## Périmètre et environnement

- Meet : `/root/.codex/worktrees/meeting-personal-notes/mastrao-meet`, base `94d6f71c2`, changements locaux présents.
- Core : `/root/projects/mastrao-platform-personal-notes`, base `f17c9020`, changements locaux présents.
- Interface : `http://127.0.0.1:5197` ; API Django : `http://127.0.0.1:8099` ; Core : `http://127.0.0.1:8101`.
- Réunion : `meeting_c7b80dfc50064551ba5ce6e78837f54b` ; salle : `room_4b227784dbfa40dc8bc27687a8f5fa73`.
- Salle : `http://127.0.0.1:5197/room_4b227784dbfa40dc8bc27687a8f5fa73`.
- Historique : `http://127.0.0.1:5197/reunions/historique/meeting_c7b80dfc50064551ba5ce6e78837f54b`.
- Chromium piloté par `dev-browser`, deux contextes séparés : `notes-real-alice` et `notes-real-bob`. Connexion LiveKit locale réelle, caméra et microphone désactivés.
- Un hôte Alice et deux rédemptions invitées distinctes ont été préparés dans les bases isolées avec des grants signés et des sessions Django réelles. Leurs signatures, liaisons de session et droits sont contrôlés par le code applicatif. Le parcours initial SSO/handoff n'a pas été exercé.
- Les réponses de configuration, d'identité affichée, de métadonnées de salle/historique et de transcript restent des fixtures sur 8103. Elles ne stockent ni ne traitent les notes dans ce banc. Le fonctionnement réel des messages et de l'ASR n'est pas revendiqué.

## Critères observables

| Critère | Résultat | Preuve actuelle |
|---|---|---|
| Mes notes apparaît à côté des vues existantes pendant un appel | PASS | Appel LiveKit rejoint depuis le lobby ; champ nommé, phrase de confidentialité, statut Enregistré ; F01 |
| Sauvegarde automatique durable et texte Unicode exact | PASS | PUT réel 200, rechargement et nouvelle lecture dans l'interface ; accents, retours à la ligne et emoji conservés ; relecture Core et SQL concordante |
| Changement d'onglet et fermeture du panneau conservent le texte | PASS | Messages → Transcription → Mes notes, fermeture puis réouverture du panneau : texte identique |
| Un autre auteur accède uniquement à son document | PASS | Bob découvre un document vide après l'enregistrement d'Alice, puis enregistre son propre texte ; deux lignes SQL aux propriétaires distincts, contenus différents ; F02 et sql-readback.json |
| Concurrence entre deux onglets sans écrasement silencieux | PASS | A enregistre avec 200 ; B, chargé auparavant, reçoit un vrai 409. Son brouillon reste affiché. Charger la version enregistrée retrouve exactement la version A ; F03 |
| Sortie bloquée sur échec de sauvegarde, puis reprise confirmée | PASS | L'ancien onglet live reçoit lui aussi un 409. Le dialogue conserve son brouillon et empêche la sortie. Après résolution et nouvelle saisie, PUT 200 avant navigation ; Feedback affiche la dernière version exacte ; F04 et F05 |
| Retour navigateur hors ligne sans perte du brouillon | PASS | Traversée réelle de l'historique SPA après une entrée pushState, réseau désactivé : l'URL change mais l'éditeur sortant reste monté dans le dialogue. Après reconnexion, reprise avec PUT 200 avant affichage de la liste ; nouvelle ouverture du détail identique ; F07 et F08 |
| Lecture et modification personnelles après fermeture canonique | PASS | État Core mis à ended et binding Django marqué closing_at dans les bases isolées. GET/PUT hôte et invité réussissent sur l'historique ; le même document est relu via la route salle. L'interface invitée retrouve ensuite ce texte et le modifie ; http-proof.json, F06 |
| Autorité serveur, CSRF et limites de contenu | PASS | Sans session : 404 ; propriétaire ajouté au corps : 422 ; PUT sans CSRF : 403 ; 100 001 caractères : 422 ; autre salle : 404. Requêtes et statuts conservés dans http-proof.json |
| Affichage mobile et saisie clavier | PASS | Viewport 390 × 844, focus et ajout par End/Entrée/saisie clavier, PUT 200 et statut Enregistré ; scrollWidth = innerWidth = 390 ; F06 |
| Isolation et persistance PostgreSQL, régressions métier | PASS | Relance actuelle de pnpm test:meeting-personal-notes:postgres : sept tests réussis, dont isolation des tenants et auteurs, refus de privilèges, révisions concurrentes, fermeture, révocation et vrai transport HTTP Python de 1 201 434 octets |
| Intégrité et accès aux captures | PASS | Huit PNG lisibles et décodés localement, HTTPS 200 image/png, Cache-Control private, no-store ; même clé média sur /api/health : 401 ; media-gates.json |

## Relecture faisant autorité

Après les essais navigateur, une connexion PostgreSQL indépendante a lu `core.meeting_personal_notes` et `core.meeting_lifecycles`, puis comparé les résultats avec les GET Django des sessions correspondantes.

- Réunion `ended`, exactement deux documents, propriétaires distincts.
- Alice : révision 5, 67 caractères, SHA-256 `bef7ad09e928381fab51417b6024dd59f82e8f44d54ac1ae521a325d5e618f67`.
- Bob : révision 3, 92 caractères, SHA-256 `034b7f0f64905670d708f9386ee582f1fa2c14f001adfcb0533d5f7e74830f9d`.
- Identifiants, révisions et contenus SQL strictement identiques aux réponses HTTP pour chaque auteur.

Le nouveau test PostgreSQL a aussi écrit et relu exactement 100 000 emojis : 400 000 octets UTF-8 de texte, 1 201 434 octets sur le fil avec les échappements JSON Python, HTTP 200.

## Preuves conservées

Dossier : `/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-real-20261016/`.

- `http-proof.json` : 14 requêtes, méthodes, chemins, statuts, révisions et empreintes de contenu.
- `sql-readback.json` et `browser-readback.json` : lectures finales indépendantes.
- `postgres-tests.log` : sortie complète de la relance des sept tests.
- `commands.md`, scripts HTTP/SQL, `instrumentation-notes.md` : commandes et incidents du banc, distingués des comportements du produit.
- `media-gates.json`, `evidence-manifest.json` : contrôles des images et empreintes des fichiers.
- F01 à F08 : captures de l'appel, de l'autre auteur, du conflit, de la reprise avant sortie, de Feedback, du mobile et de la navigation hors ligne.

Les cookies, grants et URL complètes de contournement média ne sont pas inclus dans les preuves publiées.

![F01 — Mes notes pendant un appel, sauvegardées par Django et Core dans PostgreSQL](/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-real-20261016/F01-notes-real-live.png)

![F06 — Édition mobile après fermeture de la réunion](/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-real-20261016/F06-notes-real-mobile.png)

## Limites de cette conclusion

PASS concerne les critères locaux ci-dessus. Aucun déploiement distant, accès à une base applicative existante ou modification du code applicatif n'a été effectué. La fermeture a été préparée directement dans les registres isolés ; le bouton hôte de fermeture et toute son orchestration média n'ont pas été revérifiés ici. L'authentification initiale SSO, la façade OIDC Platform depuis un autre appareil, le fonctionnement de l'ASR, un clavier mobile physique et une IME physique ne sont pas prouvés par cette exécution.

Les vérifications de compilation et la suite frontend complète de 694 tests appartiennent à l'exécution APEX précédente ; elles n'ont pas été relancées ici puisqu'aucun code applicatif n'a changé. Les processus temporaires de ce banc ont été arrêtés après capture des preuves.
