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
  cookieBrowser: string;
}

const DEFAULT_SETTINGS: YTObsidianSettings = {
  apiUrl: "http://localhost:8000",
  outputFolder: "YouTube",
  cookieBrowser: "",
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
  extendedSummaryCheckbox: HTMLInputElement;
  modelSelect: HTMLSelectElement;
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

    // Extended Summary checkbox
    const checkboxWrapper = contentEl.createDiv({ cls: "yt-obsidian-checkbox-wrapper" });
    checkboxWrapper.style.marginTop = "12px";
    checkboxWrapper.style.display = "flex";
    checkboxWrapper.style.alignItems = "center";
    checkboxWrapper.style.gap = "8px";

    this.extendedSummaryCheckbox = checkboxWrapper.createEl("input", {
      type: "checkbox",
    });
    this.extendedSummaryCheckbox.id = "yt-extended-summary";

    const checkboxLabel = checkboxWrapper.createEl("label", {
      text: "Create extended summary",
    });
    checkboxLabel.htmlFor = "yt-extended-summary";
    checkboxLabel.style.cursor = "pointer";
    checkboxLabel.style.fontSize = "14px";

    const checkboxDesc = contentEl.createDiv({ cls: "yt-obsidian-checkbox-desc" });
    checkboxDesc.setText(
      "Adds a detailed topic-by-topic editorial summary between the summary and transcript. Increases processing time."
    );
    checkboxDesc.style.fontSize = "12px";
    checkboxDesc.style.color = "var(--text-muted)";
    checkboxDesc.style.marginTop = "2px";
    checkboxDesc.style.marginBottom = "4px";

    // Model selection
    const modelWrapper = contentEl.createDiv({ cls: "yt-obsidian-model-wrapper" });
    modelWrapper.style.marginTop = "12px";
    modelWrapper.style.display = "flex";
    modelWrapper.style.alignItems = "center";
    modelWrapper.style.gap = "8px";

    modelWrapper.createEl("label", { text: "Model:" }).style.fontSize = "14px";

    this.modelSelect = modelWrapper.createEl("select", {
      cls: "dropdown",
    });
    const sonnetOpt = this.modelSelect.createEl("option", { text: "Sonnet (faster)", value: "claude-sonnet-4-6" });
    sonnetOpt.value = "claude-sonnet-4-6";
    const opusOpt = this.modelSelect.createEl("option", { text: "Opus (higher quality)", value: "claude-opus-4-6" });
    opusOpt.value = "claude-opus-4-6";

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
    this.extendedSummaryCheckbox.disabled = true;
    this.modelSelect.disabled = true;
    this.setStatus("⏳ Connecting to backend…", "info");

    try {
      const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
      const body: Record<string, string | boolean> = { url };
      if (this.plugin.settings.cookieBrowser) {
        body.cookie_browser = this.plugin.settings.cookieBrowser;
      }
      if (this.extendedSummaryCheckbox.checked) {
        body.extended_summary = true;
      }
      body.model = this.modelSelect.value;

      const response = await fetch(`${apiUrl}/process`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const err = await response
          .json()
          .catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || `Server error ${response.status}`);
      }

      // Parse SSE stream
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let doneData: { filename: string; content: string } | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          let event: Record<string, unknown>;
          try {
            event = JSON.parse(jsonStr);
          } catch {
            continue;
          }

          const stage = event.stage as string;
          const message = event.message as string;

          switch (stage) {
            case "metadata":
              this.setStatus(`⏳ ${message}`, "info");
              break;
            case "metadata_done":
              this.setStatus(`✓ ${message}`, "info");
              break;
            case "transcript":
              this.setStatus(`⏳ ${message}`, "info");
              break;
            case "transcript_done":
              this.setStatus(`✓ ${message}`, "info");
              break;
            case "claude":
              this.setStatus(`🤖 ${message}`, "info");
              break;
            case "claude_extended":
              this.setStatus(`🤖 ${message}`, "info");
              break;
            case "building":
              this.setStatus(`💾 ${message}`, "info");
              break;
            case "done":
              doneData = {
                filename: event.filename as string,
                content: event.content as string,
              };
              break;
            case "error":
              throw new Error(message);
          }
        }
      }

      if (!doneData) {
        throw new Error("Stream ended without result.");
      }

      const file = await this.plugin.createNote(
        doneData.filename,
        doneData.content
      );

      new Notice(`Created: ${file.path}`);
      this.setStatus(`Done! Note saved as "${file.path}"`, "success");

      await this.app.workspace.openLinkText(file.path, "", false);
      setTimeout(() => this.close(), 1500);
    } catch (error) {
      console.error("[YT Obsidian]", error);
      this.setStatus(`❌ ${error.message}`, "error");
      this.importBtn.disabled = false;
      this.urlInput.disabled = false;
      this.extendedSummaryCheckbox.disabled = false;
      this.modelSelect.disabled = false;
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
      .setName("Browser for Cookies")
      .setDesc(
        "Extract cookies directly from a browser where you're logged into YouTube. Fixes 'Sign in to confirm you're not a bot' errors."
      )
      .addDropdown((dropdown) =>
        dropdown
          .addOptions({
            "": "None (disabled)",
            chrome: "Chrome",
            firefox: "Firefox",
            safari: "Safari",
            edge: "Edge",
            brave: "Brave",
          })
          .setValue(this.plugin.settings.cookieBrowser)
          .onChange(async (value) => {
            this.plugin.settings.cookieBrowser = value;
            await this.plugin.saveSettings();
          })
      );

    const cookieSetting = new Setting(containerEl)
      .setName("YouTube Cookie File (fallback)")
      .setDesc(
        "Upload a Netscape cookies.txt file to the backend. Use this if the browser option above doesn't work. Export with a browser extension like 'Get cookies.txt LOCALLY'."
      );

    const cookieStatusEl = containerEl.createDiv({ cls: "yt-obsidian-cookie-status" });
    cookieStatusEl.style.fontSize = "12px";
    cookieStatusEl.style.marginTop = "-8px";
    cookieStatusEl.style.marginBottom = "12px";
    cookieStatusEl.style.paddingLeft = "18px";

    const refreshCookieStatus = async () => {
      try {
        const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
        const resp = await fetch(`${apiUrl}/cookies`);
        const data = await resp.json();
        if (data.has_cookies) {
          cookieStatusEl.setText("Cookie file is uploaded on the backend.");
          cookieStatusEl.style.color = "var(--text-success)";
        } else {
          cookieStatusEl.setText("No cookie file on the backend.");
          cookieStatusEl.style.color = "var(--text-muted)";
        }
      } catch {
        cookieStatusEl.setText("Could not reach backend.");
        cookieStatusEl.style.color = "var(--text-error)";
      }
    };
    refreshCookieStatus();

    // Hidden file input for native file picker
    const fileInput = containerEl.createEl("input", { type: "file" });
    fileInput.accept = ".txt";
    fileInput.style.display = "none";
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      const content = await file.text();
      try {
        const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
        const resp = await fetch(`${apiUrl}/cookies`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        if (!resp.ok) throw new Error("Upload failed");
        new Notice("Cookie file uploaded to backend.");
      } catch {
        new Notice("Failed to upload cookie file. Is the backend running?");
      }
      fileInput.value = "";
      refreshCookieStatus();
    });

    cookieSetting.addButton((btn) =>
      btn.setButtonText("Browse...").onClick(() => fileInput.click())
    );

    cookieSetting.addButton((btn) =>
      btn
        .setButtonText("Remove")
        .setWarning()
        .onClick(async () => {
          try {
            const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
            await fetch(`${apiUrl}/cookies`, { method: "DELETE" });
            new Notice("Cookie file removed from backend.");
          } catch {
            new Notice("Failed to remove cookie file.");
          }
          refreshCookieStatus();
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
