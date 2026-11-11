import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, authProblemFor, buildInit, errorMessage, formatIssue, get, onAuthProblem, post, type AuthProblem } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("authProblemFor", () => {
  it("maps 401 to the login screen", () => {
    expect(authProblemFor(401, "Not signed in.", "/api/overview")).toBe("login");
  });
  it("maps 403 totp_setup_required to the setup screen", () => {
    expect(authProblemFor(403, "totp_setup_required", "/api/config")).toBe("totp_setup");
  });
  it("ignores other 403s (e.g. CSRF) and other statuses", () => {
    expect(authProblemFor(403, "Missing CSRF header.", "/api/config")).toBeNull();
    expect(authProblemFor(422, [], "/api/config/voice")).toBeNull();
    expect(authProblemFor(500, null, "/api/overview")).toBeNull();
  });
  it("leaves the login endpoint's own 401s to the form", () => {
    expect(authProblemFor(401, "totp_required", "/api/auth/login")).toBeNull();
    expect(authProblemFor(401, "Wrong password.", "/api/auth/login")).toBeNull();
  });
});

describe("errorMessage", () => {
  it("uses string details", () => {
    expect(errorMessage(400, "Current password is wrong.")).toBe("Current password is wrong.");
  });
  it("formats pydantic issues", () => {
    const detail = [{ loc: ["wake_threshold"], msg: "Input should be less than or equal to 0.99", type: "x" }];
    expect(errorMessage(422, detail)).toBe("wake_threshold: Input should be less than or equal to 0.99");
  });
  it("strips FastAPI's body prefix", () => {
    expect(formatIssue({ loc: ["body", "new"], msg: "too short" })).toBe("new: too short");
  });
  it("has fallbacks for rate limiting and network errors", () => {
    expect(errorMessage(429, null)).toMatch(/Too many/);
    expect(errorMessage(0, null)).toMatch(/reach/);
  });
});

describe("buildInit", () => {
  it("GET: same-origin, no CSRF header, no body", () => {
    const init = buildInit({});
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>)["X-Jarvis-Admin"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });
  it("non-GET: CSRF header and JSON body", () => {
    const init = buildInit({ method: "PUT", body: { a: 1 } });
    const h = init.headers as Record<string, string>;
    expect(h["X-Jarvis-Admin"]).toBe("1");
    expect(h["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
  });
  it("string bodies are sent raw", () => {
    const init = buildInit({ method: "POST", body: "a: 1\n", contentType: "application/x-yaml" });
    expect(init.body).toBe("a: 1\n");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/x-yaml");
  });
});

describe("request", () => {
  it("returns parsed JSON and sends the CSRF header on POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(post("/api/voice/say", { text: "hi" })).resolves.toEqual({ ok: true });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-Jarvis-Admin"]).toBe("1");
  });

  it("throws ApiError with the detail and notifies auth listeners on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Not signed in." })));
    const seen: AuthProblem[] = [];
    const off = onAuthProblem((p) => seen.push(p));
    const err = await get("/api/overview").catch((e: unknown) => e);
    off();
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect(seen).toEqual(["login"]);
  });

  it("exposes 422 validation issues", async () => {
    const detail = [{ loc: ["providers", 0, "id"], msg: "String should match pattern", type: "string_pattern_mismatch" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail })));
    const err = (await post("/api/x", {}).catch((e: unknown) => e)) as ApiError;
    expect(err.issues).toHaveLength(1);
    expect(err.issues[0]?.loc).toEqual(["providers", 0, "id"]);
  });

  it("maps network failures to status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = (await get("/api/overview").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(0);
  });
});
