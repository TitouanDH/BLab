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

const DAY = 24 * 60 * 60 * 1000;

export function defaultState(now = Date.now()) {
  const iso = (offsetDays) => new Date(now + offsetDays * DAY).toISOString();
  return {
    me: { id: 1, username: 'alice' },
    users: [
      { id: 1, username: 'alice' },
      { id: 2, username: 'bob' },
      { id: 3, username: 'carol' },
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
    teardownErrors: {},  // by SVLAN: why its disconnect failed (the Link shows again)
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
    // Switch accounts that BLab couldn't create, by switch id: why (the others are ready)
    accountErrors: {},
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
      answer = answer || this.handle(method, path, body);
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
  // The holder, or a user the holder shares their Topology with
  mayWork(ownerId) {
    const me = this.state.me.id;
    return ownerId === me || this.state.shares.some(x => x.owner === ownerId && x.target === me);
  }

  // The Switch accounts of the user logged in: one per Switch they may work on (theirs, and
  // those of the Topologies shared with them), as GET switch_accounts/ gives them
  switchAccounts() {
    const s = this.state;
    const owners = [s.me.id, ...s.shares.filter(x => x.target === s.me.id).map(x => x.owner)];
    return s.reservations.filter(r => owners.includes(r.user)).map(r => {
      const sw = s.switches.find(x => x.id === r.switch);
      const error = s.accountErrors[r.switch] || null;
      return { switch: sw.id, mngt_IP: sw.mngt_IP, holder: this.user(r.user).username, name: s.me.username,
        password: error ? null : `Pw-${s.me.username}-${sw.id}x`, state: error ? 'failed' : 'ready', error };
    });
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

  handle(method, path, body) {
    const s = this.state;
    const key = `${method} ${path}`;
    let m;

    if (key === 'POST login/') {
      const user = s.users.find(u => u.username === body?.username);
      if (!user || body?.password !== 'secret') return json(401, { detail: 'Invalid credentials.' });
      s.me = user;
      return json(202, { token: `token-${user.id}`, user, is_staff: false });
    }
    if (key === 'POST signup/') {
      if (s.users.some(u => u.username === body?.username)) {
        return json(400, { username: ['A user with that username already exists.'] });
      }
      const user = { id: s.nextId++, username: body.username };
      s.users.push(user);
      return json(201, { token: `token-${user.id}`, user });
    }
    if (key === 'GET logout/') return json(200, { detail: 'Logout successful.' });
    if (key === 'GET list_user/') return json(200, { users: s.users });
    if ((m = path.match(/^list_user\/(\d+)\/$/)) && method === 'GET') {
      const user = this.user(m[1]);
      return user ? json(200, user) : json(404, { detail: 'Not found.' });
    }
    if (key === 'GET list_switch/') {
      // A Switch whose Cleanup runs can't be reserved until the Switch worker is done
      return json(200, { switchs: s.switches.map(sw => (!sw.unavailable && s.cleaningUp.includes(sw.id)
        ? { ...sw, unavailable: { state: 'cleaning_up', reason: 'Being Cleaned up: BLab is reloading and Inspecting it.' } }
        : sw)) });
    }
    if (key === 'GET list_reservation/') {
      return json(200, s.reservations.map(r => ({ ...r, username: this.user(r.user).username, may_work: this.mayWork(r.user) })));
    }
    if (key === 'GET list_port/') return json(200, { ports: s.ports });

    if (key === 'POST reserve/') {
      const sw = s.switches.find(x => x.id === Number(body?.switch));
      if (!sw) return json(404, { detail: 'Not found.' });
      if (this.holderOf(sw.id)) return json(400, { warning: 'This switch is already reserved.' });
      if (sw.unavailable) return json(400, { detail: `This Switch can't be reserved. ${sw.unavailable.reason}` });
      s.reservations.push({ id: s.nextId++, switch: sw.id, user: s.me.id, creation_date: new Date().toISOString(), end_date: body.end_date, renewals_left: 2, admin_exception: false });
      return json(201, { detail: 'Reservation successful.', switch_account: this.switchAccounts().find(a => a.switch === sw.id) });
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
      const shared = s.shares.some(x => x.owner === ownerId && x.target === s.me.id);
      if (ownerId !== s.me.id && !shared) return json(403, { detail: 'This topology is not shared with you.' });
      const own = s.reservations.filter(r => r.user === ownerId).map(r => r.switch);
      const ownPorts = s.ports.filter(p => own.includes(p.switch));
      // A Link being disconnected is left out, unless its teardown failed
      const svlans = [...new Set(ownPorts.filter(p => p.svlan).map(p => p.svlan))]
        .filter(v => !this.linkPorts(v).some(p => this.teardownPending(p)) || s.teardownErrors[v]);
      const farPorts = svlans.flatMap(v => this.linkPorts(v)).filter(p => !own.includes(p.switch));
      const farIds = [...new Set(farPorts.map(p => p.switch))];
      return json(200, {
        switches: [
          ...s.switches.filter(x => own.includes(x.id)).map(x => ({ ...x, in_topology: true, reservation: this.holderOf(x.id) })),
          ...s.switches.filter(x => farIds.includes(x.id)).map(x => ({ ...x, in_topology: false })),
        ],
        ports: [...ownPorts, ...farPorts].map(p => ({ teardown_requested_at: null, teardown_svlan: null, teardown_error: null, ...p,
          ...(s.teardownErrors[p.svlan] && this.teardownPending(p) ? { teardown_error: s.teardownErrors[p.svlan] } : {}) })),
        links: svlans.map(v => ({ svlan: v, ports: this.linkPorts(v).map(p => p.id), teardown_error: s.teardownErrors[v] || null })),
        may_work: true,
      });
    }
    if (key === 'POST connect/') {
      const a = s.ports.find(p => p.id === Number(body?.portA));
      const b = s.ports.find(p => p.id === Number(body?.portB));
      if (!a || !b) return json(404, { detail: 'Not found.' });
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

    if (key === 'GET switch_accounts/') return json(200, { switch_accounts: this.switchAccounts() });

    if (key === 'GET lab_status/') {
      const switches = [...s.switches].sort((a, b) => a.mngt_IP.localeCompare(b.mngt_IP)).map(sw => {
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
          history: inspection ? [inspection] : [],
        };
      });
      return json(200, { switches });
    }
    if (key === 'POST recheck/') {
      const id = Number(body?.switch);
      if (!s.quarantines[id]) return json(400, { detail: 'This Switch is not in Quarantine.' });
      return json(200, { clean: false, reasons: s.quarantines[id].reasons, detail: 'Still not clean: Unwanted cable on 1/1/5.' });
    }
    return null;
  }
}
