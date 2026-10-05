## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (TitouanDH/BLab) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Workflow

Each issue gets its own worktree and is squash-merged into `dev` without asking. Titouan only reviews the standing `dev` → `main` PR. `main` must always deploy with `git pull && docker compose up -d --build`. See `docs/agents/workflow.md`.

### Working agreement

All follow-up goes through GitHub issues. Work as autonomously as possible: pick up issues, implement, and report progress and results as issue comments. Only when a decision or information genuinely needs Titouan, comment the specific question on the issue and apply the `needs-info` label (remove it once answered).
