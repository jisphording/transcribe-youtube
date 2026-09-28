import { ItemView, Notice, WorkspaceLeaf, setIcon } from "obsidian";
import type YTObsidianPlugin from "./main";
import {
    Batch, BatchItem, ItemState, Lane, QueueStatus,
    cancelBatch, fmtDuration, fmtUsd, getQueue, resumeLane, retryBatch,
} from "./batch-api";

export const QUEUE_VIEW_TYPE = "media-import-queue";

const POLL_MS = 5000;

const STATE_ICON: Record<ItemState, string> = {
    pending: "⏳",
    running: "▶️",
    done: "✅",
    skipped: "⏭️",
    failed: "❌",
    duplicate: "🔁",
    cancelled: "🚫",
};

// Sidebar view that polls GET /batch while open. The backend does all the work;
// this view only shows progress and offers cancel / retry.
export class QueueView extends ItemView {
    plugin: YTObsidianPlugin;
    private timer: number | null = null;
    private bodyEl: HTMLElement;

    constructor(leaf: WorkspaceLeaf, plugin: YTObsidianPlugin) {
        super(leaf);
        this.plugin = plugin;
    }

    getViewType() {
        return QUEUE_VIEW_TYPE;
    }

    getDisplayText() {
        return "Import queue";
    }

    getIcon() {
        return "list-ordered";
    }

    async onOpen() {
        const root = this.containerEl.children[1] as HTMLElement;
        root.empty();
        root.style.padding = "8px 12px";
        const header = root.createDiv();
        header.style.display = "flex";
        header.style.alignItems = "center";
        header.style.justifyContent = "space-between";
        header.createEl("h4", { text: "Import queue" }).style.margin = "4px 0 8px";
        const refreshBtn = header.createEl("button", { attr: { "aria-label": "Refresh" } });
        setIcon(refreshBtn, "refresh-cw");
        refreshBtn.addEventListener("click", () => this.refresh());
        this.bodyEl = root.createDiv();
        await this.refresh();
        this.timer = window.setInterval(() => this.refresh(), POLL_MS);
        this.registerInterval(this.timer);
    }

    async onClose() {
        if (this.timer != null) window.clearInterval(this.timer);
    }

    private get apiUrl() {
        return this.plugin.settings.apiUrl;
    }

    async refresh() {
        let status: QueueStatus;
        try {
            status = await getQueue(this.apiUrl);
        } catch (e) {
            this.bodyEl.empty();
            this.muted(this.bodyEl, `Backend not reachable: ${e.message}`);
            return;
        }
        this.bodyEl.empty();
        this.renderLane(status, "youtube", "YouTube");
        this.renderLane(status, "podcast", "Podcasts");
        const batches = status.batches.filter((b) => b.state !== "pending_confirmation");
        if (!batches.length) {
            this.muted(this.bodyEl, "No batches yet. Paste a channel, playlist or podcast show URL into Import Media.");
            return;
        }
        for (const batch of batches) this.renderBatch(batch);
    }

    private muted(parent: HTMLElement, text: string): HTMLElement {
        const el = parent.createDiv({ text });
        el.style.fontSize = "12px";
        el.style.color = "var(--text-muted)";
        return el;
    }

    private renderLane(status: QueueStatus, name: "youtube" | "podcast", label: string) {
        const lane: Lane = status.lanes[name];
        const hasWork = status.batches.some((b) => b.source === name && b.state === "queued");
        if (!lane.blocked && !(name === "youtube" && hasWork)) return;
        const box = this.bodyEl.createDiv();
        box.style.fontSize = "12px";
        box.style.marginBottom = "8px";
        if (lane.blocked) {
            const until = new Date(lane.blocked_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            box.style.color = "var(--text-warning)";
            box.setText(`⚠️ ${label} paused until ${until} — ${lane.block_reason ?? "blocked"}`);
            const btn = box.createEl("button", { text: "Resume now" });
            btn.style.marginLeft = "6px";
            btn.addEventListener("click", async () => {
                await resumeLane(this.apiUrl, name).catch((e) => new Notice(e.message));
                this.refresh();
            });
        } else if (lane.hourly_cap) {
            const wait = Math.max(0, Math.round(lane.next_allowed_at - status.now));
            box.style.color = "var(--text-muted)";
            box.setText(
                `${label}: ${lane.done_last_hour}/${lane.hourly_cap} this hour` + (wait ? ` · next in ${wait}s` : "")
            );
        }
    }

    private renderBatch(batch: Batch) {
        const box = this.bodyEl.createDiv();
        box.style.border = "1px solid var(--background-modifier-border)";
        box.style.borderRadius = "6px";
        box.style.padding = "8px";
        box.style.marginBottom = "10px";

        const total = batch.items.length;
        const finished = total - (batch.counts.pending ?? 0) - (batch.counts.running ?? 0);
        const title = box.createDiv({ text: `${batch.source === "podcast" ? "🎙" : "▶"} ${batch.title || batch.source_url}` });
        title.style.fontWeight = "600";
        this.muted(box, `${batch.state} · ${finished}/${total} · cost ${fmtUsd(batch.cost_usd)} (est. ${fmtUsd(batch.estimate.low)}–${fmtUsd(batch.estimate.high)})`);

        const btnRow = box.createDiv();
        btnRow.style.display = "flex";
        btnRow.style.gap = "6px";
        btnRow.style.margin = "6px 0";
        if (batch.state === "queued") {
            btnRow.createEl("button", { text: "Cancel" }).addEventListener("click", async () => {
                await cancelBatch(this.apiUrl, batch.id).catch((e) => new Notice(e.message));
                this.refresh();
            });
        }
        if (batch.counts.failed) {
            btnRow.createEl("button", { text: `Retry ${batch.counts.failed} failed` }).addEventListener("click", async () => {
                await retryBatch(this.apiUrl, batch.id).catch((e) => new Notice(e.message));
                this.refresh();
            });
        }

        const list = box.createDiv();
        for (const item of batch.items) this.renderItem(list, item);
    }

    private renderItem(parent: HTMLElement, item: BatchItem) {
        const row = parent.createDiv();
        row.style.fontSize = "12px";
        row.style.padding = "2px 0";
        const line = row.createDiv();
        line.setText(`${STATE_ICON[item.state]} ${item.title} · ${fmtDuration(item.duration_s)}`);
        if (item.note_path) {
            line.style.cursor = "pointer";
            line.style.textDecoration = "underline";
            line.addEventListener("click", () => this.openNote(item.note_path as string));
        }
        const detail = item.state === "running" ? item.progress : item.error;
        if (detail) {
            const d = this.muted(row, detail);
            d.style.marginLeft = "18px";
            if (item.state === "failed") d.style.color = "var(--text-error)";
        }
    }

    private async openNote(absPath: string) {
        const targets = this.plugin.batchTargets();
        if (!targets || !absPath.startsWith(targets.vaultRoot + "/")) return;
        await this.app.workspace.openLinkText(absPath.slice(targets.vaultRoot.length + 1), "", false);
    }
}
