# Migrations are tracked in git and applied when the django container starts

Deploying must be nothing more than `git pull && docker compose up -d --build` on the server. Before this, `api` migrations were not committed: production generated them inside its container. That filesystem is discarded on every image rebuild, so the history production had applied (`api.0001_initial`, July 2025) no longer existed anywhere, and any model change would have needed manual steps on the server.

We now commit migrations with the model change, and the django service runs `manage.py migrate --noinput` before starting gunicorn. The committed baseline `0001_initial` was regenerated from the current models, which match the production schema field for field (checked against a prod snapshot). Production already records `api.0001_initial` as applied, so the baseline is a no-op there and later migrations apply normally.

## Considered options

- **Keep generating migrations on the server.** Rejected: they are lost on rebuild, and production's schema history becomes unreproducible.
- **Run `migrate` as a manual deploy step.** Rejected: an extra step that's easy to forget, and the point is a deploy with no steps.
- **`migrate --fake-initial` on start.** Not needed, because production already records `0001_initial`. It would also hide a missing table instead of failing loudly.

## Consequences

- A migration that fails stops the django container from starting, and the site is down until it's fixed. That's preferable to running code against a schema it doesn't match.
- Two branches that both change models will produce conflicting migration numbers. Model-changing issues are therefore merged into `dev` one at a time (see `docs/agents/workflow.md`).
