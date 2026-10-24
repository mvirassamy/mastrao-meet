# Enrichissement manuel des notes personnelles

Cette fonctionnalité est préparée sur branches locales isolées. Elle n’est pas livrée en staging.

## Parcours

Après la réunion, l’auteur connecté ouvre « Mes notes » et choisit « Enrichir mes notes ». Core vérifie une note enregistrée non vide, la clôture de la réunion et une transcription exploitable. Les notes originales servent à choisir les sujets ; seuls les passages de cette transcription justifient les nouveaux faits.

La proposition est une liste de compléments, chacun ancré sous une ligne de la note (`after_line`, profil `meeting_notes_enrichment_fr_v2`). Pendant la revue, « Mes notes » affiche la note comme un diff : les lignes d’origine telles quelles et chaque complément en vert sous sa ligne, avec ✓ / ✕ et un texte modifiable. L’utilisateur peut lire les passages utilisés, garder, refuser ou corriger chaque complément, puis « Enregistrer » ou « Annuler ». « Enregistrer » envoie les compléments gardés par leur position dans la proposition ; Core les insère sous leur ligne sans modifier les lignes d’origine. Les citations concernent la proposition initiale, pas les modifications de l’utilisateur.

« Annuler le dernier enrichissement » restaure la copie exacte précédant le dernier ajout, y compris espaces, accents décomposés et retours de ligne, si la note n’a pas changé depuis. Une modification concurrente produit un conflit plutôt qu’un écrasement. Une nouvelle génération ou son abandon conserve cette possibilité d’annulation jusqu’au prochain ajout accepté.

Les notes invitées temporaires et les assistants réunion/dossier ne reçoivent aucun accès supplémentaire. Aucun enrichissement automatique ou pendant l’appel.

## Responsabilités

- Meet : éditeur existant, sauvegarde préalable, relecture, actions explicites et réconciliation après réponse perdue.
- Django : session OIDC, CSRF des mutations, validation bornée, transport vers la façade Platform existante.
- Platform : résolution de l’identité OIDC et commande signée pour Core ; aucun stockage de note supplémentaire.
- Core : propriétaire courant, note canonique, révisions, sources courantes, proposition privée et restauration transactionnelle.
- LLM gateway : opération `meeting_notes_enrichment`, profil `meeting_notes_enrichment_fr_v1`, sortie structurée et citations contrôlées. Réutilise le journal d’opérations et les protections financières existants.

Le chemin est direct ; il n’ajoute ni Mastra, ni fournisseur alternatif, ni stockage Convex. Les notes ne sont pas envoyées dans le chat de l’assistant.

## Contrats et stockage

Meet expose GET/POST `/api/v1.0/meetings/history/{meeting_ref}/personal-note/enrichment/`. Platform expose GET/POST `/api/meet/meetings/history/{meeting_ref}/personal-note/enrichment`. GET construit une lecture privée. POST accepte une des actions `generate`, `apply`, `discard`, `undo` avec identifiant de commande et version d’état attendue ; les actions qui dépendent du texte exigent aussi sa révision. `apply` porte `items: [{ index, text }]` : les compléments gardés et leur texte approuvé.

La commande signée Core `enrich_meeting_personal_note` rejoint POST `/v1/meetings/personal-notes/enrichment`. Les DTO stricts de référence se trouvent dans `packages/meeting-runtime-contract/src/note-enrichment.ts` côté Platform. L’identité et le texte original ne sont pas des paramètres du navigateur.

La migration Core `040-meeting-note-enrichment.sql` ajoute un compagnon privé de `meeting_personal_notes`, avec RLS forcée, clé tenant/note et droits d’écriture limités par colonne. Il conserve séparément la proposition en attente et la copie précédant le dernier ajout accepté. Ce n’est pas un historique de toutes les versions. Vider volontairement la note purge ces copies dans la transaction d’écriture ; une réponse IA tardive ne les recrée pas.

La génération est élue dans une transaction courte, exécutée hors transaction, puis finalisée après contrôle des révisions et des sources. Ajouter et annuler prennent les verrous de la note et du compagnon. Ajouter vérifie aussi la transcription sous la transaction de mutation. Rejouer la même commande ne répète ni l’ajout ni l’appel au fournisseur.

Avant l’appel au fournisseur, Core conserve dans la proposition le profil, sa version, les références de tous les passages sélectionnés et leur couverture. Ces métadonnées restent disponibles en cas de résultat incertain, sans prétendre que le fournisseur a terminé l’analyse. Elles suivent la même purge que la proposition et ne sont pas ajoutées au DTO public.

## Limites et erreurs

Les notes simples gardent leur plafond existant de 100 000 caractères. L’enrichissement exige que la note complète tienne dans le budget d’entrée du profil ; il ne la tronque pas silencieusement. Les compléments sont limités à 32 et à 8 000 unités UTF-16 au total. Le contrat canonique borne aussi la taille totale de la requête.

Une transcription longue peut être sélectionnée : la couverture est affichée. Une transcription native stabilisée reste parfois `partial_sources` ; elle est utilisable avec cet avertissement. Une transcription absente, encore en traitement ou remplacée ne permet pas d’appliquer une proposition obsolète.

Une relecture marque la proposition périmée si sa transcription a changé. La perte d’autorisation des sources désactive l’enrichissement mais conserve l’annulation privée du dernier ajout, tant que le propriétaire est toujours autorisé et que ses notes n’ont pas changé.

Un résultat vide, une sortie invalide ou un refus confirmé ne modifie pas les notes. Une réponse perdue ou un timeout reste incertain jusqu’à relecture. Aucune relance automatique du fournisseur. Les erreurs publiques n’exposent ni notes, ni corps fournisseur, ni secrets.

## Vérification locale

Depuis le worktree Platform :

```sh
NOTE_ENRICHMENT_POSTGRES=1 NODE_OPTIONS=--conditions=development pnpm --filter @mastrao/tenant-core exec vitest run src/meetings/meeting-note-enrichment-postgres.test.ts
pnpm exec vitest run src/__tests__/meet-note-enrichment-route-handler.test.ts
pnpm --filter @mastrao/tenant-core exec vitest run src/meetings/note-enrichment-security.test.ts src/context/meeting-note-enrichment-inference.test.ts
```

La suite PostgreSQL démarre un cluster jetable local sans base applicative, applique la chaîne canonique et l’arrête après les tests. Les contrôles navigateur utilisent une API de recette fictive ; ils prouvent l’interaction de l’interface. Les suites Core prouvent séparément persistance, conflits et confidentialité. Le fournisseur est simulé : ces preuves ne qualifient pas la qualité réelle GPT, l’egress de staging ou un parcours entièrement connecté en staging.

La qualification locale détaillée et ses captures sont conservées sous `.agents/qualification/meeting-note-enrichment/` dans le worktree Meet.
