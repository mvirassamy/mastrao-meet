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
derives that connection from Django's default database configuration; therefore
the default route must not be changed to transaction pooling until this
convergence connection has an explicit direct configuration and qualification.

## Maintenance route

Schema migrations, extension creation, and administrative jobs must use the
direct PostgreSQL route. The Helm `backend-migrate` Job is the migration entry
point. The raw-SQL migrations are `0002_create_pg_trgm_extension`,
`0049_subtitle_advisory_key` (sequence), and `0055`/`0056` (ASR queue-metrics
view). The cleanup CronJobs above are ordinary application work and may use the
transaction-pool route once the direct-session exceptions are wired.

No migration or maintenance routing is changed by this preparation.

## Rollback

This preparation changes no schema and creates no operational resource. Revert
its commit to remove the regression proof and deployment contract. If a future
deployment routes traffic through PgBouncer, remove that routing before
re-enabling server-side cursors or an explicit Psycopg prepare threshold.
