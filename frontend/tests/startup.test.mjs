import assert from "node:assert/strict";
import test from "node:test";
import { retryStartup } from "../app/_workbench/startup.ts";

const fast = { budgetMs: 300, attemptMs: 30, intervalMs: 1 };
const failure = status => Object.assign(new Error("unavailable"), { status });
const untilAborted = signal => new Promise((resolve, reject) => {
  signal.addEventListener("abort", () => reject(signal.reason), { once: true });
});

test("a sleeping server recovers after transient proxy and network failures", async () => {
  let calls = 0;
  const result = await retryStartup(async () => {
    calls++;
    if (calls === 1) throw failure(502);
    if (calls === 2) throw failure(0);
    return { id: 1 };
  }, new AbortController().signal, fast);
  assert.deepEqual(result, { id: 1 });
  assert.equal(calls, 3);
});

test("401 and other client errors reach the caller immediately", async () => {
  for (const status of [401, 403, 404, 429]) {
    let calls = 0;
    await assert.rejects(retryStartup(async () => {
      calls++;
      throw failure(status);
    }, new AbortController().signal, fast), { status });
    assert.equal(calls, 1);
  }
});

test("a stalled request is aborted and the next attempt can recover", async () => {
  let calls = 0;
  const result = await retryStartup(signal => {
    calls++;
    return calls === 1 ? untilAborted(signal) : Promise.resolve("ready");
  }, new AbortController().signal, fast);
  assert.equal(result, "ready");
  assert.equal(calls, 2);
});

test("persistent failures stop at the startup deadline", async () => {
  await assert.rejects(retryStartup(untilAborted, new AbortController().signal,
    { budgetMs: 40, attemptMs: 15, intervalMs: 1 }), /잠시 후 다시 시도/);
});

test("leaving the screen cancels an in-flight attempt", async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = retryStartup(signal => {
    calls++;
    return untilAborted(signal);
  }, controller.signal, fast);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(calls, 1);
});

test("leaving during the retry delay prevents another request", async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = retryStartup(async () => {
    calls++;
    setTimeout(() => controller.abort(), 5);
    throw failure(503);
  }, controller.signal, { ...fast, intervalMs: 100 });
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(calls, 1);
});
