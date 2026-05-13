import {
    App,
    Notice,
    PluginSettingTab,
    Setting,
} from "obsidian";
import type YTObsidianPlugin from "./main";

export interface YTObsidianSettings {
    apiUrl: string;
    outputFolder: string;          // YouTube notes
    podcastOutputFolder: string;   // Podcast notes
    whisperLanguage: string;       // "auto" or ISO 639-1 ("en", "de", …)
    keepWhisperWarm: boolean;      // start whisper-server on plugin load (vs. lazy on first use)
}

export const DEFAULT_SETTINGS: YTObsidianSettings = {
    apiUrl: "http://localhost:8000",
    outputFolder: "YouTube",
    podcastOutputFolder: "Podcasts",
    whisperLanguage: "auto",
    keepWhisperWarm: false,
};

export class YTObsidianSettingTab extends PluginSettingTab {
    plugin: YTObsidianPlugin;

    constructor(app: App, plugin: YTObsidianPlugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display(): void {
        const { containerEl } = this;
        containerEl.empty();
        containerEl.createEl("h2", { text: "Media to Obsidian" });

        new Setting(containerEl)
            .setName("Backend API URL")
            .setDesc("URL of the local Python backend. Usually the default.")
            .addText((text) =>
                text
                    .setPlaceholder("http://localhost:8000")
                    .setValue(this.plugin.settings.apiUrl)
                    .onChange(async (value) => {
                        this.plugin.settings.apiUrl = value.trim();
                        await this.plugin.saveSettings();
                    })
            );

        // ── YouTube ──────────────────────────────────────────────────────────
        containerEl.createEl("h3", { text: "YouTube" });

        new Setting(containerEl)
            .setName("YouTube notes folder")
            .setDesc("Vault folder where YouTube notes will be saved (created if missing). Leave empty for vault root.")
            .addText((text) =>
                text
                    .setPlaceholder("YouTube")
                    .setValue(this.plugin.settings.outputFolder)
                    .onChange(async (value) => {
                        this.plugin.settings.outputFolder = value.trim();
                        await this.plugin.saveSettings();
                    })
            );

        const cookieSetting = new Setting(containerEl)
            .setName("cookies.txt (fallback only)")
            .setDesc(
                "The backend uses Safari cookies automatically. Upload a Netscape cookies.txt only if you can't grant Full Disk Access to the backend (see README)."
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
                    cookieStatusEl.setText("Fallback cookies.txt is uploaded.");
                    cookieStatusEl.style.color = "var(--text-success)";
                } else {
                    cookieStatusEl.setText("No fallback uploaded (fine — Safari path is primary).");
                    cookieStatusEl.style.color = "var(--text-muted)";
                }
            } catch {
                cookieStatusEl.setText("Could not reach backend.");
                cookieStatusEl.style.color = "var(--text-error)";
            }
        };
        refreshCookieStatus();

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
                new Notice("Cookie file uploaded.");
            } catch {
                new Notice("Failed to upload cookie file. Is the backend running?");
            }
            fileInput.value = "";
            refreshCookieStatus();
        });

        cookieSetting.addButton((btn) =>
            btn.setButtonText("Upload…").onClick(() => fileInput.click())
        );

        cookieSetting.addButton((btn) =>
            btn
                .setButtonText("Remove")
                .setWarning()
                .onClick(async () => {
                    try {
                        const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
                        await fetch(`${apiUrl}/cookies`, { method: "DELETE" });
                        new Notice("Cookie file removed.");
                    } catch {
                        new Notice("Failed to remove cookie file.");
                    }
                    refreshCookieStatus();
                })
        );

        // ── Podcasts ─────────────────────────────────────────────────────────
        containerEl.createEl("h3", { text: "Podcasts" });

        new Setting(containerEl)
            .setName("Podcast notes folder")
            .setDesc("Vault folder where podcast notes will be saved. Leave empty for vault root.")
            .addText((text) =>
                text
                    .setPlaceholder("Podcasts")
                    .setValue(this.plugin.settings.podcastOutputFolder)
                    .onChange(async (value) => {
                        this.plugin.settings.podcastOutputFolder = value.trim();
                        await this.plugin.saveSettings();
                    })
            );

        new Setting(containerEl)
            .setName("Whisper language")
            .setDesc("Default language for the whisper transcriber. 'auto' lets whisper detect; ISO codes like 'en', 'de', 'fr' work too. Can be overridden per import.")
            .addText((text) =>
                text
                    .setPlaceholder("auto")
                    .setValue(this.plugin.settings.whisperLanguage)
                    .onChange(async (value) => {
                        this.plugin.settings.whisperLanguage = value.trim() || "auto";
                        await this.plugin.saveSettings();
                    })
            );

        const whisperStatusEl = containerEl.createDiv({ cls: "yt-obsidian-whisper-status" });
        whisperStatusEl.style.fontSize = "12px";
        whisperStatusEl.style.marginTop = "-4px";
        whisperStatusEl.style.marginBottom = "12px";
        whisperStatusEl.style.paddingLeft = "18px";

        const refreshWhisperStatus = async () => {
            try {
                const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
                const resp = await fetch(`${apiUrl}/whisper/status`);
                const data = await resp.json();
                if (data.available) {
                    const idleMin = Math.round((data.idle_timeout_seconds ?? 1800) / 60);
                    whisperStatusEl.setText(
                        `whisper-server is running at ${data.server_url} (auto-stops after ${idleMin} min idle).`
                    );
                    whisperStatusEl.style.color = "var(--text-success)";
                } else {
                    whisperStatusEl.setText(
                        "whisper-server is stopped. It starts automatically on the first podcast that needs it."
                    );
                    whisperStatusEl.style.color = "var(--text-muted)";
                }
            } catch {
                whisperStatusEl.setText("Could not reach backend.");
                whisperStatusEl.style.color = "var(--text-error)";
            }
        };
        refreshWhisperStatus();

        new Setting(containerEl)
            .setName("Keep whisper-server warm")
            .setDesc(
                "Start whisper-server when Obsidian opens and stop it when Obsidian closes. " +
                "Off: lazy-start on the first podcast that needs whisper (~3s extra latency on first use). " +
                "Whichever mode you pick, the server auto-stops after 30 min of inactivity."
            )
            .addToggle((toggle) =>
                toggle
                    .setValue(this.plugin.settings.keepWhisperWarm)
                    .onChange(async (value) => {
                        this.plugin.settings.keepWhisperWarm = value;
                        await this.plugin.saveSettings();
                        const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
                        try {
                            await fetch(`${apiUrl}/whisper/${value ? "start" : "stop"}`, { method: "POST" });
                        } catch {
                            // Best-effort — backend may be down; refresh will show it
                        }
                        setTimeout(refreshWhisperStatus, value ? 4000 : 500);
                    })
            );

        new Setting(containerEl)
            .setName("Start / stop whisper-server now")
            .setDesc("Manual control. The toggle above sets the auto-start behavior at plugin load.")
            .addButton((btn) =>
                btn.setButtonText("Start").onClick(async () => {
                    const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
                    try {
                        await fetch(`${apiUrl}/whisper/start`, { method: "POST" });
                        new Notice("Starting whisper-server…");
                    } catch {
                        new Notice("Could not reach backend.");
                    }
                    setTimeout(refreshWhisperStatus, 4000);
                })
            )
            .addButton((btn) =>
                btn.setButtonText("Stop").onClick(async () => {
                    const apiUrl = this.plugin.settings.apiUrl.replace(/\/$/, "");
                    try {
                        await fetch(`${apiUrl}/whisper/stop`, { method: "POST" });
                        new Notice("Stopped whisper-server.");
                    } catch {
                        new Notice("Could not reach backend.");
                    }
                    setTimeout(refreshWhisperStatus, 500);
                })
            );
    }
}
