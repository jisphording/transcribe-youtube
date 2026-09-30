import { App, Notice } from "obsidian";
import type YTObsidianPlugin from "./main";
import { collectKnownIds } from "./url-utils";
import { BatchOptions, BatchPreview, confirmBatch, createBatch, fmtDuration, fmtUsd } from "./batch-api";

export interface BatchPanelHost {
    getUrl(): string;
    getOptions(): BatchOptions;
    setStatus(msg: string, type: "info" | "success" | "warning" | "error"): void;
    setBusy(busy: boolean): void;
    close(): void;
}

// Batch section of the Import modal (channel / playlist / podcast show URLs):
// step 1 previews the items + cost estimate, step 2 confirms and hands off to the backend queue.
export class BatchPanel {
    wrapper: HTMLElement;
    private countInput: HTMLInputElement;
    private minInput: HTMLInputElement;
    private previewEl: HTMLElement;
    private previewBtn: HTMLButtonElement;
    private startBtn: HTMLButtonElement;
    private preview: BatchPreview | null = null;
    private previewKey = "";

    constructor(parent: HTMLElement, private app: App, private plugin: YTObsidianPlugin, private host: BatchPanelHost) {
        this.wrapper = parent.createDiv({ cls: "yt-obsidian-batch" });
        this.wrapper.style.marginTop = "12px";
        this.wrapper.style.display = "none";

        const row = this.wrapper.createDiv();
        row.style.display = "flex";
        row.style.flexWrap = "wrap";
        row.style.alignItems = "center";
        row.style.gap = "6px";
        row.style.fontSize = "13px";
        row.createSpan({ text: "Import the latest" });
        this.countInput = row.createEl("input", { type: "number", placeholder: "all" });
        this.countInput.min = "1";
        this.countInput.max = "200";
        this.countInput.style.width = "64px";
        row.createSpan({ text: "items, at least" });
        this.minInput = row.createEl("input", { type: "number" });
        this.minInput.min = "0";
        this.minInput.value = "15";
        this.minInput.style.width = "56px";
        row.createSpan({ text: "min long" });
        const hint = this.wrapper.createDiv({ text: "Leave the count empty to import up to 20 new items. Items already in your vault are skipped." });
        hint.style.fontSize = "12px";
        hint.style.color = "var(--text-muted)";
        hint.style.marginTop = "4px";

        for (const input of [this.countInput, this.minInput]) {
            input.addEventListener("input", () => this.reset());
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter") this.runPreview();
            });
        }

        this.previewEl = this.wrapper.createDiv();
        this.previewEl.style.marginTop = "8px";

        const btnRow = this.wrapper.createDiv();
        btnRow.style.display = "flex";
        btnRow.style.justifyContent = "flex-end";
        btnRow.style.gap = "8px";
        btnRow.style.marginTop = "8px";
        this.previewBtn = btnRow.createEl("button", { text: "Preview", cls: "mod-cta" });
        this.previewBtn.addEventListener("click", () => this.runPreview());
        this.startBtn = btnRow.createEl("button", { text: "Start import", cls: "mod-cta" });
        this.startBtn.style.display = "none";
        this.startBtn.addEventListener("click", () => this.start());
    }

    show(visible: boolean) {
        this.wrapper.style.display = visible ? "block" : "none";
        if (!visible) this.reset();
    }

    get visible(): boolean {
        return this.wrapper.style.display !== "none";
    }

    reset() {
        this.preview = null;
        this.previewEl.empty();
        this.startBtn.style.display = "none";
        this.previewBtn.removeClass("mod-muted");
        this.previewBtn.addClass("mod-cta");
    }

    private key(): string {
        return JSON.stringify([this.host.getUrl(), this.countInput.value, this.minInput.value, this.host.getOptions()]);
    }

    async runPreview() {
        const targets = this.plugin.batchTargets();
        if (!targets) {
            this.host.setStatus("⚠️ Batch import needs the desktop app (the backend writes into the vault folder).", "warning");
            return;
        }
        const countRaw = this.countInput.value.trim();
        const count = countRaw ? parseInt(countRaw, 10) : null;
        if (count !== null && (!(count > 0) || count > 200)) {
            this.host.setStatus("⚠️ Count must be between 1 and 200.", "warning");
            return;
        }
        this.reset();
        this.host.setBusy(true);
        this.host.setStatus("⏳ Listing items and checking your vault…", "info");
        try {
            const known = collectKnownIds(this.app, targets.scanFolders);
            this.previewKey = this.key();
            this.preview = await createBatch(this.plugin.settings.apiUrl, {
                url: this.host.getUrl(),
                count,
                min_minutes: Math.max(0, parseInt(this.minInput.value, 10) || 0),
                options: this.host.getOptions(),
                vault_root: targets.vaultRoot,
                folders: targets.folders,
                scan_roots: targets.scanRoots,
                known_ids: Array.from(known),
            });
            this.renderPreview(this.preview);
        } catch (e) {
            this.host.setStatus(`❌ ${e.message}`, "error");
        } finally {
            this.host.setBusy(false);
        }
    }

    private renderPreview(p: BatchPreview) {
        this.previewEl.empty();
        const fresh = p.items;
        const dupes = p.skipped_duplicates;
        const noun = p.source === "podcast" ? "episodes" : "videos";

        const summary = this.previewEl.createDiv();
        summary.style.fontSize = "13px";
        summary.style.marginBottom = "6px";
        summary.setText(
            `${p.title}: ${fresh.length} to import` + (dupes ? `, ${dupes} already in your vault` : "") +
            (fresh.length ? ` · estimated ${fmtUsd(p.estimate.low)}–${fmtUsd(p.estimate.high)} (${p.estimate.model})` : "")
        );
        const notes: string[] = [];
        if (p.has_more && p.count_requested == null) notes.push("More than 20 new items — showing the latest 20. Enter a count to import more.");
        if (dupes && fresh.length) notes.push(`${dupes} ${noun} already in your vault were skipped — the next new ones are listed instead.`);
        if (p.window_exhausted) notes.push(`Only the latest ${p.searched} ${noun} were checked; older ones were not searched.`);
        if (p.capped) notes.push("Apple lists at most 200 episodes per show — capped at 200.");
        if (p.items.some((i) => i.duration_estimated)) notes.push("~ = the feed has no duration; estimated from the show's other episodes.");
        if (p.source === "youtube" && fresh.length > 1) notes.push("YouTube items run with 15–45 s pauses (max. 60 per hour) to avoid bot checks.");
        for (const n of notes) {
            const el = this.previewEl.createDiv({ text: n });
            el.style.fontSize = "12px";
            el.style.color = "var(--text-muted)";
        }

        const list = this.previewEl.createDiv();
        list.style.maxHeight = "220px";
        list.style.overflowY = "auto";
        list.style.marginTop = "6px";
        list.style.border = "1px solid var(--background-modifier-border)";
        list.style.borderRadius = "6px";
        for (const item of p.items) {
            const row = list.createDiv();
            row.style.display = "flex";
            row.style.gap = "8px";
            row.style.padding = "3px 8px";
            row.style.fontSize = "12px";
            const title = row.createSpan({ text: item.title });
            title.style.flex = "1";
            title.style.overflow = "hidden";
            title.style.textOverflow = "ellipsis";
            title.style.whiteSpace = "nowrap";
            row.createSpan({ text: (item.duration_estimated ? "~" : "") + fmtDuration(item.duration_seconds) });
            row.createSpan({ text: fmtUsd(item.cost_estimate) });
        }

        if (p.batch_id) {
            this.previewBtn.removeClass("mod-cta");
            this.previewBtn.addClass("mod-muted");
            this.startBtn.setText(`Start import (${fresh.length})`);
            this.startBtn.style.display = "";
            this.host.setStatus("Check the list and the estimated cost, then start the import.", "info");
        } else {
            this.host.setStatus(
                dupes
                    ? `✓ Nothing to import — all ${dupes} ${noun} checked${p.window_exhausted ? " (the latest ones)" : ""} are already in your vault.`
                    : "No items match these filters.",
                "warning"
            );
        }
    }

    async start() {
        if (!this.preview?.batch_id) return;
        if (this.key() !== this.previewKey) {
            this.host.setStatus("Options changed — updating the preview first.", "warning");
            await this.runPreview();
            return;
        }
        this.host.setBusy(true);
        try {
            await confirmBatch(this.plugin.settings.apiUrl, this.preview.batch_id);
            new Notice(`Queued ${this.preview.items.length} items from ${this.preview.title}`);
            await this.plugin.activateQueueView();
            this.host.close();
        } catch (e) {
            this.host.setStatus(`❌ ${e.message}`, "error");
            this.host.setBusy(false);
        }
    }
}
