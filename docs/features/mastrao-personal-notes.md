# Notes personnelles de réunion

« Mes notes » complète les onglets Messages et Transcription existants. Le document est privé : Core dérive son propriétaire de l'autorité serveur, jamais d'un identifiant envoyé par le navigateur. Les messages, la transcription, la synthèse partagée et les traitements IA ne contiennent pas ces notes.

## Stockage et accès

Tenant Core possède `core.meeting_personal_notes`. Une contrainte d'unicité associe chaque réunion à un acteur métier ou à une rédemption individuelle d'invitation. Deux personnes utilisant la même invitation ont des documents distincts. La RLS isole les tenants ; le service vérifie aussi le propriétaire, les droits courants et les preuves de session à chaque opération. Un rôle organisateur ou administrateur ne donne pas accès au document d'un autre auteur.

Le document accepte 100 000 caractères Unicode, sans NUL ni substitut Unicode isolé. Une lecture vide ne crée aucune ligne. Vider un document déjà enregistré conserve sa révision et efface son texte. La fermeture de la réunion ne supprime pas les notes et n'empêche pas leur modification sous une autorité valide. Aucun nouveau stockage Django, cache navigateur persistant ou délai de purge automatique n'est introduit.

L'hôte retrouve le même document dans l'historique grâce à son acteur canonique. Un invité retrouve son document depuis l'écran de sortie et via sa preuve de session correspondant à la réunion, même si le navigateur possède aussi une connexion OIDC. Les notes d'invité restent liées à cette rédemption : elles ne sont pas transférées automatiquement à un compte et ne sont plus accessibles avec une preuve expirée ou révoquée. Un simple lien de réunion ne permet pas de les récupérer sur un autre appareil.

## Sauvegarde

La sauvegarde attend 800 ms sans frappe, avec un délai maximal de cinq secondes pendant la saisie continue. Une seule écriture est en vol ; les modifications suivantes restent dans le brouillon jusqu'à confirmation. Le serveur compare `expected_revision` avant de remplacer le document. Un conflit 409 affiche un choix explicite entre charger la version enregistrée et remplacer celle-ci par son brouillon après confirmation.

Après une réponse perdue, une relecture précède la reprise. Le départ de réunion et les liens de navigation de l'espace de consultation attendent la sauvegarde. En cas d'échec, le brouillon reste disponible dans une fenêtre de récupération, avec reprise ou départ explicite sans enregistrer. Une fermeture brutale du navigateur peut perdre des modifications non confirmées ; `beforeunload` sert d'avertissement, pas de garantie de livraison.

## API

| Surface Meet | Opérations |
|---|---|
| `/api/v1.0/rooms/{room_ref}/personal-note/` | GET / PUT, preuve hôte ou invité de cette session, y compris après fermeture |
| `/api/v1.0/meetings/history/{meeting_ref}/personal-note/` | GET / PUT, preuve correspondant à la réunion puis autorité OIDC serveur |

PUT accepte uniquement `{ "content": "…", "expected_revision": 0 }`. La réponse est `{ "id": null, "content": "", "revision": 0, "created_at": null, "updated_at": null }` avant la première sauvegarde. Les réponses sont privées et non mises en cache. GET initialise le jeton CSRF ; PUT exige sa présence.

Les opérations internes Core sont des POST `/internal/v1/meetings/personal-notes/read` et `/write`. La façade Platform expose GET/PUT `/api/meet/meetings/history/{meeting_ref}/personal-note` et appelle les commandes Core signées `readPersonalNote` et `writePersonalNote`.

## Activation lors d'une livraison

1. Appliquer la migration Core `services/tenant-core/shared-schema/030-meeting-personal-notes.sql` après la chaîne canonique précédente, avec le rôle opérateur prévu par le déploiement Core.
2. Livrer les contrats et les routes Core/Platform compatibles.
3. Configurer Meet avec les deux URL privées suivantes, sur l'hôte Core autorisé par le transport existant :

   ```dotenv
   MASTRAO_CORE_PERSONAL_NOTES_READ_ENDPOINT=http://cabinet-core:8080/internal/v1/meetings/personal-notes/read
   MASTRAO_CORE_PERSONAL_NOTES_WRITE_ENDPOINT=http://cabinet-core:8080/internal/v1/meetings/personal-notes/write
   MASTRAO_CORE_PERSONAL_NOTES_TIMEOUT_SECONDS=5
   ```

   Adapter le port au service réel. Ces valeurs sont des exemples locaux, pas un changement d'infrastructure. Une configuration absente ou refusée retourne une erreur de sauvegarde ; elle ne crée pas de stockage de secours.

4. Livrer Meet puis vérifier deux auteurs, un conflit entre onglets et l'accès personnel après fermeture. Aucun déploiement distant n'est effectué par cette implémentation.

Les tests PostgreSQL se lancent depuis Platform avec `pnpm test:meeting-personal-notes:postgres`. Ils créent un cluster local isolé, appliquent la chaîne SQL, testent la persistance, la concurrence, les permissions et les routes HTTP, puis arrêtent ce cluster. Ils n'acceptent aucune URL de base applicative.
