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

export interface Env {
    DB: D1Database;
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

function isShortString(s: unknown, max: number): s is string {
    return typeof s === "string" && s.length > 0 && s.length <= max;
}

function validatePayload(body: unknown): SubmitPayload | string {
    if (!body || typeof body !== "object") return "body must be a JSON object";
    const b = body as Record<string, unknown>;

    if (!isValidAnonId(b.anon_id)) return "anon_id invalid";
    if (!isShortString(b.app_version, 32)) return "app_version invalid";
    if (!isShortString(b.os, 16)) return "os invalid";
    if (!isPositiveInt(b.keystrokes, MAX_KEYSTROKES)) return "keystrokes invalid";
    if (!isPositiveInt(b.words, MAX_WORDS)) return "words invalid";
    if (!isPositiveInt(b.predictions, MAX_WORDS)) return "predictions invalid";
    if (!isPositiveInt(b.keystrokes_saved, MAX_KEYSTROKES)) return "keystrokes_saved invalid";
    if (!isPositiveNum(b.minutes, MAX_MINUTES)) return "minutes invalid";
    if (!isPositiveInt(b.sessions, MAX_SESSIONS)) return "sessions invalid";
    if (!isPositiveInt(b.prediction_offers, MAX_WORDS)) return "prediction_offers invalid";

    return b as unknown as SubmitPayload;
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

    const now = Math.floor(Date.now() / 1000);

    // Upsert user. first_seen stays the value already on disk; only
    // last_seen / app_version / os update. The "excluded.first_seen"
    // expression in SQLite ON CONFLICT refers to the would-be-inserted
    // row, which is what we want for first_seen on the insert path.
    await env.DB.prepare(
        `INSERT INTO users (anon_id, first_seen, last_seen, app_version, os)
         VALUES (?1, ?2, ?2, ?3, ?4)
         ON CONFLICT(anon_id) DO UPDATE SET
            last_seen   = excluded.last_seen,
            app_version = excluded.app_version,
            os          = excluded.os`
    ).bind(validated.anon_id, now, validated.app_version, validated.os).run();

    // Replace the latest submission row.
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
            prediction_offers = excluded.prediction_offers`
    ).bind(
        validated.anon_id, now,
        validated.keystrokes, validated.words, validated.predictions,
        validated.keystrokes_saved, validated.minutes,
        validated.sessions, validated.prediction_offers,
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
