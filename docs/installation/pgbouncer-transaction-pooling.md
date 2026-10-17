# PgBouncer transaction pooling contract

This document records the application constraints for a future PgBouncer
transaction-pooling rollout. It does not deploy PgBouncer, set a database URL,
or introduce credentials.

## Transaction-pool route

The normal Django connection is the candidate transaction-pool route after a
direct-session route has been configured for the exceptions below. This route
will apply to:

- the Gunicorn API pods;
- Celery workers, including the native admission and ASR queues;
- CronJobs that perform ordinary application work.

The Django configuration disables server-side cursors for both `DATABASE_URL`
and the `DB_*` configuration path. Django 5.2 also passes
`prepare_threshold=None` to Psycopg 3 unless an operator explicitly overrides
it in `OPTIONS`. Both behaviors prevent client-side state from relying on one
PostgreSQL session when a transaction-pooling proxy assigns a different server
session to each transaction.

The exact iterator users are compatible with this route because server-side
cursors stay disabled:

- `clean_pending_files` and `purge_deleted_files` maintenance commands;
- the native-ASR Celery worker.

`pg_advisory_xact_lock` in room creation and native-ASR capacity claiming is
transaction-scoped and runs inside `transaction.atomic()`. It stays on this
route.

## Direct-session route

Subtitle-agent convergence uses `pg_try_advisory_lock` and
`pg_advisory_unlock` on one Psycopg connection. These are session-scoped
locks, so this path must keep a direct PostgreSQL route. The current code
opens one dedicated Psycopg connection and uses it for both acquisition and
unlock, including when the provider raises an error.

`ROOM_SUBTITLE_LOCK_DATABASE_URL` is the optional PostgreSQL connection string
for this dedicated session. The operator must supply it through the existing
protected configuration mechanism. No endpoint, credential, Secret, or Helm
value is supplied by this preparation.

- When unset or empty, the lock keeps using Django's default connection
  parameters, preserving the existing direct route. Django's `DATABASES`
  configuration is unchanged whether this setting is present or absent.
- When set, the lock uses the explicit connection string, including its TLS
  parameters, without inheriting Django's connection parameters. An invalid
  string or a failed connection fails lock acquisition; it does not fall back
  to Django's route.
- The existing per-process connection cap, acquisition timeout, connection
  timeout, and statement timeout still apply. The connection uses autocommit,
  remains open during convergence, and closes after unlock.

Before routing Django through transaction pooling, qualify this setting against
the same PostgreSQL primary and database used by Django, with access to the
same advisory-key registry and lock namespace. Account for the dedicated
sessions in the connection budget. Maintenance routing must also be qualified
separately. Setting this URL alone does not qualify or deploy PgBouncer.

The regression tests observe PostgreSQL backend PIDs and advisory-lock ownership
to prove that acquisition and unlock share one session on both routes, on
normal completion and after a provider error. They also cover invalid and
unavailable explicit routes without fallback and unchanged Django settings
for both `DATABASE_URL` and `DB_*` configuration.

To roll back the explicit lock route, first confirm Django's default route is
direct PostgreSQL, then remove `ROOM_SUBTITLE_LOCK_DATABASE_URL` through the
same configuration authority. Do not remove it while the default route uses
transaction pooling.

## Maintenance route

Schema migrations, extension creation, and administrative jobs must use the
direct PostgreSQL route. The Helm `backend-migrate` Job is the migration entry
point. The raw-SQL migrations are `0002_create_pg_trgm_extension`,
`0049_subtitle_advisory_key` (sequence), and `0055`/`0056` (ASR queue-metrics
view). The cleanup CronJobs above are ordinary application work and may use the
transaction-pool route once the direct-session exceptions are wired.

No migration or maintenance routing is changed by this preparation.

## Staging Gate 1 database receipt

Gate 1 is a passive staging qualification. It does not deploy PgBouncer,
change an application setting, create a connection storm, run a migration, or
write to PostgreSQL. Do not run it until the Gate 1 owner has recorded that
the required functional batches are merged and deployed to the selected staging
revision.

### Operator prerequisites

Before the readback, the operator records:

- the staging environment, namespace, application revision, and the deployed
  revisions of the functional batches required by Gate 1;
- a direct PostgreSQL endpoint for the selected staging database, its TLS
  requirements, and the database name; this receipt must not use or configure
  a pooler;
- an operator-held application-role manifest. Each entry names one existing,
  deployed Meet database role, its application connection allocation, and its
  direct connection-string source. The allocations must sum to the documented
  23-connection application budget. The repository contains only example
  `DB_USER` values, so it is not an authority for staging role names;
- one direct, short-lived probe connection at a time. Run it sequentially for
  the manifest entries, authenticating as one application role before closing
  it and proceeding to the next. Use each role's existing privileges and role
  memberships unchanged: do not add, remove, or modify a privilege or
  membership for this receipt;
- read-only access to the PostgreSQL logs or managed-service metrics for the
  same time window. SQL counters alone cannot attribute a fatal session to
  capacity exhaustion;
- a protected receipt location. Redact endpoint, client address, role name,
  database name, and all query text before sharing it outside the operator
  boundary.

The repository never receives the connection string, certificate, password, or
the unredacted readback.

### Read-only readbacks

Run the query set once for every manifest entry through a fresh operator shell,
with exactly one probe connection open at a time. Close `psql` before opening
the probe connection for the next role. The connection string remains only in
that shell. `PGOPTIONS` makes every implicit transaction read-only and limits
the diagnostic session. Together with the `SELECT` statements below, it keeps
the readback passive without changing an existing privilege or membership.

```sh
export PGAPPNAME='meet-db-gate1-readback'
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=5s -c lock_timeout=1s -c idle_in_transaction_session_timeout=5s'
export GATE1_EXPECTED_ROLE='<operator manifest value>'
export GATE1_APPLICATION_BUDGET='<operator manifest value>'
export GATE1_REQUIRED_CONNECTION_LIMIT='<application budget plus one readback slot>'
psql -X -v ON_ERROR_STOP=1 \
  -v expected_role="$GATE1_EXPECTED_ROLE" \
  -v application_budget="$GATE1_APPLICATION_BUDGET" \
  -v required_connection_limit="$GATE1_REQUIRED_CONNECTION_LIMIT" \
  "$STAGING_MEET_APPLICATION_DATABASE_URL"
```

The only PostgreSQL views read are `pg_roles`, `pg_stat_activity`, and
`pg_stat_database`. `pg_stat_activity` is restricted to `current_user`, so an
application role sees only its own sessions. Configuration readbacks use the
six named `current_setting()` parameters below. Do not grant a predefined
global monitoring role to widen this receipt.

In `psql`, save the redacted output of these queries as the first snapshot,
with a UTC timestamp.

```sql
SELECT current_database() AS database_name,
       current_user AS observed_role,
       :'expected_role' AS expected_role,
       current_user = :'expected_role' AS expected_role_matches,
       :'application_budget'::integer AS application_budget,
       :'required_connection_limit'::integer AS required_connection_limit,
       :'required_connection_limit'::integer
           = :'application_budget'::integer + 1 AS required_budget_matches,
       current_setting('server_version_num') AS server_version_num,
       current_setting('transaction_read_only') AS transaction_read_only;

SELECT current_setting('max_connections')::integer AS max_connections,
       COALESCE(NULLIF(current_setting('reserved_connections', true), ''), '0')::integer
           AS reserved_connections,
       current_setting('superuser_reserved_connections')::integer
           AS superuser_reserved_connections,
       current_setting('idle_in_transaction_session_timeout')
           AS idle_in_transaction_session_timeout;

SELECT rolname,
       rolconnlimit AS current_role_connection_limit,
       :'required_connection_limit'::integer AS required_connection_limit
FROM pg_roles
WHERE rolname = current_user;

SELECT clock_timestamp() AS snapshot_at,
       usename,
       application_name,
       state,
       count(*) AS connections
FROM pg_stat_activity
WHERE datname = current_database()
  AND usename = current_user
  AND backend_type = 'client backend'
GROUP BY usename, application_name, state
ORDER BY usename, application_name, state;

SELECT clock_timestamp() AS snapshot_at,
       usename,
       application_name,
       state,
       count(*) AS sessions,
       max(now() - state_change) AS oldest_idle_duration
FROM pg_stat_activity
WHERE datname = current_database()
  AND usename = current_user
  AND backend_type = 'client backend'
  AND state IN ('idle in transaction', 'idle in transaction (aborted)')
GROUP BY usename, application_name, state
ORDER BY oldest_idle_duration DESC;

SELECT clock_timestamp() AS snapshot_at,
       datname,
       numbackends,
       sessions_fatal,
       sessions_abandoned,
       stats_reset
FROM pg_stat_database
WHERE datname = current_database();
```

After a ten-minute passive observation window, rerun the two
`pg_stat_activity` queries and the `pg_stat_database` query above for every
manifest entry as the second snapshot. Compare the first and second
idle-in-transaction result sets for each application role. During the window,
do not generate application traffic or deliberately exhaust connections. The
operator also records a read-only log or metric query for that window. It must
show zero matches for PostgreSQL capacity refusals, including `too many
clients`, `too many connections for role`, and `remaining connection slots are
reserved`.

### Stop thresholds

Stop and mark the receipt `NOT_QUALIFIED` without changing staging if any item
below is true:

- the functional-batch deployment evidence, direct endpoint, or required
  read-only privilege is missing;
- `transaction_read_only` is not `on`, or the database identity does not match
  the selected staging target;
- `max_connections - reserved_connections - superuser_reserved_connections`
  is below 28. The global normal-slot budget is 23 application allocations,
  one sequential probe connection, and four slots reserved for platform,
  monitoring, and an administrator: `23 + 1 + 4 = 28`;
- completing the receipt would require creating or revoking a role, or
  granting, revoking, or modifying a privilege or role membership. Use only
  the existing deployed roles in the manifest;
- an observed role differs from its manifest entry, the manifest allocations
  do not sum to 23, `required_budget_matches` is false, or its finite
  `current_role_connection_limit` is below its `required_connection_limit`;
- either idle-in-transaction snapshot is nonempty, contains a session older
  than 60 seconds, or differs from the other snapshot for the same role;
- `stats_reset` differs between the first and second `pg_stat_database`
  snapshots. Restart the full ten-minute observation after recording the new
  baseline;
- `sessions_fatal` increases during the window, or the log/metric readback
  shows a capacity refusal;
- a required catalog row or current-role session snapshot is unavailable.

The receipt is `QUALIFIED_READ_ONLY` only when every stop threshold is clear.
It proves only the recorded staging baseline; it does not authorize a pooler
deployment or application traffic change.

### Receipt and rollback

The operator receipt contains the source/deployed revisions, the redacted
application-role manifest, UTC start and end, both snapshots for every role,
the two `pg_stat_database` snapshots and unchanged `stats_reset` values, the
redacted log or metric result, each threshold decision, and
`QUALIFIED_READ_ONLY` or `NOT_QUALIFIED`.

There is no data-plane rollback for this procedure: close `psql`, unset
`PGOPTIONS`, and discard an incomplete receipt. Do not reuse its baseline. The
receipt uses only existing deployed roles; a need to create or revoke a role,
or to grant, revoke, or modify a privilege or role membership, is
`NOT_QUALIFIED`.

## Rollback

This preparation changes no schema and creates no operational resource. Revert
its commit to remove the regression proof and deployment contract. If a future
deployment routes traffic through PgBouncer, remove that routing before
re-enabling server-side cursors or an explicit Psycopg prepare threshold.
