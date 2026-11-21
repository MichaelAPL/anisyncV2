# Coding standards

Rules code in this repository follows. Every change is reviewed against them. Things ruff, CI or an existing test
already enforce (SQL built from strings, formatting, the public-route list, response schemas, naming a provider outside
`providers/`, admin permission dependencies) are not repeated here.

## Provider HTTP

- All outgoing HTTP goes through `anisync.http.client`, which sends a named User-Agent and a default timeout; never
  build an `httpx.AsyncClient` directly. Some providers sit behind bot protection that refuses the library's default
  User-Agent. (30d4265)
- Only reads (GET/HEAD) are retried, and only on network-level errors. Writes are never repeated inside a request: a
  change that fails is planned again by the next sync, which is safer than risking it being applied twice. (dccecdd)
- Provider failures reach the user as honest status codes: 429 with the retry time when rate limited, 502 when the
  provider refused or blocked us, never a generic 500. (9e27d72, 30d4265)

## Sync runs and database sessions

- Every code path that opens a `SyncRun` closes it as an error on any exception, `BaseException` included. A run left
  "running" reads as in progress on the dashboard and in admin. (9e27d72, e5383c6, dccecdd)
- Read anything a cleanup path needs (ids, attributes) before work that can roll back. A rollback expires ORM objects,
  and reading one afterwards is I/O that can crash the cleanup itself. (dccecdd)
- The server enforces its own invariants (one sync at a time per user answers 409); a disabled button in the UI is not
  enough. (9e27d72)
- A change to how a provider client computes a value that list snapshots store (an entry's flags, normalized fields
  or IDs) ships with a migration that clears `linked_accounts.last_full_read_at` for the affected provider. Snapshots
  are only replaced by a full read, so without it every stored entry keeps the old value until that account's next
  scheduled full read, and incremental reads only refresh the entries updated since the cursor. (1d0fc8c, where the
  dev database had to be reset by hand; same mechanism as turning `ignore_adult_titles` off in `api/routes.py`)

## Data and deletion

- Removing an account or a user also removes rows keyed by user and provider, or that name its titles (sync run events,
  unmatched-title rows), not only rows with a foreign key that cascades. (4c0dc01)
- A title missing from a provider's list is not proof it was deleted. Titles a provider hides (adult filters) and
  entries that could be the twin of an unmapped master entry never cause a mirror entry to be pruned.
  (e8687b5, c533eab; AGENTS.md "Prune safety")
- Scope every query to `user.id`; another user's resource is a 404. (AGENTS.md)
- Never log request bodies or headers. Never log or persist provider passwords or raw tokens; tokens only go through
  the encrypted `LinkedAccount` properties. (AGENTS.md)
- Rate-limit every route that accepts credentials (`ratelimit.per_ip` / `per_user`). (AGENTS.md)
- Every schema change ships with an Alembic migration. (AGENTS.md)

## Structure

- `engine.py` stays pure: no I/O. I/O belongs in `sync.py` and the provider clients. (AGENTS.md)

## Frontend

- Busy and disabled states come from server state (for example the list of runs), not from what the current tab did.
  Another tab, the scheduler or an admin can start the same work. (945dd0b)

## Tests

- Every client parse/serialize change gets a respx test (prefer recorded fixtures), every engine rule a unit or
  Hypothesis test, and every DB/API behaviour a test against the `anisync_test` database. The test suite never calls
  real provider APIs. (AGENTS.md; ROADMAP → Testing strategy)

## Maintaining this file

A defect class enters this file only when it appeared in a review finding the author fixed, a fix commit, a cleanup
that removed it across the codebase, or a solution record in `docs/solutions/`, and no check covers it. State it as a
condition that stays true without naming volatile numbers or symbols, cite where it came from, and group it by area.
When a check starts enforcing a rule, or the code it guarded is gone, remove the rule.
