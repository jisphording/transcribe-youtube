import { Plugin, TFile, normalizePath } from "obsidian";
import type { SSEResource, SSESource } from "./sse-handler";
import { YTObsidianSettings, DEFAULT_SETTINGS, YTObsidianSettingTab } from "./settings";
import { YouTubeImportModal } from "./import-modal";

export default class YTObsidianPlugin extends Plugin {
    settings: YTObsidianSettings;

    async onload() {
        await this.loadSettings();

        this.addCommand({
            id: "import-media",
            name: "Import Media (YouTube or Podcast) as Note",
            callback: () => new YouTubeImportModal(this.app, this).open(),
        });

        this.addRibbonIcon("file-audio", "Import Media", () => {
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
        this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    folderForSource(source: SSESource): string {
        return source === "podcast"
            ? this.settings.podcastOutputFolder.trim()
            : this.settings.outputFolder.trim();
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
        const folder = this.folderForSource(source);
        const allFiles = this.app.vault.getFiles();
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
