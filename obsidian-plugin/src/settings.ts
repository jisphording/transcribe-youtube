import {
    App,
    Notice,
    PluginSettingTab,
    Setting,
} from "obsidian";
import type YTObsidianPlugin from "./main";

// ─── Settings ────────────────────────────────────────────────────────────────

export interface YTObsidianSettings {
    apiUrl: string;
    outputFolder: string;
    cookieBrowser: string;
}

export const DEFAULT_SETTINGS: YTObsidianSettings = {
    apiUrl: "http://localhost:8000",
    outputFolder: "YouTube",
    cookieBrowser: "",
};

// ─── Settings Tab ─────────────────────────────────────────────────────────────

export class YTObsidianSettingTab extends PluginSettingTab {
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
