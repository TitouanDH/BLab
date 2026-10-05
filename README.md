# Blab — Remote Lab, Reservation & Topology Visualization

Blab is a production-oriented remote lab platform that lets you reserve, link and operate real network devices from your desk. It provides a GNS3-like graphical workspace for physical hardware with automation for onboarding, configuration and port discovery — all runnable via Docker Compose.

<!-- Top screenshots side-by-side (table for GitHub compatibility) -->
<table>
  <tr>
    <td>
      <img src="https://github.com/user-attachments/assets/7f0a0162-1a64-45fb-b0a8-3ef2d188a92a" alt="Main UI screenshot" width="100%" />
    </td>
    <td>
      <img src="https://github.com/user-attachments/assets/bdf540a0-98af-41c4-bd8a-492c136ae7b4" alt="Topology view screenshot" width="100%" />
    </td>
  </tr>
  </table>

## Features
- Real-hardware focus: operate and test physical switches (not just simulated devices).
- GNS3-like GUI: visually create topologies, draw links and inspect live connections.
- End-to-end automation: from discovering devices to creating SPB/QinQ tunnels through a backbone.
- DevOps-friendly: API-first design so CI/CD and automation scripts can drive real equipment.
<img src="https://github.com/user-attachments/assets/01027936-88ce-45ea-81f1-99ef3081e652" alt="Reserve flow GIF" width="800" />

<img src="https://github.com/user-attachments/assets/4fc9dfad-4728-40c6-bdaa-127951a3757a" alt="Link creation GIF" width="800" />

<img src="https://github.com/user-attachments/assets/469e228b-4d85-47fa-a794-6d728febf932" alt="Share topologies" width="800" />

## Core capabilities
- Remote reservation: claim physical switches for exclusive sessions (DB-backed reservations).
- Topology editor & visualization: interactive graph showing switches, ports and tunnel links.
- Automated switch onboarding: prepare/format devices and generate vcboot.cfg automatically.
- Port discovery: LLDP parsers populate port mappings and backbone relationships.
- Tunnel orchestration: API triggers create SPB/QinQ tunnels on backbone and access switches using Paramiko or vendor APIs.
- Containerized stack: backend, frontend and DB orchestrated with Docker Compose.

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

## Quickstart (Docker)
1. Clone:
  ```bash
  git clone <repository-url>
  cd Blab/app
  ```
2. Start services:
  ```bash
  docker-compose up --build -d
  ```
3. Migrate and create admin:
  ```bash
  docker-compose exec django python manage.py migrate
  docker-compose exec django python manage.py createsuperuser
  ```
4. Populate and prepare (examples):
  ```bash
  docker-compose exec django python manage.py populate_switches --file switch_ips.txt
  docker-compose exec django python manage.py prepare_switches --file switch_ips.txt --reload
  docker-compose exec django python manage.py populate_ports
  ```

## Local development

Dev runs Django and Vite natively on your machine. It never touches production:

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

In a second terminal, run the frontend (Vite proxies `/api` to Django):

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

Migrations are not tracked in git (production generates them in its container).
When changing models, run `makemigrations` and `migrate` against the local snapshot only.

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
