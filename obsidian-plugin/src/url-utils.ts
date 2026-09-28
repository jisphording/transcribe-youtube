import { App, TFile } from "obsidian";

export type MediaSource = "youtube" | "podcast" | "web" | null;
export type CollectionSource = "youtube" | "podcast" | null;

const CHANNEL_RE = /youtube\.com\/(@[^/?#]+|channel\/[^/?#]+|c\/[^/?#]+|user\/[^/?#]+)/i;

/** Mirror of backend youtube.is_youtube_collection_url() / podcast.is_apple_show_url(). */
export function detectCollection(url: string): CollectionSource {
    const u = url.trim();
    if (/podcasts\.apple\.com\//i.test(u)) {
        return /\/id\d+/.test(u) && !/[?&]i=\d+/.test(u) ? "podcast" : null;
    }
    if (/[?&]v=|youtu\.be\/|\/shorts\/|\/live\/|\/embed\//i.test(u)) return null;
    if (CHANNEL_RE.test(u) || /youtube\.com\/playlist\?(.*&)?list=/i.test(u)) return "youtube";
    return null;
}

export function detectSource(url: string): MediaSource {
    const u = url.trim();
    if (!u) return null;
    if (/podcasts\.apple\.com\//i.test(u)) return "podcast";
    if (/youtube\.com\/|youtu\.be\//i.test(u)) return "youtube";
    if (/^https?:\/\/[^\s/]+\.[^\s/]+/i.test(u)) return "web";
    return null;
}

const TRACKING_PARAM = /^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$/i;

/** Mirror of backend web.clean_url(): drop fragment + tracking params. */
export function cleanWebUrl(url: string): string {
    try {
        const u = new URL(url.trim());
        u.hash = "";
        const params = new URLSearchParams();
        u.searchParams.forEach((value, key) => {
            if (!TRACKING_PARAM.test(key)) params.append(key, value);
        });
        u.search = params.toString();
        return u.toString();
    } catch {
        return url.trim();
    }
}

export function extractVideoId(url: string): string | null {
    const patterns = [
        /[?&]v=([a-zA-Z0-9_-]{11})/,
        /youtu\.be\/([a-zA-Z0-9_-]{11})/,
        /embed\/([a-zA-Z0-9_-]{11})/,
    ];
    for (const pattern of patterns) {
        const match = url.match(pattern);
        if (match) return match[1];
    }
    return null;
}

export function extractAppleEpisodeId(url: string): string | null {
    const match = url.match(/[?&]i=(\d+)/);
    return match ? match[1] : null;
}

export function extractAppleShowId(url: string): string | null {
    const match = url.match(/\/id(\d+)/);
    return match ? match[1] : null;
}

const ID_KEYS = ["youtube_id", "apple_episode_id", "episode_guid"];

/** Video / episode ids of a note, from the frontmatter id keys or (legacy notes) parsed from `url:`. */
function frontmatterIds(app: App, file: TFile): string[] {
    const fm = app.metadataCache.getFileCache(file)?.frontmatter;
    if (!fm) return [];
    const ids = ID_KEYS.map((k) => fm[k]).filter((v) => v != null && v !== "").map(String);
    if (typeof fm.url === "string") {
        const yt = extractVideoId(fm.url);
        if (yt) ids.push(yt);
        const ap = extractAppleEpisodeId(fm.url);
        if (ap) ids.push(ap);
    }
    return ids;
}

function filesBelow(app: App, folders: string[]): TFile[] {
    return app.vault.getMarkdownFiles().filter((f) =>
        folders.some((folder) => !folder || f.path.startsWith(folder + "/"))
    );
}

/** All known ids below the given folders — sent with a batch so the backend skips them. */
export function collectKnownIds(app: App, folders: string[]): Set<string> {
    const ids = new Set<string>();
    for (const file of filesBelow(app, folders)) {
        for (const id of frontmatterIds(app, file)) ids.add(id);
    }
    return ids;
}

/**
 * Find an existing note for a video id, Apple episode id or cleaned article URL marker.
 * Checks the frontmatter ids first (in-memory), then falls back to a content scan.
 */
export async function findExistingNote(app: App, folder: string, marker: string): Promise<TFile | null> {
    if (!marker) return null;
    const files = filesBelow(app, [folder]);
    const byId = files.find((f) => frontmatterIds(app, f).includes(marker));
    if (byId) return byId;
    for (const file of files) {
        const content = await app.vault.cachedRead(file);
        if (content.includes(marker)) return file;
    }
    return null;
}
