# Identité Mastrao Visio et e-mails

L’application utilise le nom **Mastrao Visio**. L’icône configurée est
`/assets/mastrao-logo-icon.png` : le M Mastrao sur fond blanc. Le fond bleu nuit est
réservé au portail. Le titre et l’icône du frontend sont déjà configurés ; il n’est
pas nécessaire de les remplacer.

## E-mails de l’application

Les e-mails de l’application couvrent les invitations à une visioconférence en
cours, les invitations planifiées et la disponibilité d’un enregistrement.
Leurs objets, aperçus, contenus
HTML, versions texte et signatures sont en français. Le rendu utilise `fr-fr`
uniquement pendant la préparation de l’e-mail, indépendamment de la langue de
l’interface ou du destinataire. Il ne modifie pas `LANGUAGE_CODE` ni la préférence
de langue du compte. Le fuseau horaire du destinataire reste utilisé pour
l’enregistrement. Les invitations planifiées conservent le fuseau de la réunion
et les choix distincts concernant la vidéo et la transcription audio.

Les sources des invitations en cours et des notifications sont dans
`src/mail/mjml/` ; celles des invitations planifiées sont dans
`src/backend/core/templates/invitations/`. La commande `npm run build` dans `src/mail`
génère les versions HTML et texte Django. Ces fichiers générés sont ignorés par Git
et doivent être reconstruits dans l’environnement de test ou l’image applicative.

## Paramètres

Les paramètres Django ci-dessous utilisent le préfixe `DJANGO_` dans
l’environnement, sauf `APPLICATION_BASE_URL`.

| Paramètre | Valeur par défaut ou usage |
| --- | --- |
| `DJANGO_EMAIL_FROM` | `notifications@mastrao.com` en production ; utiliser `notifications@mastrao-staging.com` en staging |
| `DJANGO_EMAIL_BRAND_NAME` | `Mastrao Visio` |
| `APPLICATION_BASE_URL` | URL publique du frontend de cette instance, à fournir par le déploiement |
| `DJANGO_EMAIL_APP_BASE_URL` | Reprend `APPLICATION_BASE_URL` si non renseigné ; une valeur explicite est conservée |
| `DJANGO_EMAIL_DOMAIN` | Hôte déduit de l’URL des e-mails si non renseigné |
| `DJANGO_EMAIL_LOGO_IMG` | URL des e-mails suivie de `/assets/mastrao-logo-icon.png` si non renseigné |
| `DJANGO_EMAIL_SUPPORT_EMAIL` | Facultatif ; renseigner uniquement une adresse d’assistance confirmée |
| `RECORDING_DOWNLOAD_BASE_URL` | URL publique de la page `/recording` du frontend, utilisée pour les liens d’enregistrement |

`EMAIL_FROM` est transmis tel quel au moteur d’envoi. Fournir l’adresse seule,
sans nom d’affichage ni chevrons. Aucune fonction ne construit de nom d’expéditeur.
Ne pas utiliser l’adresse de notification comme adresse d’assistance sans avoir
confirmé qu’elle reçoit et traite les demandes.

Les exemples Compose sont dans `env.d/production.dist/common` et
`env.d/development/common.dist`. Les overlays Helm de production et de staging
indiquent chacun leur adresse d’expéditeur. Ils doivent être examinés avec les
paramètres fournis par le déploiement : aucune valeur réellement déployée n’a été
vérifiée pendant cette correction.

## Documentation, assistance et informations légales

`FRONTEND_DOCUMENTATION_URL` permet de fournir la documentation de l’instance,
notamment depuis l’erreur de partage d’écran. Sans URL configurée, aucun lien d’aide
externe n’est affiché dans cette erreur. Les identifiants Crisp et liens de feedback
DINUM ne sont plus fournis par le preset commun.

Les pages d’accessibilité, de mentions légales et d’utilisation identifient Mastrao
Visio. Les coordonnées légales de l’éditeur, le responsable de publication,
l’hébergement et les résultats d’audit restent à confirmer et à renseigner par
l’équipe Mastrao. Les adresses, certifications et garanties du service DINUM ne
sont pas reprises comme informations de l’instance Mastrao. L’origine Meet / La
Suite Numérique, les copyrights et les licences du code restent attribués.

L’add-on Outlook n’a pas été modifié : sa distribution actuelle n’a pas été
confirmée. Le preset explicitement nommé `dev-dinum` reste un exemple de thème
amont distinct.
