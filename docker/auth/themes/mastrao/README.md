# Mastrao login theme

Inherits Keycloak 20.0.1 styles and translations. `login/login.ftl` comes from that version's base theme, with a return link added before the form. Reconcile this override when upgrading Keycloak.

The return link uses the client's configured **Home URL** (`baseUrl`), rather than browser history (which may loop back into authentication). Set it to the frontend origin for each environment. The development realm defaults to `http://localhost:3000/`; the isolated design preview uses `http://localhost:5197/` in its running realm.

For an existing realm, importing realm.json does not update settings: select the `mastrao` login theme and configure the Meet client's Home URL in Keycloak. Mount this theme directory at `/opt/keycloak/themes/mastrao`.
