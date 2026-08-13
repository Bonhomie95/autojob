/**
 * Fetch wrapper for the Flask JSON API.
 *
 * - API_BASE is empty in dev (Vite proxies /api to the backend, same-origin)
 *   and set to the deployed backend's absolute URL in production (frontend
 *   on Vercel, backend on Render — different origins). Cookies still flow
 *   cross-site via credentials:"include" + the backend's CORS/SameSite=None
 *   config (see autojob/__init__.py's _register_cors).
 * - CSRF: Flask-WTF's CSRFProtect checks the X-CSRFToken header on every
 *   mutating request. We fetch one token lazily from GET /api/auth/csrf and
 *   cache it — but the session (and therefore the token bound to it) rotates
 *   on login/logout/register, so those three call sites clear the cache via
 *   resetCsrfToken() to force a fresh fetch on the next mutation.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiClientError extends Error {
  status: number;
  body: { error?: string; errors?: Record<string, string> } | null;

  constructor(status: number, body: ApiClientError["body"]) {
    super(body?.error || "Request failed");
    this.status = status;
    this.body = body;
  }
}

let csrfTokenPromise: Promise<string> | null = null;

async function fetchCsrfToken(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/csrf`, { credentials: "include" });
  const data = await res.json();
  return data.csrfToken as string;
}

async function ensureCsrfToken(): Promise<string> {
  if (!csrfTokenPromise) {
    csrfTokenPromise = fetchCsrfToken();
  }
  return csrfTokenPromise;
}

export function resetCsrfToken(): void {
  csrfTokenPromise = null;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  json?: unknown;
  formData?: FormData;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;

  const isMutating = method !== "GET";
  if (isMutating) {
    headers["X-CSRFToken"] = await ensureCsrfToken();
  }

  if (opts.formData) {
    body = opts.formData; // browser sets multipart Content-Type + boundary
  } else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }

  const res = await fetch(`${API_BASE}${path}`, { method, headers, body, credentials: "include" });

  const contentType = res.headers.get("content-type") ?? "";
  const data = contentType.includes("application/json") ? await res.json() : null;

  if (!res.ok) {
    throw new ApiClientError(res.status, data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, json?: unknown) => request<T>(path, { method: "POST", json }),
  postForm: <T>(path: string, formData: FormData) =>
    request<T>(path, { method: "POST", formData }),
};

/** Direct (non-JSON) download URL — used for <a href> to a job's document. */
export function downloadUrl(jobId: string, filename: string): string {
  return `${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/file/${encodeURIComponent(filename)}`;
}
