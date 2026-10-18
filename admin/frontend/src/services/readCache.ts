/** Tab-local read cache. A successful session handshake is required before use. */
const STORAGE_KEY = "rag-admin-read-cache-v1";
const MAX_AGE_MS = 10 * 60_000;
const MAX_ENTRIES = 40;
const MAX_BYTES = 2 * 1024 * 1024;
interface Entry { value: unknown; savedAt: number }
let scope: string | null = null;
let generation = 0;
const entries = new Map<string, Entry>();
const pending = new Map<string, Promise<unknown>>();

function persist() {
  try {
    if (!scope) { sessionStorage.removeItem(STORAGE_KEY); return; }
    let data = JSON.stringify({ scope, entries: [...entries] });
    while (data.length * 2 > MAX_BYTES && entries.size) {
      entries.delete(entries.keys().next().value!);
      data = JSON.stringify({ scope, entries: [...entries] });
    }
    sessionStorage.setItem(STORAGE_KEY, data);
  } catch { /* Disabled/full storage still permits an in-memory cache. */ }
}

export function activateReadCache(nextScope: string) {
  if (scope === nextScope) return;
  generation += 1; pending.clear(); entries.clear(); scope = nextScope;
  try {
    const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "null");
    if (stored?.scope === scope && Array.isArray(stored.entries)) {
      for (const row of stored.entries.slice(-MAX_ENTRIES)) {
        if (Array.isArray(row) && typeof row[0] === "string" && typeof row[1]?.savedAt === "number"
          && Date.now() - row[1].savedAt >= 0 && Date.now() - row[1].savedAt < MAX_AGE_MS) entries.set(row[0], row[1]);
      }
    }
  } catch { /* A malformed cache is disposable. */ }
  persist();
}

export function clearReadCache() {
  scope = null; generation += 1; entries.clear(); pending.clear(); persist();
}

export function invalidateReadCache(prefix = "") {
  // In-flight reads from before a mutation must not repopulate stale entries.
  generation += 1; pending.clear();
  for (const key of entries.keys()) if (key.startsWith(prefix)) entries.delete(key);
  persist();
}

function abortable<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => { cleanup(); reject(signal.reason ?? new DOMException("Aborted", "AbortError")); };
    const cleanup = () => signal.removeEventListener("abort", abort);
    signal.addEventListener("abort", abort, { once: true });
    promise.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
  });
}

/** Shared GETs finish independently of a cancelled view; callers receive copies. */
export async function cachedRead<T>(key: string, fetchValue: () => Promise<T>, options: { fresh?: boolean; signal?: AbortSignal | null } = {}): Promise<T> {
  options.signal?.throwIfAborted();
  if (!scope) return abortable(fetchValue(), options.signal);
  const owner = scope;
  if (!options.fresh) {
    const entry = entries.get(key);
    if (entry && Date.now() - entry.savedAt < MAX_AGE_MS) {
      entries.delete(key); entries.set(key, entry);
      return structuredClone(entry.value) as T;
    }
  } else {
    // Explicit refresh supersedes an older in-flight read as well as saved data.
    invalidateReadCache(key);
  }
  let promise = pending.get(key);
  if (!promise) {
    const startedGeneration = generation;
    promise = fetchValue().then(value => {
      if (scope !== owner) throw new DOMException("Session changed", "AbortError");
      if (generation === startedGeneration) {
        entries.delete(key); entries.set(key, { value: structuredClone(value), savedAt: Date.now() });
        while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!);
        persist();
      }
      return value;
    });
    pending.set(key, promise);
    const thisPromise = promise;
    void promise.finally(() => { if (pending.get(key) === thisPromise) pending.delete(key); }).catch(() => undefined);
  }
  return structuredClone(await abortable(promise as Promise<T>, options.signal));
}
