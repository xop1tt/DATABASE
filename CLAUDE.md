# Claude Code Instructions — flamenco-db

Schema of the Flamenco Studio PostgreSQL database. The only place where the
schema changes. The bot and web API (`flamenco-studio-bot`, local folder
`TG BOT`) and the website (through the API) use this database but never apply
migrations; on startup they check `REQUIRED_SCHEMA_VERSION`
(`src/flamenco_bot/database/repository.py` in the bot repository).

## Rules

- Never edit an applied migration. Every change is a new file
  `migrations/NNN_description.sql` with the next number.
- Write migrations without a schema name: the role's `search_path` selects
  the schema (`flamenco` in Supabase, a temporary schema in tests).
- Preserve foreign keys, indexes and constraints; account for existing data.
  Prefer additive changes that the currently deployed bot still works with —
  the database is deployed before the bot.
- Booking capacity, payments and the credit ledger are business-critical:
  read the existing constraints and triggers before changing those tables.
- After adding a migration: run `python -m unittest discover -s tests -v`
  with `TEST_DATABASE_URL`, then the bot's integration tests
  (`FLAMENCO_DB_DIR` pointing here), and raise `REQUIRED_SCHEMA_VERSION` in
  the bot if its code needs the new schema.
- Never put credentials in the repository. `DATABASE_URL` for production lives
  only in the GitHub environment `production`.
- Do not commit or push unless explicitly asked.
