# BLab — Physical network lab, reservations & topologies

BLab lets teams reserve real lab switches, connect them through the lab backbone and manage their work together in a shared project. A web interface handles reservations, topology editing and lab health; the API and workers apply and reconcile changes on the equipment.

## Features

- **Reserve real hardware:** find available switches, reserve them for up to two weeks, renew twice, then release them when the work is done. Reservations can be grouped into a project.
- **Work together in projects:** invite members to a project, share its switches and topology, and manage its members and saved configurations.
- **Build topologies visually:** connect switch ports and lab tools with links carried across the backbone. The project canvas shows link and switch states, and its layout is shared with project members.
- **Recover project state:** save and restore snapshots of switch configurations, software releases, links and topology layout. BLab also takes a snapshot before a switch is released.
- **Keep the lab healthy:** inspect switches, review findings and quarantine, re-check after repairs, and contribute a switch or backbone cable through guided checks.
- **Manage switch access:** BLab provisions per-user switch accounts for project members and removes them when access ends.
- **Automate operations:** use the Django REST API and background workers alongside the Vue web app; Docker Compose runs the stack.

## Interface

These current interface captures use demo data.

<table>
  <tr>
    <td><img src="docs/images/readme-projects.png" alt="Projects list with topology previews and project members" width="100%" /><p align="center"><strong>Projects</strong></p></td>
    <td><img src="docs/images/readme-project-topology.png" alt="Shared project topology with switches and labeled links" width="100%" /><p align="center"><strong>Shared topology</strong></p></td>
  </tr>
  <tr>
    <td><img src="docs/images/readme-my-lab.png" alt="My lab page with project reservations and switch access" width="100%" /><p align="center"><strong>My lab</strong></p></td>
    <td><img src="docs/images/readme-lab-status.png" alt="Lab status with inspection and quarantine findings" width="100%" /><p align="center"><strong>Lab status</strong></p></td>
  </tr>
</table>

## Architecture (short)
- Backend: Django REST API, Paramiko (SSH) + optional vendor APIs to apply CLI changes.
- Database: Postgres (stores switches, ports, reservations, topology/shares).
- Frontend: web GUI to visualize topology and trigger API operations.
- Control plane: management commands (populate_switches, prepare_switches, populate_ports) and API endpoints (reserve, release, connect, disconnect, share_topology).

## How it works (high level)
1. Populate inventory: run `populate_switches` to discover chassis info and seed the DB.
2. Prepare devices: `prepare_switches` sets init/working files and can apply configurations (reboot to activate LLDP).
3. Discover ports: `populate_ports` enables backbone LLDP briefly, reads LLDP from access switches and creates Port DB entries.
4. GUI-driven operations: The frontend GUI performs authentication and all user actions (reserve, release, connect, disconnect, cleanup) by calling the backend API. The API endpoints remain available for developers and automation scripts who prefer direct integration.
5. Visualize: frontend shows reservations and live topology; disconnect/release operations reverse changes and update DB state.
6. Inspect: `inspect_switches` reads every Switch (show commands, and its init config over SFTP; it never writes) to check it is clean (BLab can log in, no VC, no Unwanted cable), records the result in its history, and prints a report. Everyone sees the results on the Lab status page. Ports cabled on purpose are marked in the Django admin (Permanent cables, on the Switch page).

## Quickstart (Docker)
1. Clone:
  ```bash
  git clone <repository-url>
  cd Blab/app
  ```
2. Create `.env` next to `docker-compose.yml` from `.env.example` (secret key, DB password,
   TLS directory holding `cert.crt` and `cert.key`).
3. Start services (the django container applies migrations on start):
  ```bash
  docker compose up -d --build
  ```
4. Create an admin:
  ```bash
  docker compose exec django python manage.py createsuperuser
  ```
5. Populate and prepare (examples):
  ```bash
  docker-compose exec django python manage.py populate_switches --file switch_ips.txt
  docker-compose exec django python manage.py prepare_switches --file switch_ips.txt --reload
  docker-compose exec django python manage.py populate_ports
  ```

## Local development

Local dev runs Django and Vite natively on your machine. It never touches production:

- **Database**: a local Postgres (`docker-compose.dev.yml`, on `127.0.0.1:5433`) loaded
  with a snapshot of the production database. Production is only read, to take the dump.
- **Lab equipment**: `BLAB_DEVICES=fake` (the default) sends backbone CLI and switch SSH
  to an in-memory stand-in (`api/api/fake_devices.py`). Connect, disconnect, reserve and
  release behave normally but configure nothing. The populate/prepare management
  commands refuse to run in fake mode. Production sets `BLAB_DEVICES=real`.

Take or refresh the snapshot (needs Docker Desktop running and SSH access to the prod host):

```powershell
.\scripts\pull-prod-db.ps1 -SshTarget <user>@10.69.144.180
```

The dump is kept in `.local/` (gitignored: it contains real users and tokens).
`-SkipDownload` restores the last dump again, e.g. after a test run messed up the data.

Run the API, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
. .\scripts\dev-env.ps1
Set-Location api
python manage.py runserver 127.0.0.1:8000
```

Disconnects are carried out by the Link worker (docs/adr/0003), and the Cleanup after a
Release by the Switch worker (docs/adr/0005), so run them too, each in its own terminal set
up the same way:

```powershell
python manage.py link_worker
python manage.py switch_worker
```

In another terminal, run the frontend (Vite proxies `/api` to Django):

```powershell
Set-Location frontend
npm install
npm run dev
```

The frontend is at `http://localhost:5173/`, the API at `http://127.0.0.1:8000/api/`.

Tests use SQLite and fake devices; no database or equipment needed:

```powershell
Set-Location api
$env:DB_ENGINE = "sqlite3"
python manage.py test api
```

The SSH commands for banners, Cleanup and Switch accounts can be checked against a real
switch with an opt-in contract test. The Cleanup part reboots the switch, so it only runs on
a standalone one that is not in the lab, not on the backbone and not in `switch_ips.txt`; the
Switch account part (create a login, use it, remove it) also runs on a lab Switch reserved
for the test. Put `BLAB_TEST_SWITCH`, `BLAB_TEST_SWITCH_USER` and `BLAB_TEST_SWITCH_PASSWORD`
in a `.env.test` at the repository root (git-ignored), then run it by name; it never runs
with the suite above:

```powershell
python manage.py test api.contract_lab_switch
```

Migrations are tracked in git. When changing models, run `makemigrations`, apply it to the
local snapshot with `migrate`, and commit the migration file with the model change.
Keep it additive (new tables, nullable columns or `db_default`): pre-prod applies it to the
database production shares (see Deploy). `python manage.py check_shared_db_migrations`
checks this.

## Deploy

Two deployments run on the server, each redeploying itself within a minute of a push:

| Branch | Deployment | URL                          | Used by          |
| ------ | ---------- | ---------------------------- | ---------------- |
| `dev`  | Pre-prod   | `https://10.69.144.180:8443` | the maintainer   |
| `main` | Production | `https://10.69.144.180`      | everyone         |

Both use the same database and the real switches. Pre-prod shows a banner with its commit.
A cron job runs `scripts/deploy.sh` in each checkout: it pulls, runs
`docker compose up -d --build` (migrations apply on start), and posts the result as the
GitHub commit status `deploy/preprod` or `deploy/production`. The server-only settings are
described in `.env.example`.

How changes move from an issue to production: [docs/agents/workflow.md](docs/agents/workflow.md).
Why pre-prod shares the database: [docs/adr/0002](docs/adr/0002-preprod-shares-production-database.md).

## Example API calls
- Reserve a switch:
  ```bash
  curl -X POST http://<api-host>/api/reserve/ -H "Authorization: Token <token>" \
    -H "Content-Type: application/json" \
    -d '{"switch": 3, "end_date": "2025-12-31T23:59:00Z"}'
  ```
- Create a tunnel (connect two ports):
  ```bash
  curl -X POST http://<api-host>/api/connect/ -H "Authorization: Token <token>" \
    -H "Content-Type: application/json" \
    -d '{"portA": 12, "portB": 34}'
  ```

## Developer notes
- Management commands are idempotent-friendly; use `--update` when re-running population.
- Tunnel creation uses Paramiko to run CLI on devices or an internal backbone API when available.
- LLDP-based discovery requires applying prepared config and a reboot on switches for LLDP to be active.
- All long-running operations should be orchestrated (or queued) from the API in production.

## Contributing & license
- Contributions welcome — open issues or PRs.
- MIT License — see LICENSE.
