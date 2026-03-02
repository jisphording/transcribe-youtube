import {
  App,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  normalizePath,
} from "obsidian";

// ─── Settings ────────────────────────────────────────────────────────────────

interface YTObsidianSettings {
  apiUrl: string;
  outputFolder: string;
}

const DEFAULT_SETTINGS: YTObsidianSettings = {
  apiUrl: "http://localhost:8000",
  outputFolder: "YouTube",
};

// ─── Plugin ──────────────────────────────────────────────────────────────────

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

    // Ensure folder exists
    if (folder && !this.app.vault.getAbstractFileByPath(folder)) {
      await this.app.vault.createFolder(folder);
    }

    const fullPath = normalizePath(folder ? `${folder}/${filename}` : filename);

    // If file already exists, add a suffix
    let finalPath = fullPath;
    if (this.app.vault.getAbstractFileByPath(finalPath)) {
      const base = fullPath.replace(/\.md$/, "");
      finalPath = `${base}-${Date.now()}.md`;
    }

    return await this.app.vault.create(finalPath, content);
  }
}

// ─── Import Modal ─────────────────────────────────────────────────────────────

class YouTubeImportModal extends Modal {
  plugin: YTObsidianPlugin;
  urlInput: HTMLInputElement;
  statusEl: HTMLElement;
  importBtn: HTMLButtonElement;

  constructor(app: App, plugin: YTObsidianPlugin) {
    super(app);
    this.plugin = plugin;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.addClass("yt-obsidian-modal");

    contentEl.createEl("h2", { text: "Import YouTube Video" });

    contentEl.createEl("p", {
      text: "Paste a YouTube URL to fetch the transcript, summarize it with Claude, and create a new note.",
      cls: "yt-obsidian-description",
    });

    // URL Input
    const inputWrapper = contentEl.createDiv({ cls: "yt-obsidian-input-wrapper" });
    inputWrapper.createEl("label", { text: "YouTube URL" });
    this.urlInput = inputWrapper.createEl("input", {
      type: "text",
      placeholder: "https://www.youtube.com/watch?v=...",
      cls: "yt-obsidian-url-input",
    });
    this.urlInput.style.width = "100%";
    this.urlInput.style.marginTop = "4px";

    // Allow hitting Enter to trigger import
    this.urlInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this.startImport();
    });

    // Status message
    this.statusEl = contentEl.createDiv({ cls: "yt-obsidian-status" });
    this.statusEl.style.minHeight = "24px";
    this.statusEl.style.marginTop = "8px";
    this.statusEl.style.fontSize = "13px";

    // Buttons
    const btnRow = contentEl.createDiv({ cls: "yt-obsidian-btn-row" });
    btnRow.style.display = "flex";
    btnRow.style.justifyContent = "flex-end";
    btnRow.style.gap = "8px";
    btnRow.style.marginTop = "16px";

    const cancelBtn = btnRow.createEl("button", { text: "Cancel" });
    cancelBtn.addEventListener("click", () => this.close());

    this.importBtn = btnRow.createEl("button", {
      text: "Import",
      cls: "mod-cta",
    });
    this.importBtn.addEventListener("click", () => this.startImport());

    // Focus the input
    setTimeout(() => this.urlInput.focus(), 50);
  }

  async startImport() {
    const url = this.urlInput.value.trim();
    if (!url) {
      this.setStatus("⚠️ Please enter a YouTube URL.", "warning");
      return;
    }

    if (!url.includes("youtube.com") && !url.includes("youtu.be")) {
      this.setStatus("⚠️ That doesn't look like a YouTube URL.", "warning");
      return;
    }

    this.importBtn.disabled = true;
    this.urlInput.disabled = true;
    this.setStatus("⏳ Fetching transcript and metadata…", "info");

    try {
      const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
      const response = await fetch(`${apiUrl}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || `Server error ${response.status}`);
      }

      this.setStatus("🤖 Claude is processing the transcript…", "info");

      const data = await response.json();
      const { filename, content } = data;

      this.setStatus("💾 Creating note in vault…", "info");

      const file = await this.plugin.createNote(filename, content);

      new Notice(`✅ Created: ${file.path}`);
      this.setStatus(`✅ Done! Note saved as "${file.path}"`, "success");

      // Open the note
      await this.app.workspace.openLinkText(file.path, "", false);

      setTimeout(() => this.close(), 1500);
    } catch (error) {
      console.error("[YT Obsidian]", error);
      this.setStatus(`❌ Error: ${error.message}`, "error");
      this.importBtn.disabled = false;
      this.urlInput.disabled = false;
    }
  }

  setStatus(msg: string, type: "info" | "success" | "warning" | "error") {
    this.statusEl.setText(msg);
    this.statusEl.className = `yt-obsidian-status yt-obsidian-status--${type}`;
  }

  onClose() {
    this.contentEl.empty();
  }
}

// ─── Settings Tab ─────────────────────────────────────────────────────────────

class YTObsidianSettingTab extends PluginSettingTab {
  plugin: YTObsidianPlugin;

  constructor(app: App, plugin: YTObsidianPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "YouTube to Obsidian Settings" });

    new Setting(containerEl)
      .setName("Backend API URL")
      .setDesc("URL of your local Python backend (default: http://localhost:8000)")
      .addText((text) =>
        text
          .setPlaceholder("http://localhost:8000")
          .setValue(this.plugin.settings.apiUrl)
          .onChange(async (value) => {
            this.plugin.settings.apiUrl = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Output Folder")
      .setDesc("Vault folder where notes will be saved (created if it doesn't exist). Leave empty for vault root.")
      .addText((text) =>
        text
          .setPlaceholder("YouTube")
          .setValue(this.plugin.settings.outputFolder)
          .onChange(async (value) => {
            this.plugin.settings.outputFolder = value.trim();
            await this.plugin.saveSettings();
          })
      );
  }
}
