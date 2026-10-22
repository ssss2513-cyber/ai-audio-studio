# DigitalOcean persistent state

App Platform's local disk is ephemeral. Spaces holds videos, not the member,
playlist, upload-session or analysis databases. Do not onboard users on local
SQLite in App Platform.

## Runtime configuration

Attach a PostgreSQL database, retaining the existing Spaces variables. Set:

* `CLASSROOM_DATABASE_URL`: the database connection URL, encrypted, runtime only.
  Use the provider's TLS-enabled URL (`sslmode=require` or stronger).
* `CLASSROOM_REQUIRE_DATABASE=true`: runtime only. Missing/broken PostgreSQL must
  fail startup rather than silently create a blank local member database.

`DATABASE_URL` is accepted if the platform already binds this variable.
The existing `PUBLIC_ORIGIN` must remain the exact HTTPS website origin.
`requirements-cloud.txt` now installs psycopg; keep the existing build command.

PostgreSQL schemas `classroom` and `lecture_ai` isolate their tables. The database
role must be able to create schemas, tables, indexes and sequences in this DB.
The identity/encryption key is initialized once in `classroom.app_secrets` and
reused by CLI commands and replacement containers. If `CLASSROOM_DATA_KEY` is
provided it must match the saved key. Back up the whole database, including this
table; restrict all database access. No credentials belong in Git or screenshots.

## Cutover

1. Preserve/export any surviving old SQLite files and `secret.key` before another
   deployment. This patch does not recover data already lost on an old container,
   or automatically import SQLite into PostgreSQL. Do not assume an old instance
   still contains the administrator just because a browser remains signed in.
2. Provision and bind the persistent database and runtime variables together.
3. Deploy the tested code. `/api/health` must report `database: postgresql`.
4. Create the administrator with `python manage.py create-admin` in the Console.
   Use a new password; do not send it in chat.
5. Verify signup, pending/approved access, one PC video upload and playback.
6. Redeploy and verify that the same admin login, member approvals, video records,
   progress and lecture evidence still exist. Only then upload the remaining videos.

Source video bytes are uploaded directly to Spaces. Temporary video clips, local
posters, unfinished local-disk uploads and local caches remain ephemeral; do not
use the legacy server-disk upload path for production video storage.
Leave `VIDEO_MIGRATION_ENABLED` off for this PC-upload workflow; its legacy SQLite
backup step is not a PostgreSQL backup. Use managed database backups for PostgreSQL.

## Verification

`pytest -q tests/test_persistent_db.py` includes a two-process, two-directory
replacement-container test when `TEST_POSTGRES_URL` points to an isolated test
database. Never point tests at production. The CI workflow provisions PostgreSQL
16 and checks account permissions, multi-GiB video metadata, retained encryption
keys, playback progress and saved lecture evidence/search across replacement.
