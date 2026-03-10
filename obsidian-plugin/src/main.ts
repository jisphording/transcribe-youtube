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
  importMode: "transcript" | "extended_summary" = "transcript";
  modeToggleBtns: HTMLButtonElement[] = [];
  modelSelect: HTMLSelectElement;
  statusEl: HTMLElement;
  detailEl: HTMLElement;
  progressWrapper: HTMLElement;
  progressBarOuter: HTMLElement;
  progressBarInner: HTMLElement;
  importBtn: HTMLButtonElement;
  skipDuplicateCheck = false;

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
    this.urlInput.addEventListener("input", () => {
      this.skipDuplicateCheck = false;
    });

    // Mode toggle: Transcript / Extended Summary
    const toggleWrapper = contentEl.createDiv({ cls: "yt-obsidian-toggle-wrapper" });
    toggleWrapper.style.marginTop = "12px";

    const toggleLabel = toggleWrapper.createEl("div", { text: "Output mode" });
    toggleLabel.style.fontSize = "12px";
    toggleLabel.style.color = "var(--text-muted)";
    toggleLabel.style.marginBottom = "6px";

    const toggleRow = toggleWrapper.createDiv({ cls: "yt-obsidian-toggle-row" });
    toggleRow.style.display = "flex";
    toggleRow.style.gap = "0";
    toggleRow.style.borderRadius = "6px";
    toggleRow.style.overflow = "hidden";
    toggleRow.style.border = "1px solid var(--background-modifier-border)";
    toggleRow.style.width = "fit-content";

    const modes: Array<{ value: "transcript" | "extended_summary"; label: string }> = [
      { value: "transcript", label: "Transcript" },
      { value: "extended_summary", label: "Extended Summary" },
    ];

    this.modeToggleBtns = modes.map(({ value, label }) => {
      const btn = toggleRow.createEl("button", { text: label });
      btn.style.padding = "5px 14px";
      btn.style.fontSize = "13px";
      btn.style.border = "none";
      btn.style.cursor = "pointer";
      btn.style.borderRadius = "0";
      btn.style.transition = "background 0.15s, color 0.15s";
      const setActive = (active: boolean) => {
        btn.style.backgroundColor = active ? "var(--interactive-accent)" : "var(--background-primary)";
        btn.style.color = active ? "var(--text-on-accent)" : "var(--text-normal)";
      };
      setActive(value === this.importMode);
      btn.addEventListener("click", () => {
        this.importMode = value;
        this.modeToggleBtns.forEach((b, i) => {
          const isActive = modes[i].value === value;
          b.style.backgroundColor = isActive ? "var(--interactive-accent)" : "var(--background-primary)";
          b.style.color = isActive ? "var(--text-on-accent)" : "var(--text-normal)";
        });
        this.modelSelect.value = value === "extended_summary" ? "claude-sonnet-4-6" : "claude-haiku-4-5-20251001";
      });
      return btn;
    });

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
    this.modelSelect.createEl("option", { text: "Haiku (fastest, cheapest)", value: "claude-haiku-4-5-20251001" });
    this.modelSelect.createEl("option", { text: "Sonnet (balanced)", value: "claude-sonnet-4-6" });
    this.modelSelect.createEl("option", { text: "Opus (highest quality)", value: "claude-opus-4-6" });
    this.modelSelect.value = "claude-haiku-4-5-20251001";

    // Progress bar
    const progressWrapper = contentEl.createDiv({ cls: "yt-obsidian-progress-wrapper" });
    progressWrapper.style.marginTop = "12px";
    progressWrapper.style.display = "none";

    this.progressBarOuter = progressWrapper.createDiv({ cls: "yt-obsidian-progress-bar-outer" });
    this.progressBarOuter.style.height = "4px";
    this.progressBarOuter.style.borderRadius = "2px";
    this.progressBarOuter.style.backgroundColor = "var(--background-modifier-border)";
    this.progressBarOuter.style.overflow = "hidden";

    this.progressBarInner = this.progressBarOuter.createDiv({ cls: "yt-obsidian-progress-bar-inner" });
    this.progressBarInner.style.height = "100%";
    this.progressBarInner.style.width = "0%";
    this.progressBarInner.style.backgroundColor = "var(--interactive-accent)";
    this.progressBarInner.style.transition = "width 0.3s ease";

    this.progressWrapper = progressWrapper;

    // Status message
    this.statusEl = contentEl.createDiv({ cls: "yt-obsidian-status" });
    this.statusEl.style.minHeight = "24px";
    this.statusEl.style.marginTop = "8px";
    this.statusEl.style.fontSize = "13px";

    // Detail line (token counts, elapsed time)
    this.detailEl = contentEl.createDiv({ cls: "yt-obsidian-detail" });
    this.detailEl.style.fontSize = "11px";
    this.detailEl.style.color = "var(--text-muted)";
    this.detailEl.style.minHeight = "16px";

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

  extractVideoId(url: string): string | null {
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

  async findExistingNote(videoId: string): Promise<TFile | null> {
    const folder = this.plugin.settings.outputFolder.trim();
    const files = this.app.vault.getMarkdownFiles().filter((f) =>
      !folder || f.path.startsWith(folder + "/")
    );
    for (const file of files) {
      const content = await this.app.vault.read(file);
      if (content.includes(videoId)) return file;
    }
    return null;
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

    if (!this.skipDuplicateCheck) {
      const videoId = this.extractVideoId(url);
      if (videoId) {
        const existing = await this.findExistingNote(videoId);
        if (existing) {
          this.setStatus(
            `⚠️ Already imported as "${existing.path}". Click Import again to overwrite.`,
            "warning"
          );
          this.skipDuplicateCheck = true;
          return;
        }
      }
    }
    this.skipDuplicateCheck = false;

    this.importBtn.disabled = true;
    this.urlInput.disabled = true;
    this.modeToggleBtns.forEach((b) => (b.disabled = true));
    this.modelSelect.disabled = true;
    this.setStatus("⏳ Connecting to backend…", "info");

    try {
      const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
      const body: Record<string, string | boolean> = { url };
      if (this.plugin.settings.cookieBrowser) {
        body.cookie_browser = this.plugin.settings.cookieBrowser;
      }
      if (this.importMode === "extended_summary") {
        body.extended_summary = true;
        body.include_transcript = false;
        body.extended_model = this.modelSelect.value;
      } else {
        body.model = this.modelSelect.value;
      }

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
      let costUsd: number | null = null;

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
          const step = (event.step as number) || 0;
          const totalSteps = (event.total_steps as number) || 4;

          switch (stage) {
            case "metadata":
              this.setProgress(step, totalSteps, `⏳ ${message}`);
              break;
            case "metadata_done":
              this.setProgress(step, totalSteps, `✓ ${message}`);
              break;
            case "transcript":
              this.setProgress(step, totalSteps, `⏳ ${message}`);
              break;
            case "transcript_done":
              this.setProgress(step, totalSteps, `✓ ${message}`);
              this.setDetail(`${event.segments ?? "?"} segments, ${((event.transcript_chars as number) ?? 0).toLocaleString()} chars`);
              break;
            case "claude":
            case "claude_extended":
              this.setProgress(step, totalSteps, `🤖 ${message}`);
              if (event.input_tokens || event.output_tokens) {
                const inTok = ((event.input_tokens as number) ?? 0).toLocaleString();
                const outTok = ((event.output_tokens as number) ?? 0).toLocaleString();
                const elapsed = event.elapsed ? `${event.elapsed}s` : "";
                this.setDetail(`Input: ${inTok} tokens · Output: ${outTok} tokens${elapsed ? ` · ${elapsed}` : ""}`);
              }
              break;
            case "claude_done":
              this.setProgress(step, totalSteps, `✓ ${message}`);
              if (event.cost_usd != null) {
                costUsd = event.cost_usd as number;
              }
              if (event.input_tokens || event.output_tokens) {
                const inTok = ((event.input_tokens as number) ?? 0).toLocaleString();
                const outTok = ((event.output_tokens as number) ?? 0).toLocaleString();
                const costStr = costUsd != null ? ` · $${costUsd.toFixed(4)}` : "";
                this.setDetail(`Total: ${inTok} input + ${outTok} output tokens${costStr}`);
              }
              break;
            case "building":
              this.setProgress(step, totalSteps, `💾 ${message}`);
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
      this.progressBarInner.style.width = "100%";
      this.setStatus(`✓ Done! Note saved as "${file.path}"`, "success");
      this.setDetail(costUsd != null ? `Estimated cost: $${costUsd.toFixed(4)}` : "");

      await this.app.workspace.openLinkText(file.path, "", false);
    } catch (error) {
      console.error("[YT Obsidian]", error);
      this.setStatus(`❌ ${error.message}`, "error");
      this.setDetail("");
      this.progressWrapper.style.display = "none";
      this.importBtn.disabled = false;
      this.urlInput.disabled = false;
      this.modeToggleBtns.forEach((b) => (b.disabled = false));
      this.modelSelect.disabled = false;
    }
  }

  setStatus(msg: string, type: "info" | "success" | "warning" | "error") {
    this.statusEl.setText(msg);
    this.statusEl.className = `yt-obsidian-status yt-obsidian-status--${type}`;
  }

  setProgress(step: number, totalSteps: number, msg: string) {
    this.progressWrapper.style.display = "block";
    const pct = Math.round((Math.max(step - 1, 0) / totalSteps) * 100);
    this.progressBarInner.style.width = `${pct}%`;
    this.statusEl.setText(msg);
    this.statusEl.className = "yt-obsidian-status yt-obsidian-status--info";
  }

  setDetail(detail: string) {
    this.detailEl.setText(detail);
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
