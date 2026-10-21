/** Ordinary Pi delegation. Process handles live only for this awaited tool call. */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { TextDecoder } from "node:util";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export const RECORD_ENV = "FRUTLUPS_PI_RECORD_BYTES";
export const RETURN_BUDGET = 512 * 1024;
const FINAL_TEXT_CAP = 1024 * 1024;
// Pinned Pi Usage requires every count and cost field; optional subsets are not aggregated.
const COUNT_KEYS = ["input", "cacheRead", "cacheWrite", "output", "totalTokens"];
const COST_KEYS = ["input", "cacheRead", "cacheWrite", "output", "total"];
const self = fileURLToPath(import.meta.url);
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf8");
const integer = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const cost = (value: unknown) => typeof value === "number" && Number.isFinite(value)
    && value >= 0 && value <= 2 ** 63 - 1;

export function recordLimit(value: string | undefined): number {
    if (value === undefined) return 2 * 1024 * 1024;
    if (!/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value))) {
        throw new Error("limits.pi_record_mib: invalid FRUTLUPS_PI_RECORD_BYTES");
    }
    return Number(value);
}

/** Same bounded JSONL/settlement policy as seats.pi._Stream; no event history. */
export class RecordReader {
    cap: number;
    pending = Buffer.alloc(0);
    pendingLength = 0;
    discarding = false;
    finished = false;
    settled = false;
    assistantPending = false;
    text: string | null = null;
    stopReason: string | null = null;
    providerFailure: string | null = null;
    diagnostic: string | null = null;
    counts: Record<string, number> = {};
    costs: Record<string, number> = {};
    complete = true;

    constructor(cap: number) {
        if (!Number.isSafeInteger(cap) || cap <= 0) throw new Error("Invalid Pi record byte limit");
        this.cap = cap;
    }

    error(text: string) {
        this.diagnostic ??= text;
        this.complete = false;
    }

    feed(chunk: Buffer) {
        if (this.finished) return;
        if (chunk.length === 0) {
            this.finished = true;
            if (this.pendingLength && !this.discarding) this.record(this.pending.subarray(0, this.pendingLength));
            this.pending = Buffer.alloc(0);
            this.pendingLength = 0;
            return;
        }
        let start = 0;
        while (start < chunk.length) {
            const end = chunk.indexOf(10, start);
            const stop = end < 0 ? chunk.length : end;
            if (!this.discarding) {
                const needed = this.pendingLength + stop - start;
                if (needed > this.cap) {
                    this.error(`Pi JSONL record exceeds ${this.cap / (1024 * 1024)} MiB limit `
                        + `(${this.cap} bytes; limits.pi_record_mib)`);
                    this.pending = Buffer.alloc(0);
                    this.pendingLength = 0;
                    this.discarding = true;
                } else {
                    if (needed > this.pending.length) {
                        const buffer = Buffer.allocUnsafe(Math.min(this.cap,
                            Math.max(8192, this.pending.length * 2, needed)));
                        this.pending.copy(buffer, 0, 0, this.pendingLength);
                        this.pending = buffer;
                    }
                    chunk.copy(this.pending, this.pendingLength, start, stop);
                    this.pendingLength = needed;
                }
            }
            if (end < 0) break;
            if (!this.discarding) this.record(this.pending.subarray(0, this.pendingLength));
            this.pendingLength = 0;
            this.discarding = false;
            start = end + 1;
        }
    }

    record(raw: Buffer) {
        let text: string;
        try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw); }
        catch { this.error("Pi JSONL record contains invalid UTF-8"); return; }
        let event: any;
        try {
            event = JSON.parse(text);
            if (!event || typeof event !== "object" || Array.isArray(event)) throw new Error();
        } catch { this.error("Pi JSONL record is malformed"); return; }
        if (event.type === "agent_start") this.settled = false;
        if (event.type === "agent_settled") this.settled = true;
        if (event.type === "turn_start") this.assistantPending = true;
        if (event.type === "auto_retry_end" && event.success === false) this.providerFailure = "transport";
        const message = event.message;
        if (!message || typeof message !== "object" || Array.isArray(message)) return;
        const assistant = message.role === "assistant";
        if (event.type === "message_start" && assistant) this.assistantPending = true;
        if (event.type !== "message_end" || !["assistant", "toolResult"].includes(message.role)) return;
        if (assistant) {
            this.settled = false;
            this.assistantPending = false;
            this.providerFailure = null;
            this.stopReason = String(message.stopReason ?? "").slice(0, 64);
            if (this.stopReason === "error") {
                const error = String(message.errorMessage ?? "").toLowerCase();
                this.providerFailure = /401|authentication_error/.test(error) ? "auth"
                    : /429|rate_limit|quota|overloaded/.test(error) ? "capacity" : "transport";
            }
            const content = Array.isArray(message.content) ? message.content : [];
            const answer = content.filter((p: any) => p && typeof p.text === "string")
                .map((p: any) => p.text).join("");
            // Buffer would replace lone surrogates; reject them like Python's strict encoding.
            if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(answer)) {
                this.error("Pi final text contains invalid UTF-8"); this.text = null;
            } else if (Buffer.byteLength(answer, "utf8") > FINAL_TEXT_CAP) {
                this.error("Pi final text exceeds 1 MiB UTF-8 limit"); this.text = null;
            } else this.text = answer;
        } else if (message.usage === undefined || message.usage === null) {
            if (message.toolName !== "subagent") return;
            // Partial delegation omits native Usage; its known values travel in details.usage.
            this.complete = false;
        }
        if (message.role === "toolResult" && (message.isError === true
            || (message.toolName === "subagent" && message.details?.usage_completeness !== "complete"))) {
            this.complete = false;
        }
        const usage = message.usage ?? (assistant ? undefined : message.details?.usage);
        for (const key of COUNT_KEYS) {
            const value = usage?.[key];
            if (!integer(value)) { this.complete = false; continue; }
            const total = (this.counts[key] ?? 0) + value;
            if (!integer(total)) this.error("Pi usage exceeds numeric limit");
            else this.counts[key] = total;
        }
        for (const key of COST_KEYS) {
            const value = usage?.cost?.[key];
            if (!cost(value)) { this.complete = false; continue; }
            const total = (this.costs[key] ?? 0) + value;
            if (!cost(total)) this.error("Pi usage exceeds numeric limit");
            else this.costs[key] = total;
        }
    }

    failure(exit: number | null, exception: string | null = null): string | null {
        if (exception) return "transport";
        if (this.providerFailure) return this.providerFailure;
        if (exit !== 0) return "transport";
        if (this.diagnostic || !this.finished || !this.settled || this.assistantPending
            || !this.text || !["", "stop"].includes(this.stopReason ?? "")) return "output";
        return null;
    }

    usage() {
        return { ...this.counts, cost: { ...this.costs } };
    }

    completeness(failure: string | null) {
        if (!Object.keys(this.counts).length && !Object.keys(this.costs).length) return "unknown";
        return this.complete && failure === null ? "complete" : "partial";
    }
}

function excerpt(text: string, budget: number): string {
    if (bytes(text) <= budget) return text;
    const omitted = `[... ${Buffer.byteLength(text, "utf8")} source bytes; output not retained]`;
    let low = 0, high = text.length;
    while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        if (bytes(text.slice(0, mid) + omitted) <= budget) low = mid;
        else high = mid - 1;
    }
    // Avoid splitting a surrogate pair at the head boundary.
    if (low && /[\uD800-\uDBFF]/u.test(text[low - 1])) low--;
    return bytes(text.slice(0, low) + omitted) <= budget ? text.slice(0, low) + omitted : "";
}

function result(summaries: any[]) {
    const counts: Record<string, number> = {}, costs: Record<string, number> = {};
    let complete = true;
    let numericFailure = false;
    for (const summary of summaries) {
        complete &&= summary.usage_completeness === "complete";
        for (const key of COUNT_KEYS) {
            const value = summary.usage[key];
            if (value !== undefined) {
                const total = (counts[key] ?? 0) + value;
                if (integer(total)) counts[key] = total;
                else numericFailure = true;
            }
        }
        for (const key of COST_KEYS) {
            const value = summary.usage.cost[key];
            if (value !== undefined) {
                const total = (costs[key] ?? 0) + value;
                if (cost(total)) costs[key] = total;
                else numericFailure = true;
            }
        }
    }
    const usage = { ...counts, cost: costs };
    const known = Object.keys(counts).length > 0 || Object.keys(costs).length > 0;
    const conforming = COUNT_KEYS.every(key => key in counts) && COST_KEYS.every(key => key in costs);
    const usage_completeness = !known ? "unknown"
        : complete && !numericFailure && conforming ? "complete" : "partial";
    // Native Usage cannot mark unknown fields, so only a complete aggregate uses it; otherwise
    // details.usage carries the known values once and readers use it only without native usage.
    const native = usage_completeness === "complete";
    const accounting = native ? { usage_completeness } : { usage_completeness, usage };
    const failed = summaries.filter(s => s.status !== "completed").length;
    let details: any = { ...accounting, summaries: summaries.map(({ text, ...s }) => s) };
    if (bytes(details) > RETURN_BUDGET / 4) {
        details = { ...accounting, count: summaries.length, failed,
            omitted: `[per-helper details omitted: ${summaries.length}]` };
    }
    const output: any = { content: [], details, ...(native ? { usage } : {}),
        isError: failed > 0 || numericFailure };
    const share = Math.max(0, Math.floor((RETURN_BUDGET - bytes(output) - 128) / summaries.length));
    if (share >= 96) {
        output.content = summaries.map((s, i) => ({ type: "text",
            text: excerpt(`[${i + 1} ${s.status}] ${s.text ?? s.error ?? ""}`, Math.max(0, share - 40)) }));
    } else {
        output.content = [{ type: "text", text: `[outputs omitted: ${summaries.length}]` }];
    }
    if (bytes(output) > RETURN_BUDGET) {
        output.content = [{ type: "text", text: `[outputs omitted: ${summaries.length}]` }];
    }
    if (numericFailure) {
        // Retain known representable spend, labelled partial, without invalid totals.
        output.content = [{ type: "text", text: "Delegation usage exceeds numeric limit" }];
    }
    return output;
}

function runChild(task: any, ctx: any, tools: string[], cap: number, thinking: string, signal?: AbortSignal) {
    return new Promise<any>((resolve) => {
        const reader = new RecordReader(cap);
        const model = task.model ?? (ctx.model && `${ctx.model.provider}/${ctx.model.id}`);
        if (!model || !process.argv[1]) throw new Error("Pi delegation needs the current CLI and model");
        const argv = [process.argv[1], "--mode", "json", "-p", "--no-session", "--no-extensions",
            "-e", self, "--no-skills", "--no-prompt-templates", "--tools", tools.join(","),
            "--model", model, "--thinking", thinking];
        const child = spawn(process.execPath, argv, { cwd: ctx.cwd, shell: false, windowsHide: true,
            env: { ...process.env, [RECORD_ENV]: String(cap) }, stdio: ["pipe", "pipe", "pipe"] });
        let exception: string | null = null;
        let tail = Buffer.alloc(0);
        let escalation: ReturnType<typeof setTimeout> | undefined;
        const abort = () => {
            child.kill("SIGTERM");
            escalation ??= setTimeout(() => child.kill("SIGKILL"), 5000);
        };
        child.on("error", (error) => { exception = String(error); });
        child.stdout.on("data", chunk => reader.feed(chunk));
        child.stdout.on("error", () => { exception = "Pi child stdout failed"; });
        child.stderr.on("data", chunk => { tail = Buffer.concat([tail, chunk]).subarray(-16 * 1024); });
        child.stderr.on("error", () => { exception = "Pi child stderr failed"; });
        child.stdin.on("error", () => { exception ??= "Pi child stdin failed"; });
        child.once("close", (exit) => {
            clearTimeout(escalation);
            signal?.removeEventListener("abort", abort);
            reader.feed(Buffer.alloc(0));
            const failure = reader.failure(exit, exception);
            const error = exception ?? reader.diagnostic ?? (failure ? `Pi ${failure} failure` : null);
            resolve({ status: failure ? "failed" : "completed", failure_class: failure, exit,
                model, stopReason: reader.stopReason, text: reader.text,
                error: error ? excerpt(error, 512) : null,
                stderr: tail.toString("utf8"), usage: reader.usage(),
                usage_completeness: reader.completeness(failure) });
        });
        signal?.addEventListener("abort", abort, { once: true });
        child.stdin.end(task.task);
        if (signal?.aborted) abort();
    });
}

export default function delegate(pi: ExtensionAPI) {
    // Frozen when the explicitly loaded extension is initialized, inherited at every nesting level.
    const cap = recordLimit(process.env[RECORD_ENV]);
    const taskSchema = { type: "object", additionalProperties: false, required: ["task"],
        properties: { task: { type: "string", minLength: 1 }, model: { type: "string", minLength: 1 },
            thinking: { type: "string", minLength: 1 } } };
    pi.registerTool({
        name: "subagent", label: "Delegate", description: "Delegate scoped work and await all helper results.",
        parameters: { anyOf: [taskSchema, { type: "object", additionalProperties: false,
            required: ["tasks"], properties: { tasks: { type: "array", minItems: 1, items: taskSchema } } }] } as any,
        async execute(_id, params: any, signal, _update, ctx) {
            // Validate even when a harness calls execute directly instead of Pi's schema validator.
            const tasks = params?.tasks ?? [params];
            if (!Array.isArray(tasks) || !tasks.length
                || (params?.tasks !== undefined && Object.keys(params).some(k => k !== "tasks"))
                || tasks.some(t => !t || typeof t.task !== "string" || !t.task
                    || Object.keys(t).some(k => !["task", "model", "thinking"].includes(k))
                    || ["model", "thinking"].some(k => t[k] !== undefined && (typeof t[k] !== "string" || !t[k])))) {
                throw new Error("Invalid subagent task; cwd, tools and parser limits are parent-owned");
            }
            if (signal?.aborted) throw new Error("Pi delegation aborted");
            if (!process.argv[1] || tasks.some(t => !t.model && !ctx.model)) {
                throw new Error("Pi delegation needs the current CLI and model");
            }
            const tools = [...pi.getActiveTools()];
            const closed = await Promise.allSettled(tasks.map(task => runChild(task, ctx, tools, cap,
                task.thinking ?? ctx.thinkingLevel ?? pi.getThinkingLevel(), signal)));
            if (signal?.aborted) throw new Error("Pi delegation aborted");
            // A launch rejection is a bounded failed summary; started siblings keep their usage.
            const summaries = closed.map((child, i) => child.status === "fulfilled" ? child.value : {
                status: "failed", failure_class: "transport", exit: null,
                model: tasks[i].model ?? `${ctx.model.provider}/${ctx.model.id}`, stopReason: null,
                text: null, error: excerpt(`Pi child launch failed: ${child.reason}`, 512), stderr: "",
                usage: { cost: {} }, usage_completeness: "unknown" });
            return result(summaries);
        },
    });
}
