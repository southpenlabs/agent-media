/**
 * agent-media plugin for opencode 2 (opencode 1's is ../../agent-media.js;
 * v1 plugins do not run on v2, docs/proposals/opencode-v2.md).
 *
 * The same jobs, through `media-hook-opencode`, in v2's plugin API (shapes
 * as opencode 2.3.0 gave them when probed, 16 Nov 2026):
 *   session "prompt" hook   → UserPromptSubmit (your words, a "You:" turn),
 *                             and the note for the model is fetched
 *                             (`media-hook-opencode note`: how to write for
 *                             the user, the thread's link, related threads,
 *                             how full the context is, a waiting restart)
 *   session "context" hook  → that note added to the system prompt of each
 *                             model request of the turn; while a restart is
 *                             waiting, the restart note too (v1 appended it
 *                             to a tool's output; this is the request after)
 *   tool "execute.before"   → PreToolUse: a step in the phone's list
 *   session.retry.scheduled → a retry as a step, so the phone says why it waits
 *   session.execution.*     → Stop, then the reply is spoken (the hook reads
 *                             it back over opencode's API)
 *
 * opencode 2 runs one background server for every client, and loads a copy
 * of this plugin per folder it serves; each copy hears every event, so an
 * event is handled once per process (its id, kept in `seen`). Which chat is
 * the phone's is the hook's to tell (sessiond's record of the session):
 * the server's environment is whoever started it.
 *
 * Installed as a folder (media-setup does this):
 *   ~/.config/opencode/plugins/agent-media/ → this folder
 */

import { Plugin } from "@opencode/plugin";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// The installed release's hook first: its venv has the voice engines (Kokoro
// is an add-on there, not in a source checkout's .venv, where every reply
// failed to render and so was never filed: no Play buttons, 17 Nov 2026).
function hookBin() {
  if (process.env.MEDIA_HOOK_BIN_OPENCODE) return process.env.MEDIA_HOOK_BIN_OPENCODE;
  const data = process.env.XDG_DATA_HOME || join(homedir(), ".local", "share");
  for (const venv of [
    join(data, "agent-media", "current", ".venv", "bin", "media-hook-opencode"),
    join(homedir(), "projects", "agent-media", ".venv", "bin", "media-hook-opencode"),
  ]) {
    if (existsSync(venv)) return venv;
  }
  return "media-hook-opencode";
}

function run(args, input = "") {
  if (process.env.MEDIA_HOOK_ENABLED === "0") return;
  try {
    const child = spawn(hookBin(), args, { stdio: ["pipe", "ignore", "ignore"], detached: true });
    child.on("error", () => {});
    child.stdin.on("error", () => {});
    child.stdin.end(input);
    child.unref();
  } catch {
    // Non-fatal: agent-media is best-effort.
  }
}

// Run the hook and wait for what it prints, at most `ms`; "" on anything
// unexpected: a prompt is never held up or broken by this.
function ask(args, input, ms = 10000) {
  if (process.env.MEDIA_HOOK_ENABLED === "0") return Promise.resolve("");
  return new Promise((resolve) => {
    let out = "";
    let done = false;
    const finish = (v) => {
      if (!done) {
        done = true;
        resolve(v);
      }
    };
    try {
      const child = spawn(hookBin(), args, { stdio: ["pipe", "pipe", "ignore"] });
      const timer = setTimeout(() => {
        try {
          child.kill();
        } catch {}
        finish("");
      }, ms);
      child.on("error", () => finish(""));
      child.stdin.on("error", () => {});
      child.stdout.on("data", (d) => (out += d));
      child.on("close", () => {
        clearTimeout(timer);
        finish(out.trim());
      });
      child.stdin.end(input);
    } catch {
      finish("");
    }
  });
}

function restartPending() {
  const state = process.env.XDG_STATE_HOME || join(homedir(), ".local", "state");
  return existsSync(join(state, "agent-media", "restart-pending.json"));
}

function event(name, session_id, extra = {}) {
  if (!session_id) return;
  run(["event"], JSON.stringify({ hook_event_name: name, session_id, ...extra }));
}

// v2's tool names as v1's, which the phone's step list knows.
const V1_TOOL = { shell: "bash", subagent: "task" };

// Shared by every copy of the plugin in this server process.
const shared = (globalThis.__agentMediaOpencode ||= { seen: new Set(), notes: new Map() });

function once(id) {
  if (!id) return true;
  if (shared.seen.has(id)) return false;
  shared.seen.add(id);
  if (shared.seen.size > 5000) shared.seen = new Set([...shared.seen].slice(-2500));
  return true;
}

export default Plugin.define({
  id: "agent-media",
  async setup(ctx) {
    const cwd = ctx.location?.directory || process.cwd();

    await ctx.session.hook("prompt", async (ev) => {
      const session = ev.sessionID;
      const text = String(ev.prompt?.text || "").trim();
      if (!session) return;
      if (text) event("UserPromptSubmit", session, { prompt: text, cwd });
      const note = await ask(["note"], JSON.stringify({ session_id: session, prompt: text, cwd }));
      if (note) shared.notes.set(session, note);
      else shared.notes.delete(session);
    });

    await ctx.session.hook("context", async (ev) => {
      const session = ev.sessionID;
      if (!session || !Array.isArray(ev.system)) return;
      const note = shared.notes.get(session);
      if (note) ev.system.push({ type: "text", text: note });
      if (restartPending()) {
        const restart = await ask(["note", "--restart"], JSON.stringify({ session_id: session }), 5000);
        if (restart) ev.system.push({ type: "text", text: restart });
      }
    });

    await ctx.tool.hook("execute.before", (ev) => {
      const tool = String(ev.tool || "");
      event("PreToolUse", ev.sessionID, { tool_name: V1_TOOL[tool] || tool, tool_input: ev.input || {} });
    });

    const ac = new AbortController();
    (async () => {
      for await (const e of ctx.event.subscribe({
        signal: ac.signal,
        types: ["session.retry.scheduled", "session.execution.succeeded",
                "session.execution.failed", "session.execution.interrupted"],
      })) {
        const session = e?.data?.sessionID;
        if (!session || !once(e.id)) continue;
        if (e.type === "session.retry.scheduled") {
          const msg = String(e.data?.error?.message || "");
          const why = /limit|rate|429|quota/i.test(msg) ? "Model limit reached" : "Model busy";
          event("PreToolUse", session, {
            tool_name: "Retry",
            tool_input: { description: `${why}, retrying (try ${(e.data?.attempt || 0) + 1})` },
          });
          continue;
        }
        shared.notes.delete(session);
        event("Stop", session);
        run(["--session", session]);
      }
    })().catch(() => {});
    return () => ac.abort();
  },
});
