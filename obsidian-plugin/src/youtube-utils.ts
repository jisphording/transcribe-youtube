import { App, TFile } from "obsidian";

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

export async function findExistingNote(app: App, folder: string, videoId: string): Promise<TFile | null> {
    const files = app.vault.getMarkdownFiles().filter((f) =>
        !folder || f.path.startsWith(folder + "/")
    );
    for (const file of files) {
        const content = await app.vault.read(file);
        if (content.includes(videoId)) return file;
    }
    return null;
}
