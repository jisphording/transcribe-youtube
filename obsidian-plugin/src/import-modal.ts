import { App, Modal, Notice } from "obsidian";
import type YTObsidianPlugin from "./main";
import { extractVideoId, findExistingNote } from "./youtube-utils";
import { processSSEStream } from "./sse-handler";

export class YouTubeImportModal extends Modal {
    plugin: YTObsidianPlugin;
    urlInput: HTMLInputElement;
    importMode: "transcript" | "extended_summary" | "focus_topic" = "transcript";
    modeToggleBtns: HTMLButtonElement[] = [];
    focusTopicWrapper: HTMLElement;
    focusTopicInput: HTMLTextAreaElement;
    modelSelect: HTMLSelectElement;
    extractResourcesCheckbox: HTMLInputElement;
    statusEl: HTMLElement;
    detailEl: HTMLElement;
    progressWrapper: HTMLElement;
    progressBarOuter: HTMLElement;
    progressBarInner: HTMLElement;
    importBtn: HTMLButtonElement;
    cancelBtn: HTMLButtonElement;
    btnRow: HTMLElement;
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

        const modes: Array<{ value: "transcript" | "extended_summary" | "focus_topic"; label: string }> = [
            { value: "transcript", label: "Transcript" },
            { value: "extended_summary", label: "Extended Summary" },
            { value: "focus_topic", label: "Focus Topic" },
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
                this.focusTopicWrapper.style.display = value === "focus_topic" ? "block" : "none";
                if (value === "transcript") {
                    this.modelSelect.value = "claude-haiku-4-5-20251001";
                } else {
                    this.modelSelect.value = "claude-sonnet-4-6";
                }
            });
            return btn;
        });

        // Focus topic input (shown only in focus_topic mode)
        this.focusTopicWrapper = toggleWrapper.createDiv({ cls: "yt-obsidian-focus-wrapper" });
        this.focusTopicWrapper.style.marginTop = "8px";
        this.focusTopicWrapper.style.display = "none";

        this.focusTopicInput = this.focusTopicWrapper.createEl("textarea", {
            placeholder: 'e.g. "Focus on the guest\'s views on marketing for junior designers"',
            cls: "yt-obsidian-focus-input",
        });
        this.focusTopicInput.style.width = "100%";
        this.focusTopicInput.style.minHeight = "56px";
        this.focusTopicInput.style.fontSize = "13px";
        this.focusTopicInput.style.resize = "vertical";
        this.focusTopicInput.style.boxSizing = "border-box";

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

        // Extract resources toggle
        const resourcesWrapper = contentEl.createDiv({ cls: "yt-obsidian-resources-wrapper" });
        resourcesWrapper.style.marginTop = "10px";
        resourcesWrapper.style.display = "flex";
        resourcesWrapper.style.alignItems = "center";
        resourcesWrapper.style.gap = "8px";

        this.extractResourcesCheckbox = resourcesWrapper.createEl("input", { type: "checkbox" });
        this.extractResourcesCheckbox.id = "yt-extract-resources";
        this.extractResourcesCheckbox.checked = true;

        const resourcesLabel = resourcesWrapper.createEl("label", {
            text: "Extract mentioned products, tools & services",
            attr: { for: "yt-extract-resources" },
        });
        resourcesLabel.style.fontSize = "13px";
        resourcesLabel.style.cursor = "pointer";

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
        this.btnRow = contentEl.createDiv({ cls: "yt-obsidian-btn-row" });
        this.btnRow.style.display = "flex";
        this.btnRow.style.justifyContent = "flex-end";
        this.btnRow.style.gap = "8px";
        this.btnRow.style.marginTop = "16px";

        this.cancelBtn = this.btnRow.createEl("button", { text: "Cancel" });
        this.cancelBtn.addEventListener("click", () => this.close());

        this.importBtn = this.btnRow.createEl("button", {
            text: "Import",
            cls: "mod-cta",
        });
        this.importBtn.addEventListener("click", () => this.startImport());

        setTimeout(() => this.urlInput.focus(), 50);
    }

    async startImport() {
        const url = this.urlInput.value.trim();
        if (!url) {
            this.setStatus("\u26A0\uFE0F Please enter a YouTube URL.", "warning");
            return;
        }

        if (!url.includes("youtube.com") && !url.includes("youtu.be")) {
            this.setStatus("\u26A0\uFE0F That doesn't look like a YouTube URL.", "warning");
            return;
        }

        if (!this.skipDuplicateCheck) {
            const videoId = extractVideoId(url);
            if (videoId) {
                const folder = this.plugin.settings.outputFolder.trim();
                const existing = await findExistingNote(this.app, folder, videoId);
                if (existing) {
                    this.setStatus(
                        `\u26A0\uFE0F Already imported as "${existing.path}". Click Import again to overwrite.`,
                        "warning"
                    );
                    this.skipDuplicateCheck = true;
                    this.btnRow.empty();
                    const cancelBtn2 = this.btnRow.createEl("button", { text: "Cancel" });
                    cancelBtn2.addEventListener("click", () => this.close());
                    const goBtn = this.btnRow.createEl("button", { text: "Take me there", cls: "mod-cta" });
                    goBtn.addEventListener("click", async () => {
                        await this.app.workspace.openLinkText(existing.path, "", false);
                        this.close();
                    });
                    const importAgainBtn = this.btnRow.createEl("button", { text: "Import anyway" });
                    importAgainBtn.addEventListener("click", () => {
                        this.btnRow.empty();
                        this.btnRow.appendChild(this.cancelBtn);
                        this.btnRow.appendChild(this.importBtn);
                        this.startImport();
                    });
                    return;
                }
            }
        }
        this.skipDuplicateCheck = false;

        if (this.importMode === "focus_topic" && !this.focusTopicInput.value.trim()) {
            this.setStatus("\u26A0\uFE0F Please enter a focus instruction.", "warning");
            return;
        }

        this.importBtn.disabled = true;
        this.urlInput.disabled = true;
        this.modeToggleBtns.forEach((b) => (b.disabled = true));
        this.modelSelect.disabled = true;
        this.extractResourcesCheckbox.disabled = true;
        this.focusTopicInput.disabled = true;
        this.setStatus("\u23F3 Connecting to backend\u2026", "info");

        try {
            const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
            const body: Record<string, string | boolean> = { url, cookie_browser: "safari" };
            if (this.importMode === "extended_summary") {
                body.extended_summary = true;
                body.include_transcript = false;
                body.extended_model = this.modelSelect.value;
            } else if (this.importMode === "focus_topic") {
                body.focus_topic = this.focusTopicInput.value.trim();
                body.include_transcript = false;
                body.extended_model = this.modelSelect.value;
            } else {
                body.model = this.modelSelect.value;
            }
            if (this.extractResourcesCheckbox.checked) {
                body.extract_resources = true;
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

            const result = await processSSEStream(response, {
                onProgress: (step, totalSteps, msg) => this.setProgress(step, totalSteps, msg),
                onDetail: (detail) => this.setDetail(detail),
            });

            const file = await this.plugin.createNote(result.filename, result.content);
            if (result.resources.length > 0) {
                await this.plugin.createResourceStubs(result.resources);
            }

            new Notice(`Created: ${file.path}`);
            this.progressBarInner.style.width = "100%";
            this.setStatus(`\u2713 Done! Note saved as "${file.path}"`, "success");
            this.setDetail(result.costUsd != null ? `Estimated cost: $${result.costUsd.toFixed(4)}` : "");

            await this.app.workspace.openLinkText(file.path, "", false);

            this.btnRow.empty();
            const closeBtn = this.btnRow.createEl("button", { text: "Ok", cls: "mod-cta" });
            closeBtn.addEventListener("click", () => this.close());
        } catch (error) {
            console.error("[YT Obsidian]", error);
            this.setStatus(`\u274C ${error.message}`, "error");
            this.setDetail("");
            this.progressWrapper.style.display = "none";
            this.importBtn.disabled = false;
            this.urlInput.disabled = false;
            this.modeToggleBtns.forEach((b) => (b.disabled = false));
            this.modelSelect.disabled = false;
            this.extractResourcesCheckbox.disabled = false;
            this.focusTopicInput.disabled = false;
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
