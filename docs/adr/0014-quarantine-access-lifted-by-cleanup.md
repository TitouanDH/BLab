# The holder a Quarantine names gets access to fix it, and lifting it Cleans the Switch up

A Quarantine names the last holder, who must clear it, yet they had no way into the Switch: it isn't reserved, and their Switch account went at Release. Only `admin` could fix what the Inspection found, so every Quarantine came back to an admin. Decided with Titouan (2026-11-05, #118): the user a Quarantine names gets a **Quarantine access** (see `CONTEXT.md`), a read-write Switch account with SU on the Quarantined Switch, for as long as they are named. No Reservation is made, since reserved Switches aren't swept.

The trade-off is in lifting it. Read-write with SU, the holder can change anything an Inspection doesn't read, and a Switch that isn't reserved must be clean. So once a Quarantine gave access, an Inspection that finds the Switch clean (a Re-check, the Sweep, an admin's) doesn't free it. It has the Switch worker Clean it up, which ends the access first, and the Cleanup's own Inspection lifts the Quarantine. A lift takes a reload (minutes) instead of being instant, and a Switch whose Cleanup then fails stays in Quarantine.

## Considered options

- **Lift at once on a clean Inspection, as before.** Rejected: whatever the holder left that an Inspection doesn't see would stay on a Switch the next user takes as clean.
- **Reserve the Switch to the holder.** Rejected: a reserved Switch isn't swept or Inspected for cables, and it would count against their Reservations.

## Consequences

- `Quarantine.access_given_at` (additive, ADR 0002) records that access was given, before the account is made. Only then does a lift go through a Cleanup: a Quarantine naming nobody, or one from before this change, is lifted at once as before. An admin's lift from the admin site still lifts at once, and has the Switch Cleaned up, naming nobody should it not be clean then.
- The account is made and removed by the Switch account sync like any other (`switch_accounts.entitled`), so the Switch worker retries it when the Switch is unreachable, and the Quarantine still stands. An Out of service Switch is left alone, as for every account, and a clean Inspection lifts its Quarantine at once: putting it back in service Cleans it up.
- An Inspection doesn't count the holder's account as left while the access is open. During a Cleanup the access is closed, so an account the Cleanup couldn't remove still fails its Inspection. If that Inspection isn't clean, the Quarantine stays and the access comes back.
- Behind `BLAB_QUARANTINE_ACCESS`, off by default: production's older code would remove the account as one left on a Switch nobody holds, and its Inspections would fail on it. Turn it on in both `.env` files once `main` runs #118.
