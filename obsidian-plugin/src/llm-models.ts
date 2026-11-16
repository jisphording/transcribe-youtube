export const MODEL_HAIKU = "claude-haiku-5-5";
export const MODEL_SONNET = "claude-sonnet-5-5";
export const MODEL_OPUS = "claude-opus-5-5";

export const CLAUDE_MODELS: Record<string, string> = {
    [MODEL_HAIKU]: "Claude Haiku 5.5 (fastest)",
    [MODEL_SONNET]: "Claude Sonnet 5.5 (balanced)",
    [MODEL_OPUS]: "Claude Opus 5.5 (high quality)",
    "claude-fable-5-1": "Claude Fable 5.1 (advanced reasoning)",
};

export const OPENROUTER_MODELS: Record<string, string> = {
    "openrouter:deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash (bulk imports)",
    "openrouter:qwen/qwen3.5-flash": "Qwen3.5 Flash (budget)",
    "openrouter:google/gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite (preview)",
};

export const DEFAULT_OPENROUTER_MODEL = Object.keys(OPENROUTER_MODELS)[0];

export const ANTIGRAVITY_MODELS: Record<string, string> = {
    "antigravity:gemini-3.8-flash-medium": "Gemini 3.8 Flash (subscription quota)",
};
export const DEFAULT_ANTIGRAVITY_MODEL = Object.keys(ANTIGRAVITY_MODELS)[0];

export const CLAUDE_CLI_HAIKU = "claude-cli:haiku";
export const CLAUDE_CLI_SONNET = "claude-cli:sonnet";
export const CLAUDE_CLI_OPUS = "claude-cli:opus";
export const CLAUDE_CLI_MODELS: Record<string, string> = {
    [CLAUDE_CLI_HAIKU]: "Claude Haiku (subscription, fastest)",
    [CLAUDE_CLI_SONNET]: "Claude Sonnet (subscription, balanced)",
    [CLAUDE_CLI_OPUS]: "Claude Opus (subscription, high quality)",
    "claude-cli:fable": "Claude Fable (subscription, advanced reasoning)",
};
export const DEFAULT_CLAUDE_CLI_MODEL = CLAUDE_CLI_HAIKU;

export function isSubscriptionModel(model: string): boolean {
    return model.startsWith("antigravity:") || model.startsWith("claude-cli:");
}
