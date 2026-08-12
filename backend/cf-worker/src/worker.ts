// Alpha-OSK telemetry endpoint.
//
// Three routes:
//   POST /v1/submit    - upsert a user's lifetime counters
//   GET  /v1/aggregate - return summed counters for the public stats page
//   POST /v1/forget    - delete a user's row (privacy / right-to-be-forgotten)
//
// Plus a scheduled handler that prunes users inactive for >365 days.
//
// Design notes live in docs/architecture/TELEMETRY.md. Privacy story lives in
// docs/PRIVACY.md. Schema lives in schema.sql.
//
// Abuse mitigation on /v1/submit and /v1/forget is keyed on anon_id only,
// never on IP, header, geo or User-Agent -- this worker must never read or
// store those (see the privacy guarantees in docs/architecture/TELEMETRY.md).
// Two layers: an edge rate-limit binding (RATE_LIMITER below, see
// wrangler.toml.example) and a per-anon_id submission cooldown enforced in
// D1 (SUBMIT_COOLDOWN_SECONDS). Neither stops an attacker who cycles through
// many distinct fake anon_ids; that needs an IP-based control, which by
// design lives outside this code -- see wrangler.toml.example for how to add
// it as a Cloudflare dashboard rule instead.

// Minimal structural type for Cloudflare's Workers rate-limiting binding.
// https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
// Declared locally rather than relying on it being exported by whatever
// version of @cloudflare/workers-types happens to be installed.
interface RateLimiter {
    limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
    DB: D1Database;
    // Optional: a worker deployed before the maintainer adds this binding to
    // their real (gitignored) wrangler.toml must keep working, so
    // withinRateLimit() below fails open (treats "binding absent" as
    // "allowed") rather than throwing.
    RATE_LIMITER?: RateLimiter;
}

// Sanity ceilings. A real user will never approach these in a single
// submission; values above the cap are treated as malformed and the
// submission is rejected. Keeps a malicious or buggy client from
// poisoning the public aggregate.
const MAX_KEYSTROKES = 1_000_000_000;     // 1B keystrokes
const MAX_WORDS      = 200_000_000;       // 200M words
const MAX_MINUTES    = 5_000_000;         // ~9.5 years of typing
const MAX_SESSIONS   = 10_000_000;

// Aggregate cache: 5 minutes at the edge.
const AGGREGATE_CACHE_SECONDS = 300;

// Minimum gap, in seconds, between two accepted submissions for the same
// anon_id (enforced in D1, see the WHERE clause in handleSubmit). The
// client's own cadence is weekly plus at most one extra opportunistic submit
// on quit (docs/architecture/TELEMETRY.md "Submit cadence"), so legitimate
// re-submits for one id are realistically hours apart, not seconds. One hour
// is well below that cadence, so it never clips a real user, even one with
// several quit/relaunch cycles in a day, while still capping a single id to
// at most 24 accepted submissions/day instead of unlimited. Applied to BOTH
// statements in handleSubmit (users and submissions_latest); gating only one
// of them would leave the other taking a write per request. Because the
// counters are monotonic lifetime totals, a submission dropped for being
// inside the
// cooldown costs nothing: the next accepted submission for that id carries
// the same up-to-date totals, so nothing is lost, only delayed.
const SUBMIT_COOLDOWN_SECONDS = 60 * 60; // 1 hour

interface SubmitPayload {
    anon_id: string;
    app_version: string;
    os: string;
    keystrokes: number;
    words: number;
    predictions: number;
    keystrokes_saved: number;
    minutes: number;
    sessions: number;
    prediction_offers: number;
}

function badRequest(reason: string): Response {
    return new Response(reason + "\n", {
        status: 400,
        headers: { "Content-Type": "text/plain" },
    });
}

// anon_id must be a UUID4-ish hex string with optional hyphens. We
// don't strictly validate the version/variant nibble because the
// client is the source of truth and any attacker bothering to forge
// these would just match the format anyway -- the validation here is
// a sanity gate, not a security gate.
function isValidAnonId(s: unknown): s is string {
    if (typeof s !== "string") return false;
    if (s.length < 32 || s.length > 64) return false;
    return /^[0-9a-fA-F-]+$/.test(s);
}

function isPositiveInt(n: unknown, max: number): n is number {
    return typeof n === "number" && Number.isFinite(n) && n >= 0
        && n <= max && Number.isInteger(n);
}

function isPositiveNum(n: unknown, max: number): n is number {
    return typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= max;
}

// SemVer 2.0.0 reference regex (semver.org FAQ: "Is there a suggested
// regular expression to check a SemVer string?"). app_version is meant to
// hold exactly this and nothing else. The length check runs first so this
// never evaluates against an attacker-controlled long string.
const SEMVER_RE =
    /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;

function isSemver(s: unknown, max: number): s is string {
    return typeof s === "string" && s.length > 0 && s.length <= max && SEMVER_RE.test(s);
}

// The platforms Alpha-OSK actually ships on (src/platform/__init__.py
// CURRENT_PLATFORM). Deliberately excludes that module's own "unsupported"
// fallback: if that ever reaches us, it is junk for this field's purpose.
const VALID_OS = new Set(["windows", "linux", "macos"]);

function isValidOs(s: unknown): s is string {
    return typeof s === "string" && VALID_OS.has(s);
}

function validatePayload(body: unknown): SubmitPayload | string {
    if (!body || typeof body !== "object") return "body must be a JSON object";
    const b = body as Record<string, unknown>;

    if (!isValidAnonId(b.anon_id)) return "anon_id invalid";
    if (!isSemver(b.app_version, 32)) return "app_version invalid";
    if (!isValidOs(b.os)) return "os invalid";
    if (!isPositiveInt(b.keystrokes, MAX_KEYSTROKES)) return "keystrokes invalid";
    if (!isPositiveInt(b.words, MAX_WORDS)) return "words invalid";
    if (!isPositiveInt(b.predictions, MAX_WORDS)) return "predictions invalid";
    if (!isPositiveInt(b.keystrokes_saved, MAX_KEYSTROKES)) return "keystrokes_saved invalid";
    if (!isPositiveNum(b.minutes, MAX_MINUTES)) return "minutes invalid";
    if (!isPositiveInt(b.sessions, MAX_SESSIONS)) return "sessions invalid";
    if (!isPositiveInt(b.prediction_offers, MAX_WORDS)) return "prediction_offers invalid";

    return b as unknown as SubmitPayload;
}

// Cheap edge-level throttle keyed by a caller-chosen id string (never IP --
// see the module comment at the top of this file). Fails OPEN (returns
// true / "allowed") if the binding isn't configured yet, so shipping this
// code ahead of the maintainer wiring up wrangler.toml's [[ratelimits]]
// block doesn't break submissions; the D1 cooldown in handleSubmit is the
// authoritative backstop either way.
async function withinRateLimit(env: Env, key: string): Promise<boolean> {
    const result = await env.RATE_LIMITER?.limit({ key });
    return result ? result.success : true;
}

async function handleSubmit(req: Request, env: Env): Promise<Response> {
    let body: unknown;
    try {
        body = await req.json();
    } catch {
        return badRequest("body must be valid JSON");
    }

    const validated = validatePayload(body);
    if (typeof validated === "string") return badRequest(validated);

    if (!(await withinRateLimit(env, `submit:${validated.anon_id}`))) {
        // Same 204 a normal submission returns. See the D1 cooldown WHERE
        // clause below: a throttled request must be indistinguishable from
        // an accepted one, or the response becomes an oracle for "does this
        // anon_id exist / was it recently active".
        return new Response(null, { status: 204 });
    }

    const now = Math.floor(Date.now() / 1000);

    // Upsert user. first_seen stays the value already on disk; only
    // last_seen / app_version / os update. The "excluded.first_seen"
    // expression in SQLite ON CONFLICT refers to the would-be-inserted
    // row, which is what we want for first_seen on the insert path.
    //
    // Carries the SAME cooldown WHERE clause as submissions_latest below,
    // and that is the point: without it this statement runs on every
    // request and the cooldown bounds only half the write path, so one
    // anon_id could still drive unlimited writes here while its
    // submissions row sat frozen. The insert path is unaffected (the
    // WHERE only gates DO UPDATE), so a first-ever submission for an id
    // still lands immediately.
    await env.DB.prepare(
        `INSERT INTO users (anon_id, first_seen, last_seen, app_version, os)
         VALUES (?1, ?2, ?2, ?3, ?4)
         ON CONFLICT(anon_id) DO UPDATE SET
            last_seen   = excluded.last_seen,
            app_version = excluded.app_version,
            os          = excluded.os
         WHERE excluded.last_seen - users.last_seen >= ?5`
    ).bind(
        validated.anon_id, now, validated.app_version, validated.os,
        SUBMIT_COOLDOWN_SECONDS,
    ).run();

    // Replace the latest submission row -- but only if the existing row (if
    // any) is older than SUBMIT_COOLDOWN_SECONDS. A same-id submission
    // inside the cooldown hits the WHERE clause, so ON CONFLICT's DO UPDATE
    // silently does nothing: no error, no changed row, and critically the
    // same 204 below either way. We reject by *ignoring*, not by returning a
    // different status, for the same anti-oracle reason as the rate-limit
    // check above: a distinguishable rejection would let an attacker probe
    // whether a given anon_id already has a recent row, i.e. whether it
    // exists at all.
    await env.DB.prepare(
        `INSERT INTO submissions_latest (
            anon_id, ts, keystrokes, words, predictions,
            keystrokes_saved, minutes, sessions, prediction_offers
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(anon_id) DO UPDATE SET
            ts                = excluded.ts,
            keystrokes        = excluded.keystrokes,
            words             = excluded.words,
            predictions       = excluded.predictions,
            keystrokes_saved  = excluded.keystrokes_saved,
            minutes           = excluded.minutes,
            sessions          = excluded.sessions,
            prediction_offers = excluded.prediction_offers
         WHERE excluded.ts - submissions_latest.ts >= ?`
    ).bind(
        validated.anon_id, now,
        validated.keystrokes, validated.words, validated.predictions,
        validated.keystrokes_saved, validated.minutes,
        validated.sessions, validated.prediction_offers,
        SUBMIT_COOLDOWN_SECONDS,
    ).run();

    return new Response(null, { status: 204 });
}

async function handleAggregate(env: Env): Promise<Response> {
    const row = await env.DB.prepare(
        `SELECT
            COUNT(*)                 AS users,
            COALESCE(SUM(keystrokes), 0)        AS keystrokes,
            COALESCE(SUM(words), 0)             AS words,
            COALESCE(SUM(predictions), 0)       AS predictions,
            COALESCE(SUM(keystrokes_saved), 0)  AS keystrokes_saved,
            COALESCE(SUM(minutes), 0.0)         AS minutes,
            COALESCE(SUM(sessions), 0)          AS sessions,
            COALESCE(SUM(prediction_offers), 0) AS prediction_offers
         FROM submissions_latest`
    ).first<Record<string, number>>();

    return new Response(JSON.stringify(row ?? {}), {
        headers: {
            "Content-Type": "application/json",
            "Cache-Control": `public, max-age=${AGGREGATE_CACHE_SECONDS}`,
            // Permissive CORS so a static stats page can fetch from
            // anywhere. The endpoint returns no PII.
            "Access-Control-Allow-Origin": "*",
        },
    });
}

async function handleForget(req: Request, env: Env): Promise<Response> {
    let body: unknown;
    try {
        body = await req.json();
    } catch {
        return badRequest("body must be valid JSON");
    }
    const id = (body as { anon_id?: unknown }).anon_id;
    if (!isValidAnonId(id)) return badRequest("anon_id invalid");

    if (!(await withinRateLimit(env, `forget:${id}`))) {
        // Same 204 as a normal forget, for the same anti-oracle reason noted
        // on the "Always 204" line below.
        return new Response(null, { status: 204 });
    }

    // ON DELETE CASCADE on submissions_latest takes care of the child row.
    await env.DB.prepare(`DELETE FROM users WHERE anon_id = ?`).bind(id).run();

    // Always 204 -- don't leak whether the id existed.
    return new Response(null, { status: 204 });
}

export default {
    async fetch(req: Request, env: Env): Promise<Response> {
        const url = new URL(req.url);

        if (req.method === "POST" && url.pathname === "/v1/submit") {
            return handleSubmit(req, env);
        }
        if (req.method === "GET" && url.pathname === "/v1/aggregate") {
            return handleAggregate(env);
        }
        if (req.method === "POST" && url.pathname === "/v1/forget") {
            return handleForget(req, env);
        }
        if (req.method === "OPTIONS") {
            // Minimal CORS preflight for the aggregate endpoint.
            return new Response(null, {
                status: 204,
                headers: {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "86400",
                },
            });
        }

        return new Response("not found\n", { status: 404 });
    },

    // Daily cron prunes inactive users. Cron schedule is configured
    // in wrangler.toml; the worker only sees the trigger event.
    async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
        const cutoff = Math.floor(Date.now() / 1000) - 365 * 24 * 60 * 60;
        await env.DB.prepare(
            `DELETE FROM users WHERE last_seen < ?`
        ).bind(cutoff).run();
    },
};
