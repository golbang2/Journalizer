const STARTUP_ERROR = "서버 연결이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.";

/** Retry only the initial read while a sleeping server wakes up. Never replay writes. */
export async function retryStartup<T>(
  read: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
  { budgetMs = 120_000, attemptMs = 15_000, intervalMs = 3_000 } = {},
): Promise<T> {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    signal.throwIfAborted();
    const attempt = new AbortController();
    const cancel = () => attempt.abort();
    signal.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(cancel, Math.min(attemptMs, deadline - Date.now()));
    try {
      return await read(attempt.signal);
    } catch (error) {
      signal.throwIfAborted();
      const status = (error as { status?: number })?.status;
      // An unauthenticated response means the server is ready. Other client errors
      // also belong to the caller, rather than the cold-start retry loop.
      if (!attempt.signal.aborted && status !== 0 && status !== 500 && status !== 502 && status !== 503 && status !== 504) throw error;
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener("abort", cancel);
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await new Promise<void>((resolve, reject) => {
      const cancel = () => { clearTimeout(timer); reject(signal.reason); };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", cancel);
        resolve();
      }, Math.min(intervalMs, remaining));
      signal.addEventListener("abort", cancel, { once: true });
    });
  }
  signal.throwIfAborted();
  throw new Error(STARTUP_ERROR);
}
