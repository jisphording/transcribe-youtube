import { Plugin, TFile, addIcon, normalizePath } from "obsidian";
import type { SSEResource, SSESource } from "./sse-handler";
import { YTObsidianSettings, DEFAULT_SETTINGS, YTObsidianSettingTab } from "./settings";
import { YouTubeImportModal } from "./import-modal";

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

    async loadSettings() {
        const saved = await this.loadData();
        this.settings = Object.assign({}, DEFAULT_SETTINGS, saved);

        // Migrate old settings (outputFolder, podcastOutputFolder, webOutputFolder) to new structure
        if (saved && (saved.outputFolder || saved.podcastOutputFolder || saved.webOutputFolder)) {
            const oldBase = saved.outputFolder || "YouTube";
            this.settings.mediaTranscriptsFolder = oldBase.replace(/\/YouTube$/, "") || DEFAULT_SETTINGS.mediaTranscriptsFolder;
            await this.saveSettings();
        }
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    folderForSource(source: SSESource): string {
        const base = this.settings.mediaTranscriptsFolder.trim();
        if (source === "podcast") {
            return base ? `${base}/Podcasts` : "Podcasts";
        }
        if (source === "web") {
            return base ? `${base}/Articles` : "Articles";
        }
        return base ? `${base}/YouTube` : "YouTube";
    }

    resourceFolder(): string {
        const base = this.settings.mediaTranscriptsFolder.trim();
        return base ? `${base}/Mentioned_Resources` : "Mentioned_Resources";
    }

    async createNote(filename: string, content: string, source: SSESource): Promise<TFile> {
        const folder = this.folderForSource(source);

        if (folder && !this.app.vault.getAbstractFileByPath(folder)) {
            await this.app.vault.createFolder(folder);
        }

        const fullPath = normalizePath(folder ? `${folder}/${filename}` : filename);

        let finalPath = fullPath;
        if (this.app.vault.getAbstractFileByPath(finalPath)) {
            const base = fullPath.replace(/\.md$/, "");
            finalPath = `${base}-${Date.now()}.md`;
        }

        return await this.app.vault.create(finalPath, content);
    }

    async createResourceStubs(resources: SSEResource[], source: SSESource): Promise<void> {
        const folder = this.resourceFolder();
        const allFiles = this.app.vault.getFiles();

        if (folder && !this.app.vault.getAbstractFileByPath(folder)) {
            await this.app.vault.createFolder(folder);
        }

        const folderPrefix = folder ? folder + "/" : "";

        for (const resource of resources) {
            const name = resource.name.trim();
            if (!name) continue;

            const alreadyExists = allFiles.some(
                (f) =>
                    f.basename.toLowerCase() === name.toLowerCase() &&
                    (folderPrefix === "" || f.path.startsWith(folderPrefix))
            );
            if (alreadyExists) continue;

            const stubPath = normalizePath(folderPrefix ? `${folder}/${name}.md` : `${name}.md`);
            if (!this.app.vault.getAbstractFileByPath(stubPath)) {
                await this.app.vault.create(stubPath, "");
            }
        }
    }
}
