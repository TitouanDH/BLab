---
name: issues
description: Work the open GitHub issues end to end — clarify with Titouan, then orchestrate subagents that implement, gate, squash-merge into dev, close.
disable-model-invocation: true
---

Drain the **queue**: every open issue an agent can act on, carried from pick-up to closed on `dev`. This session is the **orchestrator**: it triages, clarifies, dispatches, serialises merges, and keeps the standing PR. Each issue's implementation runs in its own **worker** subagent.

Arguments, if given, are issue numbers: the queue is then those issues only.

## Budget

Every window — orchestrator and worker — stays within **200k tokens**. The orchestrator holds the queue, the briefs and the workers' short reports; diffs, test output, logs and file contents stay in the workers. An issue too large for one worker's budget is split into child issues (`Part of #<n>`) before dispatch.

**Pool**: at most **4 workers running** at once (laptop memory). A worker that has stopped — finished, blocked, or waiting for its merge turn — frees its slot. Workers spawn no agents of their own, so the pool is the whole footprint.

## 1. Load the procedure

Read `docs/agents/workflow.md` (the per-issue procedure, the gate, the standing PR), `docs/agents/triage-labels.md`, `CONTEXT.md`, and the ADR titles in `docs/adr/`. These are the rules; this skill drives the loop around them. `git fetch` and fast-forward `dev`.

## 2. Build the queue

`gh issue list --state open --json number,title,body,labels,comments`, then sort each issue into one bucket:

- **`needs-info`**: read the comments after the question. Answered → remove `needs-info`, add `ready-for-agent`, queue it. Unanswered → it goes to §3.
- **`needs-triage` or unlabelled**: triage it. Clear enough to build → `ready-for-agent`, queue it. Otherwise it goes to §3.
- **Parent issues** (a body that lists child issues, or children marked `Part of #<n>`): do not implement directly. Close one when all its children are closed, with a comment summing them up.
- **`ready-for-human`, `wontfix`**: skip.
- **`ready-for-agent`**: queue it.

Order the queue: blockers before what they block (`Blocked by` lines, native dependencies, the order in a parent's list), then bugs, then the rest by number. Mark each issue **model** if it will likely add a migration.

## 3. Clarify before dispatch

A worker knows only its brief, so each queued issue must be **brief-ready**: a worker could build it without asking anything. For each one, list the open decisions. Decide the ones with a sensible default yourself (they go in the brief and the closing comment). The rest are questions for Titouan — see §6 for what qualifies.

Ask all of them now, in this session, with `AskUserQuestion` (up to 4 questions per call, each naming its issue, with options and your recommendation first). Post each answer on its issue as a comment (`Decided with Titouan: …`) so the decision lives in the tracker, and remove `needs-info`. An issue Titouan defers gets the question as a comment plus `needs-info`, and leaves the queue.

Done when every issue left in the queue is brief-ready. From here the run goes unattended.

## 4. Orchestrate

Fill the pool from the front of the queue, skipping any issue whose blocker is still open, and never running two **model** issues at once. Dispatch each worker with the `Agent` tool (`general-purpose`, background), its prompt filled from [`worker-brief.md`](worker-brief.md). When a worker stops, read its report and refill its slot right away from the queue.

A worker stops in one of three states:

- **`ready-to-merge`**: gate green in its worktree. Put it on the **merge line**.
- **`blocked`**: it hit a question. Ask Titouan with `AskUserQuestion`, record the answer on the issue, then resume the same worker with `SendMessage`. If he defers, follow §6.
- **`failed`**: real diagnosis did not clear it. Follow §6.

**Merge line**: one merge at a time, because every push to `dev` redeploys pre-prod. When no merge is in flight, resume the first worker on the line with `SendMessage`: `Merge now.` It rebases on the latest `dev`, reruns the gate, squash-merges, pushes, waits for green `deploy/preprod`, comments, closes and cleans up (workflow.md steps 4–6), then reports `merged`. After each `merged`, update the standing PR description yourself (step 7) and resume the next worker on the line. A red `deploy/preprod` freezes the line until it is fixed through `dev`.

## 5. Judgement calls

When an issue leaves a detail open and a sensible default exists, choose it, and record the choice and its reason in the brief and the closing comment so Titouan can overturn it on pre-prod.

## 6. When Titouan is genuinely needed

Only what he alone can decide:

- a product decision with no sensible default, where a wrong guess ships visible behaviour he would have to undo;
- a migration that cannot be kept additive (ADR 0002);
- any write to a real backbone or lab switch outside code shipped through `dev` (device access is read-only without his written approval);
- touching the server beyond fixing a failed deploy;
- a blocker you cannot clear after real diagnosis (`diagnosing-bugs`).

Ask him in session (§3, §4). When he defers, or the issue cannot proceed: comment one specific question on the issue, with the options, your recommendation, and what was already found; add `needs-info`; leave any worktree in place and say so in the comment; move on.

## 7. Done

The run is done when every issue in the queue is closed, labelled `needs-info` with a question, or skipped per §2, no worker is running, `dev` has a green `deploy/preprod` on its head, and the standing `dev` → `main` PR lists every merged issue.

Report to the user: a table of issue, outcome (closed / needs-info / skipped) and a one-line note, then the standing PR link.
