import { App, TFile } from "obsidian";

export type MediaSource = "youtube" | "podcast" | null;

export function detectSource(url: string): MediaSource {
    const u = url.trim();
    if (!u) return null;
    if (/podcasts\.apple\.com\//i.test(u)) return "podcast";
    if (/youtube\.com\/|youtu\.be\//i.test(u)) return "youtube";
    return null;
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

/**
 * Find an existing note by searching for a marker in the file content.
 * The marker is the YouTube video ID or the Apple episode/show id pair.
 */
export async function findExistingNote(app: App, folder: string, marker: string): Promise<TFile | null> {
    if (!marker) return null;
    const files = app.vault.getMarkdownFiles().filter((f) =>
        !folder || f.path.startsWith(folder + "/")
    );
    for (const file of files) {
        const content = await app.vault.read(file);
        if (content.includes(marker)) return file;
    }
    return null;
}
