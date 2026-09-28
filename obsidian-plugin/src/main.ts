import { FileSystemAdapter, Plugin, TFile, addIcon, normalizePath } from "obsidian";
import type { SSEResource, SSESource } from "./sse-handler";
import { YTObsidianSettings, DEFAULT_SETTINGS, YTObsidianSettingTab, FolderKey } from "./settings";
import { YouTubeImportModal } from "./import-modal";
import { QueueView, QUEUE_VIEW_TYPE } from "./queue-view";

// Where the backend's batch queue writes notes (absolute paths) and which folders
// count for duplicate detection (vault-relative for metadataCache, absolute for the backend).
export interface BatchTargets {
    vaultRoot: string;
    folders: { youtube: string; podcast: string; resources: string };
    scanFolders: string[];
    scanRoots: string[];
}

// Bundled as SVG so the ribbon icon never depends on Obsidian's Lucide version
// (a renamed Lucide id renders as an invisible-but-clickable ribbon button).
const RIBBON_ICON_ID = "media-to-obsidian-import";
const RIBBON_ICON_SVG =
    '<g fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M58 10H26a8 8 0 0 0-8 8v64a8 8 0 0 0 8 8h48a8 8 0 0 0 8-8V34z"/>' +
    '<path d="M58 10v24h24"/><path d="M50 44v30"/><path d="M37 61l13 13 13-13"/></g>';

export default class YTObsidianPlugin extends Plugin {
    settings: YTObsidianSettings;

    async onload() {
        await this.loadSettings();

        this.addCommand({
            id: "import-media",
            name: "Import Media (YouTube, Podcast or Web Article) as Note",
            callback: () => new YouTubeImportModal(this.app, this).open(),
        });

        addIcon(RIBBON_ICON_ID, RIBBON_ICON_SVG);
        this.addRibbonIcon(RIBBON_ICON_ID, "Import Media", () => {
            new YouTubeImportModal(this.app, this).open();
        });

        this.registerView(QUEUE_VIEW_TYPE, (leaf) => new QueueView(leaf, this));
        this.addCommand({
            id: "show-import-queue",
            name: "Show import queue",
            callback: () => this.activateQueueView(),
        });

        this.addSettingTab(new YTObsidianSettingTab(this.app, this));

        if (this.settings.keepWhisperWarm) {
            this.fireWhisperLifecycle("start");
        }
    }

    async onunload() {
        this.fireWhisperLifecycle("stop");
    }

    private fireWhisperLifecycle(action: "start" | "stop") {
        const apiUrl = this.settings.apiUrl.replace(/\/$/, "");
        // Best-effort, fire-and-forget — don't block Obsidian's lifecycle on a network call.
        fetch(`${apiUrl}/whisper/${action}`, { method: "POST" }).catch(() => {});
    }

    async activateQueueView() {
        const existing = this.app.workspace.getLeavesOfType(QUEUE_VIEW_TYPE)[0];
        const leaf = existing ?? this.app.workspace.getRightLeaf(false);
        if (!leaf) return;
        if (!existing) await leaf.setViewState({ type: QUEUE_VIEW_TYPE, active: true });
        this.app.workspace.revealLeaf(leaf);
    }

    /** Null on mobile: the backend can only write into a vault on this machine's disk. */
    batchTargets(): BatchTargets | null {
        const adapter = this.app.vault.adapter;
        if (!(adapter instanceof FileSystemAdapter)) return null;
        const base = adapter.getBasePath();
        const abs = (p: string) => `${base}/${p}`;
        const youtube = this.folderForSource("youtube");
        const podcast = this.folderForSource("podcast");
        const scanFolders = this.settings.useParentFolder
            ? [normalizePath(this.settings.mediaTranscriptsFolder.trim() || DEFAULT_SETTINGS.mediaTranscriptsFolder)]
            : [youtube, podcast];
        return {
            vaultRoot: base,
            folders: { youtube: abs(youtube), podcast: abs(podcast), resources: abs(this.resourceFolder()) },
            scanFolders,
            scanRoots: scanFolders.map(abs),
        };
    }

    async loadSettings() {
        const saved = (await this.loadData()) ?? {};
        const legacyKeys = ["outputFolder", "podcastOutputFolder", "webOutputFolder"];
        const hasLegacy = legacyKeys.some((k) => k in saved);
        const isOldDefault = saved.mediaTranscriptsFolder === "Media Transcripts";
        for (const k of legacyKeys) delete saved[k];
        if (hasLegacy || isOldDefault) delete saved.mediaTranscriptsFolder;

        this.settings = Object.assign({}, DEFAULT_SETTINGS, saved);
        if (hasLegacy || isOldDefault) await this.saveSettings();
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    // With useParentFolder (default) every folder lives below one root folder;
    // otherwise the folders are siblings in the vault root.
    private resolveFolder(key: FolderKey): string {
        const name = this.settings[key].trim() || DEFAULT_SETTINGS[key];
        if (!this.settings.useParentFolder) return normalizePath(name);
        const root = this.settings.mediaTranscriptsFolder.trim() || DEFAULT_SETTINGS.mediaTranscriptsFolder;
        return normalizePath(`${root}/${name}`);
    }

    folderForSource(source: SSESource): string {
        return this.resolveFolder(source === "podcast" ? "podcastFolder" : source === "web" ? "articleFolder" : "youtubeFolder");
    }

    // Shared by all sources so the same tool/product gets a single stub.
    resourceFolder(): string {
        return this.resolveFolder("resourcesFolder");
    }

    private async ensureFolder(folder: string): Promise<void> {
        if (!this.app.vault.getAbstractFileByPath(folder)) {
            await this.app.vault.createFolder(folder);
        }
    }

    async createNote(filename: string, content: string, source: SSESource): Promise<TFile> {
        const folder = this.folderForSource(source);
        await this.ensureFolder(folder);

        const fullPath = normalizePath(`${folder}/${filename}`);

        let finalPath = fullPath;
        if (this.app.vault.getAbstractFileByPath(finalPath)) {
            const base = fullPath.replace(/\.md$/, "");
            finalPath = `${base}-${Date.now()}.md`;
        }

        return await this.app.vault.create(finalPath, content);
    }

    async createResourceStubs(resources: SSEResource[]): Promise<void> {
        const folder = this.resourceFolder();
        await this.ensureFolder(folder);

        const folderPrefix = folder + "/";
        const allFiles = this.app.vault.getFiles();

        for (const resource of resources) {
            const name = resource.name.trim();
            if (!name) continue;

            const alreadyExists = allFiles.some(
                (f) => f.basename.toLowerCase() === name.toLowerCase() && f.path.startsWith(folderPrefix)
            );
            if (alreadyExists) continue;

            const stubPath = normalizePath(`${folder}/${name}.md`);
            if (!this.app.vault.getAbstractFileByPath(stubPath)) {
                await this.app.vault.create(stubPath, "");
            }
        }
    }
}
