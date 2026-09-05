// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// Execute the served source with a controlled clock and unresolved browser requests.
const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(0, "utf8");
const pointerTtlMs = Number(process.argv[2]);
const flush = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function page({ query, frame = false } = {}) {
  let now = 10000;
  const requests = [], timers = [], listeners = new Map();
  const canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 }) };
  const window = {
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(fn);
    },
  };
  window.top = frame ? {} : window;
  window.self = window;
  const context = vm.createContext({
    window,
    document: { hidden: false, getElementById: () => canvas, querySelector: () => canvas },
    navigator: query ? { permissions: { query } } : {},
    Date: { now: () => now },
    setInterval: (fn, ms) => timers.push({ fn, ms }),
    fetch: (url, options) => {
      const result = deferred();
      requests.push({ ...result, url, body: JSON.parse(options.body), at: now });
      return result.promise;
    },
  });
  const run = () => vm.runInContext(source, context);
  run();
  return {
    requests, timers, context, run,
    move(x = 400, y = 300) {
      for (const fn of listeners.get("mousemove") || []) fn({ clientX: x, clientY: y });
    },
    async tick(count = 1) {
      for (let i = 0; i < count; i++) {
        now += 100;
        for (const { fn, ms } of timers) if (now % ms === 0) fn();
        await flush();
      }
    },
  };
}

test("Onshape owns the first grant; pointer traffic waits through query, prompt and denial", async () => {
  const query = deferred();
  const p = page({ query: () => query.promise });
  p.move();
  await p.tick(100);
  assert.equal(p.requests.length, 0, "query still pending");
  const permission = { state: "prompt" };
  query.resolve(permission);
  await flush();
  await p.tick(100);
  assert.equal(p.requests.length, 0, "must not compete with Onshape's permission prompt");
  permission.state = "denied";
  await p.tick(100);
  assert.equal(p.requests.length, 0, "denial must not start a retry loop");
  permission.state = "granted";
  await p.tick();
  assert.equal(p.requests.length, 1);
  p.requests[0].resolve({ ok: true });
  await flush();
  permission.state = "prompt";
  await p.tick(10);
  assert.equal(p.requests.length, 1, "revocation stops subsequent posts");
  permission.state = "granted";
  await p.tick();
  assert.equal(p.requests.length, 2);
});

test("a pending fetch never overlaps, even when the pointer keeps moving", async () => {
  const p = page(); // Browser without a queryable network permission.
  await flush();
  p.move();
  await p.tick();
  for (let i = 0; i < 100; i++) {
    p.move(500 + i, 200);
    await p.tick();
  }
  assert.equal(p.requests.length, 1);
  p.requests[0].resolve({ ok: true });
  await flush();
  await p.tick();
  assert.equal(p.requests.length, 2);
  assert.equal(p.requests[1].body.ndc_x, 599 / 800 * 2 - 1, "send the latest sample, no queue");
});

test("query the loopback permission first and fall back only for an unsupported name", async () => {
  const names = [];
  const permission = { state: "prompt" };
  const p = page({ query: async ({ name }) => {
    names.push(name);
    if (name === "loopback-network") throw new TypeError("unsupported");
    return permission;
  } });
  await flush();
  p.move();
  await p.tick(50);
  assert.deepEqual(names, ["loopback-network", "local-network-access"]);
  assert.equal(p.requests.length, 0);
  permission.state = "granted";
  await p.tick();
  assert.equal(p.requests.length, 1);
});

test("unsupported queries allow serialized transport, other query errors do not grant access", async () => {
  for (const name of ["TypeError", "InvalidStateError"]) {
    const p = page({ query: () => { const error = new Error("query failed"); error.name = name; throw error; } });
    await flush();
    p.move();
    await p.tick(50);
    assert.equal(p.requests.length, name === "TypeError" ? 1 : 0);
  }
});

test("successful sends cap motion traffic and refresh a stationary sample inside its TTL", async () => {
  const p = page({ query: async () => ({ state: "granted" }) });
  await flush();
  for (let i = 0; i < 1000; i++) p.move(500, 200);
  assert.equal(p.requests.length, 0);
  await p.tick();
  assert.equal(p.requests.length, 1);
  assert.equal(p.requests[0].body.ndc_x, 0.25);
  assert.equal(p.requests[0].body.on_canvas, true);
  p.requests[0].resolve({ ok: true });
  await flush();
  await p.tick(2);
  assert.equal(p.requests.length, 1);
  await p.tick();
  assert.equal(p.requests.length, 2);
  assert.equal(p.requests[1].at - p.requests[0].at, 300);
  assert.ok(p.requests[1].at - p.requests[0].at < pointerTtlMs);
});

test("network errors and HTTP failures back off, success restores the refresh rate", async () => {
  const p = page();
  await flush();
  p.move();
  await p.tick();
  let previousDelay = 0;
  for (let i = 0; i < 7; i++) {
    const request = p.requests[i];
    if (i % 2) request.resolve({ ok: false, status: 503 });
    else request.reject(new Error("offline"));
    await flush();
    await p.tick();
    assert.equal(p.requests.length, i + 1, "must not retry on the next timer tick");
    for (let t = 1; p.requests.length === i + 1 && t <= 50; t++) await p.tick();
    assert.equal(p.requests.length, i + 2, "backoff capped at five seconds");
    const delay = p.requests[i + 1].at - request.at;
    assert.ok(delay >= previousDelay && delay <= 5000);
    previousDelay = delay;
  }
  p.requests[7].resolve({ ok: true });
  await flush();
  await p.tick(3);
  assert.equal(p.requests.length, 9);
});

test("synchronous fetch errors also back off and release the pending slot", async () => {
  const p = page();
  const fetch = p.context.fetch;
  let attempts = 0;
  p.context.fetch = (...args) => {
    if (++attempts === 1) throw new Error("synchronous failure");
    return fetch(...args);
  };
  await flush();
  p.move();
  await p.tick(2);
  assert.equal(attempts, 1);
  await p.tick(50);
  assert.equal(attempts, 2);
});

test("duplicate installation and subframes cannot create extra pointer senders", async () => {
  const p = page();
  p.run();
  await flush();
  p.move();
  await p.tick(10);
  assert.equal(p.requests.length, 1);
  assert.equal(p.timers.length, 1);
  const child = page({ frame: true });
  await flush();
  child.move();
  await child.tick(10);
  assert.equal(child.requests.length, 0);
});
