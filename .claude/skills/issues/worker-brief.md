# Worker brief

The orchestrator fills the `<…>` slots and sends the result as the worker's prompt. Paste the decisions in full: the worker cannot see this conversation.

---

You are the worker for issue #<n> "<title>" in TitouanDH/BLab (repo at `C:\Users\tdhallui\Code\BLab`). An orchestrator runs several workers in parallel and serialises merges; you own this one issue.

**Issue**: read it with `gh issue view <n> --comments`.

**Decisions already made** (follow these; they override anything open in the issue):
<bullet list: decisions from Titouan and the orchestrator's defaults, each with its reason>

**Model issue**: <yes/no>. <If no: should you find you need a migration, say so in your report.>

**Rules**: read `docs/agents/workflow.md`, `CONTEXT.md`, and only the ADRs in `docs/adr/` that bear on this issue. Follow workflow.md § *Per issue* with worktree `.claude/worktrees/<n>-<slug>` and branch `issue/<n>-<slug>`.

**Known pitfalls**:
- Run the gate on Python 3.10 with the shared `C:\Users\tdhallui\Code\BLab\.venv310` (Dockerfile and CI use 3.10; the main `.venv` is 3.14). Do not create a venv of your own. Also check CI on the `dev` head.
- Merge with `git merge --squash` onto a fresh `origin/dev`, then check `git show --stat`: a stale squash has reverted other issues' files before.
- Device access is read-only. Writes to real switches need Titouan's written approval; the test switch .132 is writable for tests.
- Local admin smoke runs need `DJANGO_DEBUG=1` and a CSRF trusted origin, or admin login returns 403.
- `deploy/preprod` usually takes ~1.5 min but can take 30+; wait up to ~45 min before calling it failed. SSH to the server is denied to agents.
<add any other memory notes that bear on this issue>

**Budget**: stay within 200k tokens. Read excerpts and use targeted test runs while iterating; run the full gate once at the end. Run the `code-review` step yourself, both axes (standards, spec) in your own window, without spawning agents.

**Phase 1 — build**: worktree, implement, gate (workflow.md steps 1–3). Then **stop** and report `ready-to-merge`. Do not merge: the orchestrator resumes you with `Merge now.` when it is your turn.

**Phase 2 — merge** (on `Merge now.`): workflow.md steps 4–6 — rebase on the latest `dev` (regenerate your migration if its number now collides), rerun the gate, squash-merge as `<title> (#<n>)`, push, wait for green `deploy/preprod`, closing comment, close, remove worktree and branch. Then report `merged`. Leave the standing PR to the orchestrator.

**When stuck**: if a decision only Titouan can make comes up, stop and report `blocked` with one specific question, the options, and your recommendation; you will be resumed with the answer. If real diagnosis cannot clear a failure, report `failed`.

**Report** (your final message, at most 15 lines):
```
status: ready-to-merge | merged | blocked | failed
issue: #<n>
summary: <what changed, one or two lines>
gate: <each check: pass/fail>
model: <migration added? its name>
defaults: <choices you made and why>
question: <only when blocked/failed>
commit: <sha on dev, only when merged>
```
