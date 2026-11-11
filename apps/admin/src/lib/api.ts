/**
 * Tiny fetch wrapper for the admin API (docs/ADMIN_API.md).
 *
 * - always same-origin credentials (session cookie)
 * - JSON in/out, `X-Jarvis-Admin: 1` on every non-GET (CSRF guard)
 * - 401 → login screen, 403 `totp_setup_required` → TOTP setup screen (via `onAuthProblem`)
 */

export type AuthProblem = "login" | "totp_setup";

/** One pydantic validation error, as returned in a 422 `detail`. */
export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(errorMessage(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** Validation issues when this is a 422 with a pydantic error list, else []. */
  get issues(): ValidationIssue[] {
    return validationIssues(this.detail);
  }
}

export function validationIssues(detail: unknown): ValidationIssue[] {
  if (!Array.isArray(detail)) return [];
  return detail
    .filter((d): d is ValidationIssue => !!d && typeof d === "object" && "msg" in d)
    .map((d) => ({ loc: Array.isArray(d.loc) ? d.loc : [], msg: String(d.msg), type: d.type }));
}

/** Human-readable message for an error response. */
export function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) {
    if (detail === "totp_required") return "Enter your 6-digit code.";
    if (detail === "totp_setup_required") return "Two-factor setup required.";
    return detail;
  }
  const issues = validationIssues(detail);
  if (issues.length) return issues.map(formatIssue).join("; ");
  if (status === 429) return "Too many attempts — wait a minute and try again.";
  if (status === 0) return "Can't reach JARVIS.";
  return `Request failed (${status}).`;
}

export function formatIssue(i: ValidationIssue): string {
  // Drop FastAPI's "body" prefix for request-body errors.
  const loc = i.loc[0] === "body" ? i.loc.slice(1) : i.loc;
  return loc.length ? `${loc.join(".")}: ${i.msg}` : i.msg;
}

/**
 * Decide whether an error response means the user must (re)authenticate.
 * The login endpoint's own 401s ("Wrong password", "totp_required") are handled by the form.
 */
export function authProblemFor(status: number, detail: unknown, path: string): AuthProblem | null {
  if (path.startsWith("/api/auth/login")) return null;
  if (status === 401) return "login";
  if (status === 403 && detail === "totp_setup_required") return "totp_setup";
  return null;
}

type Listener = (p: AuthProblem) => void;
const listeners = new Set<Listener>();

export function onAuthProblem(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** JSON-serialised unless it's a string (sent raw, e.g. YAML import). */
  body?: unknown;
  contentType?: string;
  signal?: AbortSignal;
  /** Let the request outlive the page (used to turn the mic monitor off on tab close). */
  keepalive?: boolean;
}

export function buildInit(opts: RequestOptions): RequestInit {
  const method = opts.method ?? (opts.body === undefined ? "GET" : "POST");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Jarvis-Admin"] = "1";
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    if (typeof opts.body === "string") {
      body = opts.body;
      headers["Content-Type"] = opts.contentType ?? "text/plain";
    } else {
      body = JSON.stringify(opts.body);
      headers["Content-Type"] = "application/json";
    }
  }
  return { method, headers, body, credentials: "same-origin", signal: opts.signal, keepalive: opts.keepalive };
}

async function readDetail(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => "");
  try {
    const j = JSON.parse(text) as { detail?: unknown };
    return j && typeof j === "object" && "detail" in j ? j.detail : j;
  } catch {
    return text || null;
  }
}

/** Low-level: returns the Response, throws ApiError for non-2xx. */
export async function request(path: string, opts: RequestOptions = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, buildInit(opts));
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, null);
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    const problem = authProblemFor(res.status, detail, path);
    if (problem) listeners.forEach((fn) => fn(problem));
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await request(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
