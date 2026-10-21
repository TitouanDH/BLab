// A fake BLab API for browser tests: every request to /api/ is answered here, in the page,
// so the tests never reach a server or a switch. The state is a small lab that the
// handlers change the way the real API would (reserve, release, connect, share...).
//
// Usage in a test (see fixtures.js):
//   test('...', async ({ page, api }) => {
//     api.state.switches[0].unavailable = { state: 'quarantine', reason: '...' };
//     api.override('POST', 'reserve/', () => ({ status: 400, body: { detail: 'No.' } }));
//     await page.goto('/reservation');
//   });

import { checkSwitchPassword } from '../src/utils/switchPassword.js';

// A user as everyone sees them
const publicUser = ({ id, username }) => ({ id, username });

const DAY = 24 * 60 * 60 * 1000;

export function defaultState(now = Date.now()) {
  const iso = (offsetDays) => new Date(now + offsetDays * DAY).toISOString();
  return {
    me: { id: 1, username: 'alice', email: 'alice@example.com' },
    // The Projects stage setting (BLAB_PROJECTS): on, every user chooses a Switch password,
    // kept as users[].switchPassword (never sent back), and every page waits for it
    projects: false,
    // A user's email (their Rainbow login) only ever reaches that user: login/ and account/
    users: [
      { id: 1, username: 'alice', email: 'alice@example.com' },
      { id: 2, username: 'bob', email: 'bob@example.com' },
      { id: 3, username: 'carol', email: 'carol@example.com' },
    ],
    switches: [
      { id: 1, model: 'OS6860E-24', mngt_IP: '10.69.145.11', console: 'ts1:2001', part_number: '903624-90', hardware_revision: '05', serial_number: 'SN0001', unavailable: null },
      { id: 2, model: 'OS6900-X20', mngt_IP: '10.69.145.12', console: 'ts1:2002', part_number: '902740-90', hardware_revision: '12', serial_number: 'SN0002', unavailable: null },
      { id: 3, model: 'OS6560-P24', mngt_IP: '10.69.145.13', console: 'ts1:2003', part_number: '903721-90', hardware_revision: '03', serial_number: 'SN0003', unavailable: null },
      { id: 4, model: 'OS6465-P12', mngt_IP: '10.69.145.14', console: 'ts1:2004', part_number: '903917-90', hardware_revision: '02', serial_number: 'SN0004',
        unavailable: { state: 'quarantine', reason: 'In Quarantine: Unwanted cable on 1/1/5. Named: carol.' } },
      { id: 5, model: 'OS6360-10', mngt_IP: '10.69.145.15', console: 'ts1:2005', part_number: '904016-90', hardware_revision: '01', serial_number: 'SN0005',
        unavailable: { state: 'out_of_service', reason: 'Out of service: PSU broken' } },
    ],
    // Tools (#56), such as the IXIA: never reserved; their ports, in `ports`, take a Link from any Topology
    tools: [],
    reservations: [
      { id: 1, switch: 2, user: 1, creation_date: iso(-3), end_date: iso(4), renewals_left: 2, admin_exception: false },
      { id: 2, switch: 3, user: 2, creation_date: iso(-1), end_date: iso(10), renewals_left: 2, admin_exception: false },
    ],
    ports: [
      { id: 11, switch: 1, port_switch: '1/1/1', backbone: '10.69.144.1', port_backbone: '1/1/11', svlan: null, status: 'UP' },
      { id: 12, switch: 1, port_switch: '1/1/2', backbone: '10.69.144.1', port_backbone: '1/1/12', svlan: null, status: 'UP' },
      { id: 21, switch: 2, port_switch: '1/1/1', backbone: '10.69.144.1', port_backbone: '1/1/21', svlan: 1001, status: 'UP' },
      { id: 22, switch: 2, port_switch: '1/1/2', backbone: '10.69.144.1', port_backbone: '1/1/22', svlan: null, status: 'UP' },
      { id: 31, switch: 3, port_switch: '1/1/1', backbone: '10.69.144.2', port_backbone: '1/1/31', svlan: 1001, status: 'UP' },
      { id: 32, switch: 3, port_switch: '1/1/2', backbone: '10.69.144.2', port_backbone: '1/1/32', svlan: null, status: 'UP' },
    ],
    // Topology layouts, by owner id: { [switchId]: {x, y} } (topology/<owner>/layout/), and with
    // Projects on by 'p<project id>' (projects/<id>/layout/)
    layouts: {},
    // With Projects on: { id, name, reference, owner, archived_at, invite_token,
    // members: [{ id, joined_at, last_reserved_at }] }; a Reservation's `project` is one's id
    projectList: [],
    // Snapshots (#45), newest first: { id, project, taken_at, taken_by, reason, note,
    // switches: [{ id, model, mngt_IP, aos_release, read_at, read_error, restorable }], links }
    snapshots: [],
    restores: {},  // by project id: its latest Restore, as projects/<id>/snapshots/ gives it
    // A Switch's Config (#44): its running config by switch id, each Snapshot's saved one by
    // '<snapshot id>-<switch id>', and how many live reads each Switch had
    runningConfigs: {},
    snapshotConfigs: {},
    liveReads: {},
    staff: false,  // whether the user logged in is an admin, for switches/<id>/config/
    releases: {},  // by switch id: the AOS release it runs now (the Snapshot's unless set)
    teardownErrors: {},  // by SVLAN: why its disconnect failed (the Link shows again)
    ghosts: {},  // by SVLAN: why the Link worker found it a Ghost Link
    shares: [
      { id: 1, owner: 2, target: 1, created_at: iso(-2) },
    ],
    inspections: {
      1: { kind: 'inspection', at: iso(-1), ok: true, reasons: [], warnings: [], user: null },
      4: { kind: 'inspection', at: iso(-1), ok: false, reasons: ['Unwanted cable on 1/1/5'], warnings: [], user: null },
    },
    quarantines: {
      4: { holder: 'carol', holder_id: 3, opened_at: iso(-1), reasons: ['Unwanted cable on 1/1/5'] },
    },
    cleaningUp: [],
    // Switch accounts that BLab couldn't create, by switch id: why; those it is still creating,
    // by switch id (the others are ready)
    accountErrors: {},
    accountsPending: [],
    nextSvlan: 1002,
    nextId: 100,
  };
}

const json = (status, body) => ({ status, body });

export class MockApi {
  constructor(state = defaultState()) {
    this.state = state;
    this.overrides = [];
    this.calls = [];       // every request answered, as 'METHOD path'
    this.unhandled = [];   // requests no handler knows: a test should fail on them
  }

  // Answer one endpoint differently, e.g. to make it fail. Later overrides win.
  // handler(request, body) returns { status, body }, or undefined to fall through.
  override(method, path, handler) {
    this.overrides.unshift({ method, path, handler });
  }

  // Make every API call fail as if BLab were unreachable, until restored
  goOffline() { this.offline = true; }
  goOnline() { this.offline = false; }

  async install(page) {
    await page.route(/\/api\//, async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace(/^.*?\/api\//, '');
      const method = request.method();
      if (this.offline) {
        await route.abort('connectionrefused');
        return;
      }
      let body = null;
      try { body = request.postDataJSON(); } catch { body = null; }
      this.calls.push(`${method} ${path}`);
      let answer;
      for (const o of this.overrides) {
        if (o.method === method && (o.path instanceof RegExp ? o.path.test(path) : o.path === path)) {
          answer = await o.handler(request, body);
          if (answer) break;
        }
      }
      answer = answer || this.handle(method, path, body, url.searchParams);
      if (!answer) {
        this.unhandled.push(`${method} ${path}`);
        answer = json(501, { detail: `The mock API has no handler for ${method} ${path}` });
      }
      await route.fulfill({ status: answer.status, contentType: 'application/json', body: JSON.stringify(answer.body) });
    });
  }

  // --- The fake lab ---

  user(id) { return this.state.users.find(u => u.id === Number(id)); }
  holderOf(switchId) { return this.state.reservations.find(r => r.switch === Number(switchId)); }
  linkPorts(svlan) { return this.state.ports.filter(p => p.svlan === svlan); }
  project(id) { return this.state.projectList.find(p => p.id === Number(id)); }
  isMember(project, userId = this.state.me.id) { return !!project?.members.some(m => m.id === userId); }
  // Who may work on a reserved Switch: with Projects on and the Reservation in one, its members
  // and the holder; otherwise the holder's Topology rule (mayWork)
  mayWorkOn(reservation) {
    if (this.state.projects && reservation.project) {
      return reservation.user === this.state.me.id || this.isMember(this.project(reservation.project));
    }
    return this.mayWork(reservation.user);
  }
  // switches/<id>/config/ (#44): live for whoever may work on the Switch and admins, the Golden
  // init, and the Snapshots of the caller's Projects; a live read at most every 30 s per Switch
  switchConfig(id, show, against) {
    const s = this.state;
    const sw = s.switches.find(x => x.id === id);
    if (!sw) return json(404, { detail: 'Not found.' });
    const reservation = this.holderOf(id);
    const works = s.staff || (reservation && this.mayWorkOn(reservation));
    const snapshots = s.projects ? s.snapshots.filter(x => x.switches.some(e => e.id === id) && (s.staff || this.isMember(this.project(x.project)))) : [];
    if (!works && !snapshots.length) return json(403, { detail: "Only the members of this Switch's Project, and admins, may see its config." });
    const sources = [
      ...(works ? [{ key: 'live', kind: 'live', label: 'Live' }] : []),
      { key: 'golden', kind: 'golden', label: 'Golden init', model: sw.model },
      ...snapshots.map(x => { const p = this.project(x.project); return { key: `snapshot-${x.id}`, kind: 'snapshot', label: 'Snapshot', id: x.id,
        taken_at: x.taken_at, reason: x.reason, note: x.note, taken_by: x.taken_by, project: { id: p.id, name: p.name },
        aos_release: x.switches.find(e => e.id === id).aos_release, read_error: x.switches.find(e => e.id === id).read_error }; }),
    ];
    const read = (key) => {
      if (!sources.some(x => x.key === key)) return null;
      if (key === 'live') {
        s.liveReads[id] = (s.liveReads[id] || 0) + 1;
        const now = new Date();
        return { key, kind: 'live', config: s.runningConfigs[id] ?? null, vcsetup: null, read_at: now.toISOString(), read_by: s.me.username,
          error: s.runningConfigs[id] === undefined ? `Cannot connect to ${sw.mngt_IP}: timed out` : null, reading: false,
          next_read_at: new Date(now.getTime() + 30000).toISOString() };
      }
      if (key === 'golden') return { key, kind: 'golden', config: `system name "${sw.model}"\ncommand-log enable\n`, vcsetup: null, read_at: null, error: null };
      const snapshot = s.snapshots.find(x => `snapshot-${x.id}` === key);
      const config = s.snapshotConfigs[`${snapshot.id}-${id}`] ?? null;
      return { key, kind: 'snapshot', config, vcsetup: null, read_at: snapshot.taken_at, error: config === null ? 'not read' : null,
        aos_release: snapshot.switches.find(e => e.id === id).aos_release };
    };
    const shownKey = show || sources[0].key;
    const shown = read(shownKey);
    const other = against ? read(against) : null;
    if (!shown || (against && !other)) return json(404, { detail: 'No such config of this Switch among those you may see.' });
    let diff = null;
    if (other && shown.config !== null && other.config !== null) {
      // Lines only in one: enough of api.switch_config.diff for the page
      const a = other.config.split('\n').filter(Boolean);
      const b = shown.config.split('\n').filter(Boolean);
      const rows = [...b.map(line => ({ op: a.includes(line) ? ' ' : '+', line })), ...a.filter(line => !b.includes(line)).map(line => ({ op: '-', line }))];
      diff = { rows, added: rows.filter(r => r.op === '+').length, removed: rows.filter(r => r.op === '-').length, reordered: false };
    }
    const project = reservation?.project && this.isMember(this.project(reservation.project)) ? this.project(reservation.project) : null;
    return json(200, { switch: { id, model: sw.model, mngt_IP: sw.mngt_IP, project: project ? { id: project.id, name: project.name } : null },
      sources, shown, against: other, diff, live_read_interval: 30 });
  }

  // A Project as projects/ gives it to the user logged in
  projectSummary(p) {
    const me = this.state.me.id;
    return { id: p.id, name: p.name, reference: p.reference || '', owner: p.owner ? publicUser(this.user(p.owner)) : null,
      is_owner: p.owner === me, created_at: p.created_at || new Date().toISOString(), archived_at: p.archived_at || null,
      switch_count: this.state.reservations.filter(r => r.project === p.id).length,
      members: p.members.map(m => ({ id: m.id, username: this.user(m.id).username, joined_at: m.joined_at })),
      invite_token: p.owner === me ? p.invite_token : null };
  }
  newProject(name, reference = '') {
    const p = { id: this.state.nextId++, name, reference, owner: this.state.me.id, archived_at: null,
      invite_token: `tok${this.state.nextId++}`, members: [{ id: this.state.me.id, joined_at: new Date().toISOString() }] };
    this.state.projectList.push(p);
    return p;
  }
  lastUsed() {
    const me = this.state.me.id;
    const mine = this.state.projectList.filter(p => !p.archived_at && this.isMember(p))
      .map(p => ({ p, m: p.members.find(m => m.id === me) }))
      .sort((a, b) => (b.m.last_reserved_at || '').localeCompare(a.m.last_reserved_at || '') || b.m.joined_at.localeCompare(a.m.joined_at));
    return mine[0]?.p || null;
  }
  isTool(switchId) { return this.state.tools.some(t => t.id === Number(switchId)); }
  // The topology/ answer for these own Switches; layoutKey picks the saved layout
  topologyOf(own, layoutKey) {
    const s = this.state;
    const ownPorts = s.ports.filter(p => own.includes(p.switch));
    // A Link being disconnected is left out, unless its teardown failed
    const svlans = [...new Set(ownPorts.filter(p => p.svlan).map(p => p.svlan))]
      .filter(v => !this.linkPorts(v).some(p => this.teardownPending(p)) || s.teardownErrors[v]);
    const farPorts = svlans.flatMap(v => this.linkPorts(v)).filter(p => !own.includes(p.switch));
    const farIds = [...new Set(farPorts.map(p => p.switch))];
    const tool = (t) => ({ ...t, is_tool: true, in_topology: false });
    return {
      switches: [
        ...s.switches.filter(x => own.includes(x.id)).map(x => ({ ...x, in_topology: true, reservation: this.holderOf(x.id) })),
        ...s.switches.filter(x => farIds.includes(x.id)).map(x => ({ ...x, in_topology: false })),
        ...s.tools.filter(t => farIds.includes(t.id)).map(tool),
      ],
      tools: s.tools.map(tool),
      tool_ports: s.ports.filter(p => this.isTool(p.switch) && !p.svlan),
      ports: [...ownPorts, ...farPorts].map(p => ({ teardown_requested_at: null, teardown_svlan: null, teardown_error: null, ...p,
        ...(s.teardownErrors[p.svlan] && this.teardownPending(p) ? { teardown_error: s.teardownErrors[p.svlan] } : {}) })),
      links: svlans.map(v => ({ svlan: v, ports: this.linkPorts(v).map(p => p.id), teardown_error: s.teardownErrors[v] || null,
        ghost_reason: s.ghosts[v] || null, ghost_seen_at: s.ghosts[v] ? new Date(Date.now() - 3 * 60 * 1000).toISOString() : null })),
      layout: Object.fromEntries(Object.entries(s.layouts[layoutKey] || {})
        .filter(([id]) => own.includes(Number(id)) || farIds.includes(Number(id)))),
      may_work: true,
    };
  }

  // The holder, or a user the holder shares their Topology with
  mayWork(ownerId) {
    const me = this.state.me.id;
    return ownerId === me || this.state.shares.some(x => x.owner === ownerId && x.target === me);
  }
  // A user as they see their own account (account/, login/)
  account({ id, username, email, switchPassword }) {
    return { id, username, email: email || '', switch_login: username,
      switch_password_enabled: this.state.projects, switch_password_set: this.state.projects && !!switchPassword };
  }

  // The Switch accounts of the user logged in: one per Switch they may work on (theirs, and
  // those of the Topologies shared with them), as my_lab/ and reserve/ give them (the real
  // switch_accounts/, which the UI no longer reads, gives the same). With a Switch password,
  // the account opens with it and no password is given.
  switchAccounts() {
    const s = this.state;
    return s.reservations.filter(r => this.mayWorkOn(r)).map(r => {
      const sw = s.switches.find(x => x.id === r.switch);
      const error = s.accountErrors[r.switch] || null;
      const pending = !error && s.accountsPending.includes(r.switch);
      const chosen = s.projects && !!s.me.switchPassword;
      return { switch: sw.id, mngt_IP: sw.mngt_IP, holder: this.user(r.user).username, name: s.me.username,
        password: error || pending || chosen ? null : `Pw-${s.me.username}-${sw.id}x`, switch_password: chosen,
        state: error ? 'failed' : pending ? 'pending' : 'ready', error };
    });
  }

  // My lab (my_lab/): every Switch the user logged in may work on, grouped by Project (with
  // Projects off: their own Reservations, then each Topology shared with them), and the
  // Quarantines naming them, as api/api/my_lab.py gives it
  myLab() {
    const s = this.state;
    const me = s.me.id;
    const holderGroup = (id) => ({ kind: 'holder', holder: publicUser(this.user(id)), switches: [] });
    const groups = new Map();
    if (s.projects) {
      s.projectList.filter(p => !p.archived_at && this.isMember(p)).sort((a, b) => a.name.localeCompare(b.name))
        .forEach(p => groups.set(`p${p.id}`, { kind: 'project', project: this.projectSummary(p), switches: [] }));
    } else {
      groups.set(`u${me}`, holderGroup(me));
      s.shares.filter(x => x.target === me).map(x => x.owner)
        .sort((a, b) => this.user(a).username.localeCompare(this.user(b).username))
        .forEach(owner => groups.set(`u${owner}`, holderGroup(owner)));
    }
    const accounts = Object.fromEntries(this.switchAccounts().map(a => [a.switch, a]));
    const switchOf = (r) => s.switches.find(x => x.id === r.switch);
    const reservations = s.reservations.filter(r => this.mayWorkOn(r))
      .sort((a, b) => switchOf(a).model.localeCompare(switchOf(b).model) || a.switch - b.switch);
    for (const r of reservations) {
      const sw = switchOf(r);
      const key = s.projects && r.project ? `p${r.project}` : `u${r.user}`;
      if (!groups.has(key)) {
        groups.set(key, key.startsWith('p') ? { kind: 'project', project: this.projectSummary(this.project(r.project)), switches: [] } : holderGroup(r.user));
      }
      // Links as its Topology shows them: not while being disconnected, unless that failed
      const svlans = [...new Set(s.ports.filter(p => p.switch === sw.id && p.svlan).map(p => p.svlan))]
        .filter(v => this.linkPorts(v).length === 2 && (!this.linkPorts(v).some(p => this.teardownPending(p)) || s.teardownErrors[v]));
      groups.get(key).switches.push({ id: sw.id, model: sw.model, mngt_IP: sw.mngt_IP,
        holder: this.user(r.user).username, holder_id: r.user, end_date: r.end_date,
        renewals_left: r.renewals_left, admin_exception: r.admin_exception,
        links: svlans.length, ghost_links: svlans.filter(v => s.ghosts[v]).length, account: accounts[sw.id] || null });
    }
    const quarantines = Object.entries(s.quarantines).filter(([, q]) => q.holder_id === me).map(([id, q]) => {
      const sw = s.switches.find(x => x.id === Number(id));
      return { id: sw.id, model: sw.model, mngt_IP: sw.mngt_IP,
        quarantine: { opened_at: q.opened_at, reasons: q.reasons }, cleaning_up: s.cleaningUp.includes(sw.id) };
    });
    return { projects: s.projects, groups: [...groups.values()], quarantines };
  }

  // A disconnect asked for and not done yet (Port.teardown_pending in the real API)
  teardownPending(port) { return port.svlan != null && port.teardown_requested_at != null && port.teardown_svlan === port.svlan; }

  // The Link worker's turn: tears down every Link asked for whose teardown hasn't failed
  runLinkWorker() {
    const s = this.state;
    for (const p of s.ports.filter(p => this.teardownPending(p) && !s.teardownErrors[p.svlan])) {
      for (const q of this.linkPorts(p.svlan)) {
        Object.assign(q, { svlan: null, teardown_requested_at: null, teardown_svlan: null });
      }
    }
  }

  handle(method, path, body, query = new URLSearchParams()) {
    const s = this.state;
    const key = `${method} ${path}`;
    let m;

    if (key === 'POST login/') {
      const user = s.users.find(u => u.username === body?.username);
      if (!user || body?.password !== 'secret') return json(401, { detail: 'Invalid credentials.' });
      s.me = user;
      return json(202, { token: `token-${user.id}`, user: this.account(user), is_staff: false });
    }
    if (key === 'POST signup/') {
      if (s.users.some(u => u.username === body?.username)) {
        return json(400, { username: ['A user with that username already exists.'] });
      }
      if (!body?.email) return json(400, { email: ['This field is required.'] });
      const user = { id: s.nextId++, username: body.username, email: body.email.toLowerCase() };
      s.users.push(user);
      return json(201, { token: `token-${user.id}`, user: this.account(user) });
    }
    if (key === 'GET logout/') return json(200, { detail: 'Logout successful.' });
    if (key === 'GET account/') return json(200, this.account(s.me));
    if (key === 'POST account/') {
      if (!body?.email) return json(400, { email: ['This field may not be blank.'] });
      s.me.email = body.email.toLowerCase();
      return json(200, this.account(s.me));
    }
    // The real API refuses everything else to an account without its email, then, with
    // Projects on, without its Switch password
    if (!s.me.email) return json(403, { detail: 'Add your email address (your Rainbow login) before going on.', code: 'email_required' });
    if (key === 'POST switch_password/') {
      if (!s.projects) return json(404, { detail: 'Not found.' });
      const broken = checkSwitchPassword(body?.password || '', [s.me.username]).filter(r => !r.passed);
      if (broken.length) return json(400, { password: broken.map(r => `Not followed: ${r.label}.`) });
      s.me.switchPassword = body.password;
      return json(200, { detail: 'Switch password saved.', failed: this.switchAccounts().filter(a => a.state === 'failed') });
    }
    if (s.projects && !s.me.switchPassword) return json(403, { detail: 'Choose your Switch password before going on.', code: 'switch_password_required' });
    if (key === 'GET list_user/') return json(200, { users: s.users.map(publicUser) });
    if ((m = path.match(/^list_user\/(\d+)\/$/)) && method === 'GET') {
      const user = this.user(m[1]);
      return user ? json(200, publicUser(user)) : json(404, { detail: 'Not found.' });
    }
    if (key === 'GET list_switch/') {
      // A Switch whose Cleanup runs can't be reserved until the Switch worker is done
      // Every Switch with its details, and cabled_ports: its Ports (each paired with a UNI)
      return json(200, { switchs: s.switches.map(sw => ({
        ...sw,
        cabled_ports: s.ports.filter(p => p.switch === sw.id).length,
        ...(!sw.unavailable && s.cleaningUp.includes(sw.id)
          ? { unavailable: { state: 'cleaning_up', reason: 'Being Cleaned up: BLab is reloading and Inspecting it.' } } : {}),
      })) });
    }
    if (key === 'GET list_reservation/') {
      return json(200, s.reservations.map(r => {
        const row = { ...r, username: this.user(r.user).username, may_work: this.mayWorkOn(r) };
        delete row.project;
        if (s.projects) Object.assign(row, { project: row.may_work ? r.project || null : null,
          project_name: row.may_work && r.project ? this.project(r.project).name : null });
        return row;
      }));
    }
    if (key === 'GET list_port/') return json(200, { ports: s.ports });

    if (key === 'POST reserve/') {
      const sw = s.switches.find(x => x.id === Number(body?.switch));
      if (!sw) return json(404, { detail: 'Not found.' });
      if (this.holderOf(sw.id)) return json(400, { warning: 'This switch is already reserved.' });
      if (sw.unavailable) return json(400, { detail: `This Switch can't be reserved. ${sw.unavailable.reason}` });
      let project = null;
      if (s.projects) {
        if (body.new_project) {
          if (!body.new_project.name?.trim()) return json(400, { detail: 'Give the Project a name.' });
          project = this.newProject(body.new_project.name.trim(), body.new_project.reference || '');
        } else if (body.project) {
          project = this.project(body.project);
          if (!project || !this.isMember(project)) return json(404, { detail: 'No such Project among yours.' });
          if (project.archived_at) return json(400, { detail: `${project.name} is archived: choose another Project.` });
        } else {
          project = this.lastUsed() || this.newProject(`${s.me.username}'s lab`);
        }
        project.members.find(m => m.id === s.me.id).last_reserved_at = new Date().toISOString();
      }
      s.reservations.push({ id: s.nextId++, switch: sw.id, user: s.me.id, creation_date: new Date().toISOString(), end_date: body.end_date, renewals_left: 2, admin_exception: false,
        ...(project ? { project: project.id } : {}) });
      return json(201, { detail: 'Reservation successful.', switch_account: this.switchAccounts().find(a => a.switch === sw.id),
        ...(project ? { project: { id: project.id, name: project.name } } : {}) });
    }
    if (key === 'POST renew/') {
      const reservation = this.holderOf(body?.switch);
      if (!reservation) return json(400, { detail: 'This Switch is not reserved.' });
      if (!reservation.renewals_left) return json(400, { detail: 'This Reservation has no Renewals left.' });
      reservation.renewals_left -= 1;
      reservation.end_date = new Date(new Date(reservation.end_date).getTime() + 7 * DAY).toISOString();
      return json(200, { detail: `Renewed for another 7 days. Renewals left: ${reservation.renewals_left}.`,
        end_date: reservation.end_date, renewals_left: reservation.renewals_left });
    }
    if ((m = path.match(/^release_check\/(\d+)\/$/)) && method === 'GET') {
      return json(200, { unwanted_cables: [] });
    }
    if (key === 'POST release/') {
      const reservation = this.holderOf(body?.switch);
      if (!reservation) return json(400, { detail: 'This switch is not reserved.' });
      s.reservations = s.reservations.filter(r => r !== reservation);
      s.cleaningUp.push(reservation.switch);  // every Release Cleans up
      for (const p of s.ports.filter(p => p.switch === reservation.switch && p.svlan)) {
        for (const q of this.linkPorts(p.svlan)) q.svlan = null;
      }
      return json(200, { detail: 'Released. BLab is now Cleaning the Switch up: it restores the init config, reloads it, then Inspects it.' });
    }

    if ((m = path.match(/^topology\/(\d+)\/$/)) && method === 'GET') {
      const ownerId = Number(m[1]);
      const shared = !s.projects && s.shares.some(x => x.owner === ownerId && x.target === s.me.id);
      if (ownerId !== s.me.id && !shared) return json(403, { detail: 'This topology is not shared with you.' });
      return json(200, this.topologyOf(s.reservations.filter(r => r.user === ownerId).map(r => r.switch), ownerId));
    }
    if ((m = path.match(/^topology\/(\d+)\/layout\/$/)) && ['PUT', 'DELETE'].includes(method)) {
      const ownerId = Number(m[1]);
      if (!this.mayWork(ownerId)) return json(403, { detail: 'This topology is not shared with you.' });
      if (method === 'DELETE') {
        delete s.layouts[ownerId];
        return json(200, { detail: 'Layout forgotten.' });
      }
      s.layouts[ownerId] = { ...s.layouts[ownerId], ...body?.positions };
      return json(200, { detail: 'Layout saved.' });
    }
    if (key === 'POST connect/') {
      const a = s.ports.find(p => p.id === Number(body?.portA));
      const b = s.ports.find(p => p.id === Number(body?.portB));
      if (!a || !b) return json(404, { detail: 'Not found.' });
      if (this.isTool(a.switch) && this.isTool(b.switch)) return json(400, { detail: 'A Link joins a Tool to a Switch, not to another Tool.' });
      if (a.svlan || b.svlan) return json(400, { detail: 'One or both ports are already connected. Disconnect them first.' });
      a.svlan = b.svlan = s.nextSvlan++;
      return json(200, { detail: `Ports connected successfully with svlan ${a.svlan}` });
    }
    if (key === 'POST disconnect/') {
      const a = s.ports.find(p => p.id === Number(body?.portA));
      if (!a?.svlan) return json(400, { detail: 'These ports are not connected to each other.' });
      // Like the real API: recorded for the Link worker (runLinkWorker), and asking again
      // clears a past failure
      delete s.teardownErrors[a.svlan];
      for (const q of this.linkPorts(a.svlan)) {
        Object.assign(q, { teardown_requested_at: new Date().toISOString(), teardown_svlan: q.svlan });
      }
      return json(202, { detail: 'Disconnecting the ports.' });
    }
    // With Projects on, sharing is gone
    if (s.projects && ['GET list_shared_topologies/', 'POST share_topology/'].includes(key)) {
      return json(404, { detail: 'Topology sharing is replaced by Projects: invite people to your Project instead.' });
    }

    // --- Projects: 404 everywhere while off ---
    if (path.startsWith('projects/') || path.startsWith('join/')) {
      if (!s.projects) return json(404, { detail: 'Not found.' });
      if (key === 'GET projects/') {
        return json(200, { projects: s.projectList.filter(p => this.isMember(p)).map(p => this.projectSummary(p)),
          last_used: this.lastUsed()?.id ?? null });
      }
      if (key === 'POST projects/') {
        if (!body?.name?.trim()) return json(400, { detail: 'Give the Project a name.' });
        return json(201, this.projectSummary(this.newProject(body.name.trim(), body.reference || '')));
      }
      if ((m = path.match(/^join\/([^/]+)\/$/))) {
        const p = s.projectList.find(x => x.invite_token === decodeURIComponent(m[1]) && !x.archived_at);
        if (!p) return json(404, { detail: "This Invite link doesn't work: it may have been renewed, or its Project archived. Ask the Project's owner for the current one." });
        let detail;
        if (method === 'POST') {
          detail = this.isMember(p) ? `You are already a member of ${p.name}.` : `You joined ${p.name}.`;
          if (!this.isMember(p)) p.members.push({ id: s.me.id, joined_at: new Date().toISOString() });
        }
        const own = s.reservations.filter(r => r.project === p.id).map(r => r.switch);
        return json(200, { project: { id: p.id, name: p.name, reference: p.reference || '', owner: this.user(p.owner)?.username || null,
          members: p.members.length, switches: s.switches.filter(x => own.includes(x.id)).map(x => ({ id: x.id, model: x.model, mngt_IP: x.mngt_IP })) },
        member: this.isMember(p), ...(detail ? { detail } : {}) });
      }
      if ((m = path.match(/^projects\/(\d+)\/(.*)$/))) {
        const p = this.project(m[1]);
        if (!p || !this.isMember(p)) return json(404, { detail: 'No such Project among yours.' });
        const rest = `${method} ${m[2]}`;
        const owns = p.owner === s.me.id;
        if (rest === 'GET ') return json(200, this.projectSummary(p));
        if (rest === 'GET topology/') {
          return json(200, { ...this.topologyOf(s.reservations.filter(r => r.project === p.id).map(r => r.switch), `p${p.id}`),
            project: this.projectSummary(p) });
        }
        if (rest === 'PUT layout/') {
          s.layouts[`p${p.id}`] = { ...s.layouts[`p${p.id}`], ...body?.positions };
          return json(200, { detail: 'Layout saved.' });
        }
        if (rest === 'DELETE layout/') {
          delete s.layouts[`p${p.id}`];
          return json(200, { detail: 'Layout forgotten.' });
        }
        if (rest === 'POST invite/') {
          if (!owns) return json(403, { detail: "Only the Project's owner may renew its Invite link." });
          p.invite_token = `tok${s.nextId++}`;
          return json(200, this.projectSummary(p));
        }
        if (rest === 'POST leave/') {
          if (owns) return json(400, { detail: "You own this Project: you can't leave it. Archive it once it holds no Switch." });
          if (s.reservations.some(r => r.project === p.id && r.user === s.me.id)) return json(400, { detail: 'You still hold Switches in this Project: Release them first.' });
          p.members = p.members.filter(x => x.id !== s.me.id);
          return json(200, { detail: `You left ${p.name}.` });
        }
        if ((m = m[2].match(/^members\/(\d+)\/$/)) && method === 'DELETE') {
          if (!owns) return json(403, { detail: "Only the Project's owner may remove members." });
          const id = Number(m[1]);
          if (s.reservations.some(r => r.project === p.id && r.user === id)) {
            return json(400, { detail: `${this.user(id).username} still holds Switches in this Project: they, or any member, must Release them first.` });
          }
          p.members = p.members.filter(x => x.id !== id);
          return json(200, { detail: 'Member removed.', project: this.projectSummary(p) });
        }
        if (rest === 'POST switches/') {
          // Moving a reserved Switch in (#61): the holder must be a member; Links are left to the real API
          const r = s.reservations.find(x => x.switch === Number(body?.switch));
          if (!r) return json(400, { detail: 'This Switch is not reserved.' });
          if (r.project === p.id) return json(400, { detail: `This Switch is already in ${p.name}.` });
          if (!p.members.some(x => x.id === r.user)) {
            return json(400, { detail: `${this.user(r.user).username} is not a member of ${p.name}: the holder of a Switch is always a member of its Project.` });
          }
          r.project = p.id;
          return json(200, { detail: `The Switch moved to ${p.name}.`, project: this.projectSummary(p) });
        }
        if (rest === 'GET snapshots/') {
          return json(200, { snapshots: s.snapshots.filter(x => x.project === p.id), kept: 10, restore: s.restores[p.id] || null });
        }
        if (rest === 'POST snapshots/') {
          const own = s.reservations.filter(r => r.project === p.id).map(r => r.switch);
          if (!own.length) return json(400, { detail: 'This Project holds no Switch: there is nothing to save.' });
          const snapshot = { id: s.nextId++, project: p.id, taken_at: new Date().toISOString(), taken_by: s.me.username,
            reason: 'save', note: body?.note || '', links: 0,
            switches: s.switches.filter(x => own.includes(x.id)).map(x => ({ id: x.id, model: x.model, mngt_IP: x.mngt_IP,
              aos_release: s.releases[x.id] || '8.10.86.R04', read_at: new Date().toISOString(), read_error: null, restorable: true })) };
          s.snapshots.unshift(snapshot);
          return json(201, snapshot);
        }
        if ((m = path.match(/^projects\/\d+\/snapshots\/(\d+)\/restore\/$/))) {
          const snapshot = s.snapshots.find(x => x.id === Number(m[1]) && x.project === p.id);
          if (!snapshot) return json(404, { detail: 'No such Snapshot in this Project.' });
          const plan = snapshot.switches.map(sw => {
            const r = this.holderOf(sw.id);
            const unavailable = s.switches.find(x => x.id === sw.id)?.unavailable;
            const state = r ? (r.project === p.id ? 'in_project' : 'busy') : unavailable ? 'unavailable' : 'free';
            const current = ['free', 'in_project'].includes(state) ? (s.releases[sw.id] || sw.aos_release) : null;
            return { id: sw.id, model: sw.model, mngt_IP: sw.mngt_IP, state, restorable: ['free', 'in_project'].includes(state), free_on: state === 'busy' ? r.end_date : null,
              detail: { free: 'Free: reserved into the Project.', in_project: "In the Project: its config is replaced by the Snapshot's.",
                busy: 'Reserved by someone else.', unavailable: unavailable?.reason }[state],
              snapshot_release: sw.aos_release, current_release: current, release_differs: !!current && current !== sw.aos_release };
          });
          if (method === 'GET') return json(200, { switches: plan, links: snapshot.links });
          const chosen = body?.switches || plan.map(x => x.id);
          const restore = { id: s.nextId++, snapshot: snapshot.id, started_by: s.me.username, started_at: new Date().toISOString(),
            finished_at: null, links: [], switches: plan.map(x => {
              const go = ['free', 'in_project'].includes(x.state) && chosen.includes(x.id);
              if (go && x.state === 'free') {
                s.reservations.push({ id: s.nextId++, switch: x.id, user: s.me.id, project: p.id, creation_date: new Date().toISOString(),
                  end_date: new Date(Date.now() + 7 * DAY).toISOString(), renewals_left: 2, admin_exception: false });
              }
              return { id: x.id, model: x.model, state: go ? 'waiting' : 'left_out', warnings: [], free_on: x.free_on,
                detail: go ? 'Waiting for the Switch worker.' : chosen.includes(x.id) ? x.detail : 'Not chosen.', updated_at: new Date().toISOString() };
            }) };
          s.restores[p.id] = restore;
          return json(201, restore);
        }
        if (rest === 'POST archive/') {
          if (!owns) return json(403, { detail: "Only the Project's owner may archive it." });
          if (s.reservations.some(r => r.project === p.id)) return json(400, { detail: 'This Project still holds Switches: Release them first.' });
          Object.assign(p, { archived_at: new Date().toISOString(), invite_token: null });
          return json(200, this.projectSummary(p));
        }
      }
      return null;
    }

    if (key === 'GET list_shared_topologies/') {
      return json(200, {
        shared_with_me: s.shares.filter(x => x.target === s.me.id).map(x => ({
          id: x.id, owner_id: x.owner, owner_username: this.user(x.owner).username, shared_at: x.created_at, direction: 'received' })),
        shared_by_me: s.shares.filter(x => x.owner === s.me.id).map(x => ({
          id: x.id, target_id: x.target, target_username: this.user(x.target).username, shared_at: x.created_at, direction: 'shared' })),
      });
    }
    if (key === 'POST share_topology/') {
      const target = s.users.find(u => u.username === body?.target_username);
      if (!target) return json(404, { detail: 'Target user does not exist.' });
      if (s.shares.some(x => x.owner === s.me.id && x.target === target.id)) return json(409, { detail: 'Topology already shared with this user.' });
      s.shares.push({ id: s.nextId++, owner: s.me.id, target: target.id, created_at: new Date().toISOString() });
      return json(201, { detail: 'Topology shared successfully.' });
    }
    if ((m = path.match(/^unshare_topology\/(\d+)\/$/)) && method === 'DELETE') {
      s.shares = s.shares.filter(x => x.id !== Number(m[1]));
      return json(200, { detail: 'Topology unshared successfully.' });
    }

    if (key === 'GET my_lab/') return json(200, this.myLab());

    if ((m = path.match(/^switches\/(\d+)\/config\/$/)) && method === 'GET') {
      return this.switchConfig(Number(m[1]), query.get('show'), query.get('against'));
    }

    // Contribute a cable (#58): s.contribute overrides the proposal; a POST takes the cable in
    // once s.contributionPlugged is set, and otherwise says there is no link; s.contributionRefused
    // refuses the proposal with that reason
    if (key === 'GET contribute/cable/' && !query.get('switch')) {
      const mayAdd = (sw) => { const r = this.holderOf(sw.id); return !r || r.user === s.me?.id; };
      return json(200, { switches: s.switches.filter(sw => !this.isTool(sw.id) && mayAdd(sw)).map(sw => ({
        id: sw.id, model: sw.model, mngt_IP: sw.mngt_IP, ports: s.ports.filter(p => p.switch === sw.id).length })) });
    }
    if (key === 'GET contribute/cable/') {
      if (s.contributionRefused) return json(400, { detail: s.contributionRefused });
      // The other pairs that share a speed (#82): copper 1/1/23-24 with copper 3/12-13, the 25G cage 1/1/25 with 10G cages
      const copper = (bb) => [{ port: bb, speeds: '10/100/1G' }, { port: bb.replace(/\d+$/, n => Number(n) + 1), speeds: '10/100/1G' }];
      const choices = (bb) => [{ port: '1/1/23', speeds: '10/100/1G', backbone_ports: copper(bb) },
        { port: '1/1/24', speeds: '10/100/1G', backbone_ports: copper(bb) },
        { port: '1/1/25', speeds: '1G/10G/25G', backbone_ports: [{ port: '1/2/9', speeds: '10G' }, ...copper(bb)] }];
      const c = { switch_port: '1/1/24', backbone: '10.69.144.1', backbone_port: '3/12', backbones: ['10.69.144.1', '10.69.144.2'],
        problem: null, choices: choices('3/12'), ...(s.contribute || {}) };
      if (query.get('backbone')) Object.assign(c, { backbone: query.get('backbone'), backbone_port: '3/40', choices: choices('3/40') });
      return json(200, { proposal: c });
    }
    if (key === 'POST contribute/cable/') {
      if (!s.contributionPlugged) {
        return json(400, { detail: `No link between ${body.switch_port} and port ${body.backbone_port} of Backbone ${body.backbone}. Check the cable is in at both ends (and the optics, if any), then press Plugged again.` });
      }
      const port = { id: 900 + s.ports.length, switch: body.switch, port_switch: body.switch_port, backbone: body.backbone,
        port_backbone: body.backbone_port, svlan: null, status: 'DOWN' };
      s.ports.push(port);
      const sw = s.switches.find(x => x.id === body.switch);
      return json(201, { detail: `Thank you: ${sw.model} ${sw.mngt_IP} ${port.port_switch} is now a Port, to Backbone ${port.backbone} port ${port.port_backbone}.`, port });
    }
    // Contribute a Switch (#59): s.switchContributions as contribute/switch/ gives them; Done works
    // once s.contributedSwitchAnswers is set, and otherwise says nothing answers
    if (key === 'GET contribute/switch/') {
      return json(200, { contributions: s.switchContributions || [], models: ['OS6860E-24', 'OS6900-X20'] });
    }
    if (key === 'POST contribute/switch/') {
      if (!body.has_emp) return json(400, { detail: "A Switch without an EMP can't be contributed yet. Ask an admin." });
      s.switchContributions = s.switchContributions || [];
      const ip = `10.69.144.${150 + s.switchContributions.length}`;
      const c = { id: 70 + s.switchContributions.length, model: body.model, mngt_IP: ip, has_emp: true, state: 'held',
        detail: '', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 86400000).toISOString(), expired: false, switch: null, steps: [
          { title: 'Set the EMP address', where: "At the Switch's console", note: 'commit boot keeps it.',
            commands: ['modify boot parameters', `boot empipaddr ${ip}`, 'boot empmasklength 26', 'commit boot', 'commit system', 'exit'] },
          { title: 'Add the routes and local logins, and save', where: 'Back at the CLI', note: "Leave admin's password.",
            commands: ['ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1', 'write memory'] },
          { title: 'Cable the EMP and reload', where: 'The EMP port', note: 'Then press Done.',
            commands: ['reload from working no rollback-timeout'] }] };
      s.switchContributions.unshift(c);
      return json(201, { contribution: c });
    }
    if ((m = key.match(/^POST contribute\/switch\/(\d+)\/(done|cancel)\/$/))) {
      const c = (s.switchContributions || []).find(x => x.id === Number(m[1]));
      if (!c) return json(404, { detail: 'Not found.' });
      if (m[2] === 'cancel') {
        s.switchContributions = s.switchContributions.filter(x => x !== c);
        return json(200, { detail: `${c.mngt_IP} is free again.` });
      }
      if (!s.contributedSwitchAnswers) return json(400, { detail: `Nothing answers SSH at ${c.mngt_IP} yet. Check the EMP address.` });
      Object.assign(c, { state: 'onboarding', detail: `Found a ${c.model} at ${c.mngt_IP}: BLab is taking it in.` });
      return json(200, { contribution: c });
    }
    if (key === 'GET lab_status/') {
      const switches = [...s.switches].sort((a, b) => a.model.localeCompare(b.model) || a.id - b.id).map(sw => {
        const reservation = this.holderOf(sw.id);
        const inspection = s.inspections[sw.id] || null;
        return {
          id: sw.id, mngt_IP: sw.mngt_IP, model: sw.model,
          holder: reservation ? this.user(reservation.user).username : null,
          holder_id: reservation ? reservation.user : null,
          end_date: reservation ? reservation.end_date : null,
          renewals_left: reservation ? reservation.renewals_left : 0,
          admin_exception: reservation ? reservation.admin_exception : false,
          inspection,
          quarantine: s.quarantines[sw.id] || null,
          out_of_service: sw.unavailable?.state === 'out_of_service'
            ? { reason: sw.unavailable.reason.replace(/^Out of service: /, ''), since: new Date().toISOString() } : null,
          cleaning_up: s.cleaningUp.includes(sw.id),
          missing_cables: (s.missingCables || {})[sw.id] || [],
          aos_release: (s.aosReleases || {})[sw.id] || null,
          lab_release: (s.labReleaseOf || {})[sw.id] || null,
          history: inspection ? [inspection] : [],
        };
      });
      return json(200, { switches, lab_releases: s.labReleases || [], sweep: s.sweep || null });
    }
    if (key === 'POST recheck/') {
      const id = Number(body?.switch);
      if (!s.quarantines[id]) return json(400, { detail: 'This Switch is not in Quarantine.' });
      return json(200, { clean: false, reasons: s.quarantines[id].reasons, detail: 'Still not clean: Unwanted cable on 1/1/5.' });
    }
    return null;
  }
}
