# Projects replace Topology sharing, rolled out at a maintenance window

Sharing a whole Topology was coarse and pushed onto people: a holder shared everything they held, with whoever they picked, for as long as they forgot to unshare. Work in the lab is organised around a piece of work (a case, a test campaign), not around who holds what. So a **Project** (see `CONTEXT.md`) now groups the Switches reserved for one piece of work and its **members**, who join through its **Invite link**. Every Reservation is made into one Project; the user who reserved stays its holder. Every member may work on every Switch of the Project (Renew, Release, Connect, Disconnect) and gets a Switch account on it.

## Decisions

- **Behind `BLAB_PROJECTS`.** The stage setting #40 introduced turns Projects on, along with the Switch password. With it off, BLab behaves as before: Reserve ignores any Project, sharing works, every Projects endpoint answers 404, and the UI shows none of it (it asks `projects/`, and a 404 means off).
- **Who may work on a reserved Switch** is asked of one place, `api/topology.py` (`may_work_on`, `workable_reservations`, `users_who_may_work_on`), which Switch details (#36), Switch accounts, Renew, Release, Connect and Disconnect all use; with Projects on it answers with the rule in `api/projects.py`. With Projects on: the holder and the members of the Reservation's Project. A Reservation without a Project (made with Projects off, or through older code) keeps the old rule, its holder and the users they shared with, until the conversion gives it one. A Share no longer grants anything on a Reservation that has a Project.
- **Reserve** takes `project` (one of the caller's active Projects) or `new_project` (`{name, reference}`). With neither, it uses the caller's last used Project, or, for their first Reserve, makes "<username>'s lab". A new Project is only made once the Switch is known to be free, inside the Reserve's transaction, so a refused Reserve leaves no empty Project behind. The Project row is locked meanwhile, so it can't be archived under the Reserve.
- **Links never cross Projects**: Connect refuses two Switches whose Reservations aren't in the same Project (or, without Projects, held by the same holder). Disconnect still only needs either end, so a Link that crossed before the conversion can always be removed.
- **Members**: the owner renews the Invite link (the old one stops working) and removes members; any other member may leave. Joining, leaving and being removed bring the Switch accounts of the Project's Switches in step at once; the Switch worker retries what failed.
- **Archive** only once the Project holds no Reservation; it clears the Invite link. Projects are never deleted (the admin site can't delete them either).
- **Topology layout** is a new table keyed per Project (`ProjectSwitchPosition`). Adding a Project key to `SwitchPosition` would have meant dropping its `(owner, switch)` unique constraint, which `main`'s code needs for its upsert (ADR 0002).

## Rollout

Pre-prod and production share the database and the real Switches (ADR 0002), and both run with `BLAB_PROJECTS` off until Titouan's maintenance window. Migration `0014_projects` only adds tables and a nullable `Reservation.project`, so `main`'s code keeps working on it meanwhile. At the window:

1. Generate `BLAB_SWITCH_PASSWORD_KEY` and set it and `BLAB_PROJECTS=on` in both `.env` files (ADR 0004, #40).
2. Merge `dev` into `main`; both stages redeploy with Projects on.
3. Run the conversion once, on either stage: `docker compose exec django python manage.py convert_shares_to_projects --dry-run`, then without `--dry-run`. Each user holding a Reservation without a Project gets "<username>'s lab" (an existing one of theirs is used only if nobody else is in it but users they shared with); their Reservations go into it, each user they shared their Topology with becomes a member, and their layout is copied. It only moves Reservations without a Project, so it is safe to run again, and running it late changes nothing anyone can do in the meantime: until converted, a Reservation keeps the share rule.

Shares (`TopologyShare`), the per-owner `SwitchPosition` table and the sharing endpoints stay until a later, destructive step, once `main` no longer uses them.

## Considered options

- **A data migration for the conversion.** Rejected: pre-prod would apply it to production's data as soon as it reached `dev`, weeks before the window, and production's code would keep sharing meanwhile.
- **Keep sharing alongside Projects.** Rejected: two rules for who may work on a Switch, and nothing would ever retire the coarse one.
- **Let any member see and pass on the Invite link.** Rejected for now: a removed member could rejoin with a link they kept. Only the owner sees it; renewing it is how they close the door.

## Consequences

- This amends ADR 0004: Switch accounts follow Project membership instead of Shares, with Projects on.
- A member who holds a Switch in the Project can't leave or be removed until it is Released (any member may Release it). The owner can't leave; they archive the Project once it is empty. A Project whose owner's account is deleted has no owner: an admin sets one on the admin site.
- After the conversion, a Link made through a Share may join two Projects. It stays shown on both Topologies and can be disconnected; it can't be made again.
- The API's `topology/<owner>/` with Projects on shows only the caller's own Switches. In the UI a Topology is a Project's picture: one Projects tab, and each Project's page (`/projects/<id>`, #54) has its Topology, Switches, Snapshots and Members; `/topology/<project id>` (#43's address) redirects there. With Projects off the Topology tab's picker lists the caller's Topology and those shared with them, at `/topology/user/<owner id>`. My lab grouped by Project is #42. Both build on `projects/` and `projects/<id>/topology/`.
- Logging in now comes back to the page asked for (`?redirect=`), such as an Invite link, with Projects on or off.
