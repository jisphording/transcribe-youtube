import { Plugin, TFile, normalizePath } from "obsidian";
import type { SSEResource } from "./sse-handler";
import { YTObsidianSettings, DEFAULT_SETTINGS, YTObsidianSettingTab } from "./settings";
import { YouTubeImportModal } from "./import-modal";

export default class YTObsidianPlugin extends Plugin {
    settings: YTObsidianSettings;

    async onload() {
        await this.loadSettings();

        this.addCommand({
            id: "import-youtube-video",
            name: "Import YouTube Video as Note",
            callback: () => new YouTubeImportModal(this.app, this).open(),
        });

        this.addRibbonIcon("youtube", "Import YouTube Video", () => {
            new YouTubeImportModal(this.app, this).open();
        });

        this.addSettingTab(new YTObsidianSettingTab(this.app, this));
    }

    async loadSettings() {
        this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    async createNote(filename: string, content: string): Promise<TFile> {
        const folder = this.settings.outputFolder.trim();

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

    async createResourceStubs(resources: SSEResource[]): Promise<void> {
        const folder = this.settings.outputFolder.trim();
        const allFiles = this.app.vault.getFiles();
        const folderPrefix = folder ? folder + "/" : "";

        for (const resource of resources) {
            const name = resource.name.trim();
            if (!name) continue;

            // Skip if a file with this name already exists in the transcript folder or subfolders
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
