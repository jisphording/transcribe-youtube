// Typed client for the backend's /batch endpoints. Pure module — no plugin imports.

export type BatchOptions = Record<string, string | boolean>;

export interface BatchEstimate {
    model: string;
    total: number;
    low: number;
    high: number;
}

export interface PreviewItem {
    url: string;
    external_id: string;
    title: string;
    duration_seconds: number;
    duration_estimated: boolean;
    duplicate: boolean;
    cost_estimate: number;
}

export interface BatchPreview {
    batch_id: string | null;
    source: "youtube" | "podcast";
    title: string;
    items: PreviewItem[];
    estimate: BatchEstimate;
    has_more: boolean;
    capped: boolean;
    count_requested: number | null;
    expires_in_seconds: number;
}

export type ItemState = "pending" | "running" | "done" | "skipped" | "failed" | "duplicate" | "cancelled";

export interface BatchItem {
    id: number;
    url: string;
    external_id: string;
    title: string;
    duration_s: number;
    cost_estimate: number;
    state: ItemState;
    error: string | null;
    progress: string | null;
    note_path: string | null;
    cost_usd: number | null;
}

export interface Batch {
    id: string;
    created_at: number;
    state: "pending_confirmation" | "queued" | "done" | "cancelled" | "expired";
    source: "youtube" | "podcast";
    source_url: string;
    title: string;
    estimate: BatchEstimate;
    counts: Partial<Record<ItemState, number>>;
    cost_usd: number;
    items: BatchItem[];
}

export interface Lane {
    next_allowed_at: number;
    blocked_until: number;
    blocked: boolean;
    block_reason: string | null;
    done_last_hour?: number;
    hourly_cap?: number;
}

export interface QueueStatus {
    batches: Batch[];
    lanes: Record<"youtube" | "podcast", Lane>;
    now: number;
}

export interface BatchCreateBody {
    url: string;
    count: number | null;
    min_minutes: number;
    options: BatchOptions;
    vault_root: string;
    folders: { youtube: string; podcast: string; resources: string };
    scan_roots: string[];
    known_ids: string[];
}

async function call<T>(apiUrl: string, path: string, method = "GET", body?: unknown): Promise<T> {
    const res = await fetch(`${apiUrl.replace(/\/$/, "")}${path}`, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof err.detail === "string" ? err.detail : `Server error ${res.status}`);
    }
    return res.json();
}

export const createBatch = (apiUrl: string, body: BatchCreateBody) => call<BatchPreview>(apiUrl, "/batch", "POST", body);
export const confirmBatch = (apiUrl: string, id: string) => call<Batch>(apiUrl, `/batch/${id}/confirm`, "POST");
export const cancelBatch = (apiUrl: string, id: string) => call<Batch>(apiUrl, `/batch/${id}/cancel`, "POST");
export const retryBatch = (apiUrl: string, id: string) => call<{ retried: number }>(apiUrl, `/batch/${id}/retry`, "POST");
export const resumeLane = (apiUrl: string, lane: string) => call<unknown>(apiUrl, `/batch/lanes/${lane}/resume`, "POST");
export const getQueue = (apiUrl: string) => call<QueueStatus>(apiUrl, "/batch?limit=10");

export function fmtDuration(seconds: number): string {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

export function fmtUsd(n: number): string {
    return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}
