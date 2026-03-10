export interface SSECallbacks {
    onProgress(step: number, totalSteps: number, msg: string): void;
    onDetail(detail: string): void;
}

export interface SSEResult {
    filename: string;
    content: string;
    costUsd: number | null;
}

export async function processSSEStream(response: Response, callbacks: SSECallbacks): Promise<SSEResult> {
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
                    callbacks.onProgress(step, totalSteps, `\u23F3 ${message}`);
                    break;
                case "metadata_done":
                    callbacks.onProgress(step, totalSteps, `\u2713 ${message}`);
                    break;
                case "transcript":
                    callbacks.onProgress(step, totalSteps, `\u23F3 ${message}`);
                    break;
                case "transcript_done":
                    callbacks.onProgress(step, totalSteps, `\u2713 ${message}`);
                    callbacks.onDetail(`${event.segments ?? "?"} segments, ${((event.transcript_chars as number) ?? 0).toLocaleString()} chars`);
                    break;
                case "claude":
                case "claude_extended":
                    callbacks.onProgress(step, totalSteps, `\uD83E\uDD16 ${message}`);
                    if (event.input_tokens || event.output_tokens) {
                        const inTok = ((event.input_tokens as number) ?? 0).toLocaleString();
                        const outTok = ((event.output_tokens as number) ?? 0).toLocaleString();
                        const elapsed = event.elapsed ? `${event.elapsed}s` : "";
                        callbacks.onDetail(`Input: ${inTok} tokens \u00B7 Output: ${outTok} tokens${elapsed ? ` \u00B7 ${elapsed}` : ""}`);
                    }
                    break;
                case "claude_done":
                    callbacks.onProgress(step, totalSteps, `\u2713 ${message}`);
                    if (event.cost_usd != null) {
                        costUsd = event.cost_usd as number;
                    }
                    if (event.input_tokens || event.output_tokens) {
                        const inTok = ((event.input_tokens as number) ?? 0).toLocaleString();
                        const outTok = ((event.output_tokens as number) ?? 0).toLocaleString();
                        const costStr = costUsd != null ? ` \u00B7 $${costUsd.toFixed(4)}` : "";
                        callbacks.onDetail(`Total: ${inTok} input + ${outTok} output tokens${costStr}`);
                    }
                    break;
                case "building":
                    callbacks.onProgress(step, totalSteps, `\uD83D\uDCBE ${message}`);
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

    return { ...doneData, costUsd };
}
