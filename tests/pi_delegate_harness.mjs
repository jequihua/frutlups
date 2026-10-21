// Provider-free execution of the real registered handler, inside run_process ownership.
import assert from "node:assert/strict";
import { readFile, writeFile, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const here = dirname(fileURLToPath(import.meta.url));
const modulePath = join(here, "../src/frutlups/seats/pi_delegate/index.ts");
const { default: register, RecordReader, RECORD_ENV, RETURN_BUDGET, recordLimit } = await import(pathToFileURL(modulePath));
const cases = JSON.parse(await readFile(process.argv[2], "utf8"));
const scratch = dirname(process.argv[2]);
const fake = join(scratch, "child.mjs");

// This controlled CLI mirrors only the documented JSONL interface. Nested work
// invokes the same extension factory/handler with the inherited transport limit.
await writeFile(fake, `
import { readFile,writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { once } from 'node:events';
let input=''; for await (const chunk of process.stdin) input+=chunk;
const spec=JSON.parse(input);
const option=k=>process.argv[process.argv.indexOf(k)+1];
const usage=spec.usage??{input:1,cacheRead:2,cacheWrite:3,output:4,totalTokens:10,
 cost:{input:0.03125,output:0.0625,cacheRead:0.0078125,cacheWrite:0.0234375,total:0.125}};
const emit=o=>process.stdout.write(JSON.stringify(o)+'\\n');
if(spec.mode==='records') {
 const data=await readFile(spec.path);
 for(let n=0;n<data.length;n+=65536) {
  if(!process.stdout.write(data.subarray(n,n+65536))) await once(process.stdout,'drain');
 }
} else if(spec.mode==='abort') {
 if(process.platform!=='win32') process.on('SIGTERM',()=>{});
 await writeFile(spec.marker,String(process.pid));
 setInterval(()=>{},1000);
} else {
 let text='ok';
 if(spec.mode==='forward') text=JSON.stringify({argv:process.argv.slice(2),stdin:input,
   cwd:process.cwd(),cap:process.env.FRUTLUPS_PI_RECORD_BYTES});
 if(spec.mode==='blob') {
  text='"\\\\\\n\\u0000🍏'.repeat(spec.repeats);
  await writeFile(spec.sizeMarker, String(Buffer.byteLength(text,'utf8')));
  process.stderr.write('diagnostic:'.repeat(1000));
 }
 if(spec.mode==='nested') {
  let tool;
  const {default:factory}=await import(pathToFileURL(option('-e')));
  factory({registerTool:t=>tool=t,getActiveTools:()=>option('--tools').split(','),getThinkingLevel:()=> 'medium'});
  const result=await tool.execute('nested',spec.inner??{task:JSON.stringify({mode:'forward'})},undefined,undefined,
    {cwd:process.cwd(),model:{provider:'p',id:'nested'},thinkingLevel:'medium'});
  emit({type:'message_end',message:{role:'toolResult',toolName:'subagent',...result}});
  text=result.content[0].text;
 }
 if(spec.mode==='delay') await new Promise(r=>setTimeout(r,spec.ms));
 emit({type:'message_end',message:{role:'assistant',stopReason:spec.mode==='failed'?'error':'stop',
  errorMessage:spec.mode==='failed'?'ordinary helper failure':undefined,
  content:[{type:'text',text}],usage}});
 emit({type:'agent_end',willRetry:false});emit({type:'agent_settled'});
 if(spec.closedMarker) await writeFile(spec.closedMarker,'finished');
}
`, "utf8");
process.argv[1] = fake;

function handler(cap = 4 * 1024 * 1024, tools = ["read", "grep", "subagent"]) {
    process.env[RECORD_ENV] = String(cap);
    let tool;
    register({ registerTool: t => tool = t, getActiveTools: () => tools, getThinkingLevel: () => "high" });
    assert.equal(tool.name, "subagent");
    return tool;
}
const ctx = { cwd: scratch, model: { provider: "p", id: "default" }, thinkingLevel: "high" };
const execute = (tool, params, signal) => tool.execute("call", params, signal, undefined, ctx);
const task = spec => ({ task: JSON.stringify(spec) });
// Pinned Pi Usage: every count and all five cost fields. The fake child reports ONE per run.
const ONE = { input: 1, cacheRead: 2, cacheWrite: 3, output: 4, totalTokens: 10,
    cost: { input: 0.03125, output: 0.0625, cacheRead: 0.0078125, cacheWrite: 0.0234375, total: 0.125 } };
const LEGACY = { input: 1, cacheRead: 2, cacheWrite: 3, output: 4, cost: { total: 0.125 } };
const times = (n, usage = ONE) => ({ ...Object.fromEntries(Object.entries(usage)
    .filter(([key]) => key !== "cost").map(([key, value]) => [key, value * n])),
    cost: Object.fromEntries(Object.entries(usage.cost).map(([key, value]) => [key, value * n])) });
// LEGACY plus ONE: fields only ONE reports stay at ONE's value, never padded with zeros.
const LEGACY_AND_ONE = { ...times(2, LEGACY), totalTokens: 10,
    cost: { ...times(2, LEGACY).cost, input: 0.03125, output: 0.0625, cacheRead: 0.0078125, cacheWrite: 0.0234375 } };
function assertUsage(actual, expected) {
    // Exact keys: unknown fields stay absent rather than becoming zero.
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort());
    assert.deepEqual(Object.keys(actual.cost).sort(), Object.keys(expected.cost).sort());
    for (const key of Object.keys(expected)) if (key !== "cost") assert.equal(actual[key], expected[key], key);
    for (const key of Object.keys(expected.cost)) assert.equal(actual.cost[key], expected.cost[key], `cost.${key}`);
}
function assertNative(output, expected) {
    assert.equal(output.details.usage_completeness, "complete");
    assert.equal(output.details.usage, undefined);
    for (const key of ["input", "output", "cacheRead", "cacheWrite", "totalTokens"]) assert.ok(Number.isSafeInteger(output.usage[key]), key);
    for (const key of ["input", "output", "cacheRead", "cacheWrite", "total"]) assert.equal(typeof output.usage.cost[key], "number", key);
    assertUsage(output.usage, expected);
}
function assertPartial(output, expected, completeness = "partial") {
    assert.equal(output.usage, undefined); // Native Usage is never incomplete.
    assert.equal(output.details.usage_completeness, completeness);
    assertUsage(output.details.usage, expected);
}
// Root consumption of one returned tool result, then a recovering parent final.
function consume(output) {
    const reader = new RecordReader(2 * 1024 * 1024);
    reader.feed(Buffer.from(JSON.stringify({ type: "message_end", message: { role: "toolResult", toolName: "subagent", ...output } }) + "\n"));
    reader.feed(Buffer.from(JSON.stringify({ type: "message_end", message: { role: "assistant", stopReason: "stop", content: [{ text: "handled" }] } }) + '\n{"type":"agent_settled"}\n'));
    reader.feed(Buffer.alloc(0));
    assert.equal(reader.failure(0), null);
    return reader;
}
const partial = {};

for (const value of ["", "0", "-1", "1.5", "true", "9007199254740992", " 2", "2x"]) {
    assert.throws(() => recordLimit(value), /limits.pi_record_mib/);
    process.env[RECORD_ENV] = value;
    assert.throws(() => register({ registerTool() {} }), /limits.pi_record_mib/);
}
assert.equal(recordLimit(undefined), 2 * 1024 * 1024);
assert.equal(recordLimit("9007199254740991"), Number.MAX_SAFE_INTEGER);

for (const test of cases) {
    const raw = await readFile(test.path);
    for (const chunk of [1, 7, 65536]) {
        const reader = new RecordReader(test.cap);
        for (let offset = 0; offset < raw.length; offset += chunk) reader.feed(raw.subarray(offset, offset + chunk));
        reader.feed(Buffer.alloc(0));
        assert.equal(reader.failure(0), test.failure, test.name);
        const n = ["oversized-retry", "oversized-aggregate-retry", "recovered-helper"].includes(test.name) ? 2 : 1;
        assert.equal(reader.counts.input, 3 * n);
        assert.equal(reader.counts.cacheRead, 1 * n);
        assert.equal(reader.counts.cacheWrite, 2 * n);
        assert.equal(reader.counts.output, 4 * n);
        assert.equal(reader.counts.totalTokens, 10 * n);
        assert.equal(reader.costs.total, 0.125 * n);
        assert.equal(reader.costs.input, 0.03125 * n);
        assert.equal(reader.costs.output, 0.0625 * n);
        assert.equal(reader.costs.cacheRead, 0.0078125 * n);
        assert.equal(reader.costs.cacheWrite, 0.0234375 * n);
        assert.equal(reader.completeness(test.failure), test.failure || test.name === "recovered-helper" ? "partial" : "complete");
        assert.ok(reader.pending.length <= test.cap);
    }
    // Invoke the registered execute through an actual owned child's pipe too.
    const output = await execute(handler(test.cap), task({ mode: "records", path: test.path }));
    const summary = output.details.summaries[0];
    assert.equal(summary.failure_class, test.failure, test.name);
    assert.equal(output.isError, test.failure !== null);
    assert.equal(summary.usage.input, ["oversized-retry", "oversized-aggregate-retry", "recovered-helper"].includes(test.name) ? 6 : 3);
}

const tool = handler();
process.env[RECORD_ENV] = "1"; // Cannot override the factory's frozen cap.
const forwarded = await execute(tool, { tasks: [
    { ...task({ mode: "forward" }), model: "other/model", thinking: "medium" },
    task({ mode: "forward" }),
] });
const snapshots = forwarded.content.map(c => JSON.parse(c.text.slice(c.text.indexOf("{") )));
assert.equal(snapshots[0].argv[snapshots[0].argv.indexOf("--model") + 1], "other/model");
assert.equal(snapshots[0].argv[snapshots[0].argv.indexOf("--thinking") + 1], "medium");
assert.equal(snapshots[1].argv[snapshots[1].argv.indexOf("--model") + 1], "p/default");
assert.equal(snapshots[1].argv[snapshots[1].argv.indexOf("--thinking") + 1], "high");
for (const value of snapshots) {
    assert.deepEqual(value.argv.slice(0, 7), ["--mode", "json", "-p", "--no-session", "--no-extensions", "-e", modulePath]);
    assert.ok(value.argv.includes("--no-skills"));
    assert.ok(value.argv.includes("--no-prompt-templates"));
    assert.equal(value.argv[value.argv.indexOf("--tools") + 1], "read,grep,subagent");
    assert.equal(value.cwd, scratch);
    assert.equal(value.cap, String(4 * 1024 * 1024));
    assert.deepEqual(JSON.parse(value.stdin), { mode: "forward" });
}
for (const key of ["cwd", "tools", "record_limit", "pi_record_mib"]) {
    await assert.rejects(execute(tool, { ...task({ mode: "forward" }), [key]: "override" }), /parent-owned/);
}
const withoutContextThinking = await tool.execute("fallback", task({ mode: "forward" }), undefined, undefined,
    { ...ctx, thinkingLevel: undefined });
const fallbackSnapshot = JSON.parse(withoutContextThinking.content[0].text.slice(
    withoutContextThinking.content[0].text.indexOf("{")));
assert.equal(fallbackSnapshot.argv[fallbackSnapshot.argv.indexOf("--thinking") + 1], "high");

const nested = await execute(tool, task({ mode: "nested" }));
const nestedText = nested.content[0].text;
const nestedSnapshot = JSON.parse(nestedText.slice(nestedText.indexOf("{")));
assert.equal(nestedSnapshot.cap, String(4 * 1024 * 1024));
assert.equal(nested.usage.input, 2);
assert.equal(nested.usage.output, 8);
assertNative(nested, times(2));

const sizeMarker = join(scratch, "blob-size");
const returned = await execute(tool, { tasks: Array.from({ length: 60 }, () => task({ mode: "blob", repeats: 6400, sizeMarker })) });
const returnBytes = Buffer.byteLength(JSON.stringify(returned), "utf8");
assert.ok(returnBytes <= RETURN_BUDGET);
assert.ok(returned.content.some(c => c.text.includes("output not retained")));
assert.equal(Number(await readFile(sizeMarker, "utf8")), 50 * 1024);
assert.equal(returned.details.omitted, "[per-helper details omitted: 60]");
assert.equal(returned.usage.input, 60);
assert.equal(returned.usage.cacheRead, 120);
assert.equal(returned.usage.cacheWrite, 180);
assert.equal(returned.usage.output, 240);
assert.equal(returned.usage.cost.total, 7.5);
assertNative(returned, times(60));
const root = new RecordReader(2 * 1024 * 1024);
root.feed(Buffer.from(JSON.stringify({ type: "message_end", message: { role: "toolResult", toolName: "subagent", ...returned } }) + "\n"));
assert.equal(root.diagnostic, null);
assert.equal(root.counts.input, 60);
assert.equal(root.costs.total, 7.5);
assertUsage(root.usage(), times(60));

const start = performance.now();
const waiting = await execute(tool, { tasks: [task({ mode: "delay", ms: 1000 }), task({ mode: "delay", ms: 2000 })] });
const waitMs = performance.now() - start;
assert.ok(waitMs >= 1900);
assert.equal(waiting.details.summaries.length, 2);
const recovered = await execute(tool, { tasks: [task({ mode: "failed" }), task({ mode: "forward" })] });
assert.equal(recovered.isError, true);
assert.equal(recovered.details.summaries[0].status, "failed");
assert.equal(recovered.details.summaries[1].status, "completed");
const recoveredRoot = new RecordReader(2 * 1024 * 1024);
recoveredRoot.feed(Buffer.from(JSON.stringify({ type: "message_end", message: { role: "toolResult", toolName: "subagent", ...recovered } }) + "\n"));
recoveredRoot.feed(Buffer.from(JSON.stringify({ type: "message_end", message: { role: "assistant", stopReason: "stop", content: [{ text: "handled" }] } }) + '\n{"type":"agent_settled"}\n'));
recoveredRoot.feed(Buffer.alloc(0));
assert.equal(recoveredRoot.failure(0), null);
assert.equal(recoveredRoot.completeness(null), "partial");
// Both runs reported complete Usage, but a failed run makes coverage partial: details only.
assertPartial(recovered, times(2));
assertUsage(recoveredRoot.usage(), times(2));
partial.recovered = recovered;

// Missing fields stay absent; known values from complete siblings are retained once.
const missing = await execute(tool, task({ mode: "forward", usage: LEGACY }));
assert.equal(missing.isError, false);
assertPartial(missing, LEGACY);
const mixed = await execute(tool, { tasks: [task({ mode: "forward", usage: LEGACY }), task({ mode: "forward" })] });
assertPartial(mixed, LEGACY_AND_ONE);
const badCost = await execute(tool, task({ mode: "forward", usage: { ...ONE, cost: { ...ONE.cost, cacheWrite: "1" } } }));
assertPartial(badCost, { ...ONE, cost: { input: 0.03125, output: 0.0625, cacheRead: 0.0078125, total: 0.125 } });
const missingRoot = consume(missing);
assertUsage(missingRoot.usage(), LEGACY);
assert.equal(missingRoot.completeness(null), "partial");
partial.missing = missing;
partial.mixed = mixed;

// Nested partial results propagate through details.usage without native Usage or zeros.
const nestedPartial = await execute(tool, task({ mode: "nested", inner: task({ mode: "forward", usage: LEGACY }) }));
assert.equal(nestedPartial.details.summaries[0].status, "completed");
assertPartial(nestedPartial, LEGACY_AND_ONE);
partial.nested = nestedPartial;

const overflowing = await execute(tool, { tasks: [
    task({ mode: "forward", usage: { input: Number.MAX_SAFE_INTEGER, cacheRead: 2, cacheWrite: 3,
        output: 4, cost: { total: 0.125 } } }), task({ mode: "forward" }),
] });
assert.equal(overflowing.isError, true);
assert.equal(overflowing.details.usage_completeness, "partial");
assert.equal(overflowing.details.usage.input, Number.MAX_SAFE_INTEGER);
assert.equal(overflowing.details.usage.cacheRead, 4);
assert.equal(overflowing.details.usage.output, 8);
assert.equal(overflowing.details.usage.cost.total, 0.25);
assertPartial(overflowing, { ...LEGACY_AND_ONE, input: Number.MAX_SAFE_INTEGER });
assert.match(overflowing.content[0].text, /numeric limit/);
// One child's own unrepresentable count is unknown, not wrapped or zero.
const childOverflow = await execute(tool, task({ mode: "forward", usage: { ...ONE, output: Number.MAX_SAFE_INTEGER + 2 } }));
assertPartial(childOverflow, (({ output, ...rest }) => rest)(ONE));

// A synchronous spawn rejection must await siblings already launched and keep their usage.
const closedMarker = join(scratch, "sibling-finished");
const rejectionStart = performance.now();
const rejection = await execute(tool, { tasks: [
    task({ mode: "delay", ms: 300, closedMarker }),
    { ...task({ mode: "forward" }), model: "invalid\0model" },
] });
assert.equal(await readFile(closedMarker, "utf8"), "finished");
assert.ok(performance.now() - rejectionStart >= 290);
assert.equal(rejection.isError, true);
assert.equal(rejection.details.summaries[0].status, "completed");
assert.equal(rejection.details.summaries[1].status, "failed");
assert.equal(rejection.details.summaries[1].usage_completeness, "unknown");
assert.match(rejection.details.summaries[1].error, /launch failed.*null bytes/s);
assert.match(rejection.content[1].text, /^\[2 failed\] Pi child launch failed/);
assertPartial(rejection, ONE);
const rejectionRoot = consume(rejection);
assertUsage(rejectionRoot.usage(), ONE);
assert.equal(rejectionRoot.completeness(null), "partial");
partial.rejection = rejection;
const allFailed = await execute(tool, { tasks: [
    { ...task({ mode: "forward" }), model: "a\0" }, { ...task({ mode: "forward" }), model: "b\0" },
] });
assert.equal(allFailed.isError, true);
assert.deepEqual(allFailed.details.summaries.map(s => s.status), ["failed", "failed"]);
assertPartial(allFailed, { cost: {} }, "unknown");
const allFailedRoot = consume(allFailed);
assert.deepEqual(allFailedRoot.usage(), { cost: {} });
assert.equal(allFailedRoot.completeness(null), "unknown");
partial.all_failed = allFailed;
const nestedRejection = await execute(tool, task({ mode: "nested", inner: { tasks: [
    task({ mode: "forward" }), { ...task({ mode: "forward" }), model: "nested\0" }] } }));
assert.equal(nestedRejection.details.summaries[0].status, "completed");
assertPartial(nestedRejection, times(2));
partial.nested_rejection = nestedRejection;

const aborter = new AbortController();
const marker = join(scratch, "abort-pid");
const aborted = execute(tool, task({ mode: "abort", marker }), aborter.signal);
// Attach a rejection observer before awaiting readiness to avoid unhandled rejections.
const disposition = aborted.then(() => "unexpected-success", error => {
    assert.match(error.message, /aborted/); return "closed";
});
for (let n = 0; n < 200; n++) {
    try { await access(marker); break; } catch { await delay(10); }
}
const pid = Number(await readFile(marker, "utf8"));
aborter.abort();
assert.equal(await disposition, "closed");
assert.throws(() => process.kill(pid, 0), error => error.code === "ESRCH");
const already = new AbortController(); already.abort();
await assert.rejects(execute(tool, task({ mode: "forward" }), already.signal), /aborted/);

console.log(JSON.stringify({ shared_cases: cases.length, chunks: [1, 7, 65536], return_bytes: returnBytes,
    usage_input: returned.usage.input, usage_output: returned.usage.output, wait_ms: waitMs,
    nested_limit: Number(nestedSnapshot.cap), abort_disposition: "closed", platform: process.platform, partial }));
