/**
 * agent-media plugin for opencode.
 *
 * Tells agent-media what Claude Code's hooks tell it, in the same shape
 * (see agent_media_core/intake/agent_events.py), through
 * `media-hook-opencode`:
 *   chat.message        → UserPromptSubmit: your words, as a "You:" turn
 *   tool.execute.before → PreToolUse: a step in the phone's list
 *   session.status      → a retry (a model busy or out of its limit; opencode
 *                         tries five times over ~70 s) as a step, so the
 *                         phone says why it is waiting
 *   session.idle        → Stop, then the reply is spoken. opencode says the
 *                         session went idle, not what it said, so the hook
 *                         reads the reply back from opencode's database.
 *
 * And it gives the model what Claude Code's UserPromptSubmit hook adds to
 * its context (intake/context.note_for: how to write for the user, the
 * thread's link, related threads, how full the context is, a waiting
 * restart): `media-hook-opencode note` is asked on each prompt and its
 * answer attached as a synthetic text part, which the model reads and no
 * reader shows as the person's words. Mid-turn, as with Claude Code's
 * PostToolUse, a waiting restart is told after a tool call — asked only
 * while one is pending, so a tool call costs a file check otherwise.
 *
 * A subagent's session goes idle too; the hook leaves it unspoken, since its
 * words are its parent's tool result.
 *
 * Install once (media-setup does this):
 *   ln -s ~/projects/agent-media/packages/core/opencode/agent-media.js \
 *         ~/.config/opencode/plugins/agent-media.js
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// opencode's PATH need not include the venv agent-media is installed in.
function hookBin() {
  if (process.env.MEDIA_HOOK_BIN_OPENCODE) return process.env.MEDIA_HOOK_BIN_OPENCODE;
  const venv = join(homedir(), "projects", "agent-media", ".venv", "bin", "media-hook-opencode");
  return existsSync(venv) ? venv : "media-hook-opencode";
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

// A part id that sorts after the prompt's own: opencode orders a message's
// parts by id ("prt_" + time-based hex + random), so this follows its form.
let seq = 0;
function partId() {
  // opencode keeps the low 48 bits of (ms * 4096 + a counter) as 12 hex digits.
  const t = ((BigInt(Date.now()) * 4096n + BigInt(seq++ % 4096)) & 0xffffffffffffn).toString(16).padStart(12, "0");
  let r = "";
  const abc = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
  for (let i = 0; i < 14; i++) r += abc[Math.floor(Math.random() * abc.length)];
  return `prt_${t}${r}`;
}

function event(name, session_id, extra = {}) {
  if (!session_id) return;
  run(["event"], JSON.stringify({ hook_event_name: name, session_id, ...extra }));
}

export const AgentMedia = async ({ directory } = {}) => ({
  "chat.message": async (input, output) => {
    const text = (output?.parts || [])
      .filter((p) => p?.type === "text" && !p.synthetic && p.text)
      .map((p) => p.text)
      .join("\n")
      .trim();
    const cwd = directory || process.cwd();
    if (text) event("UserPromptSubmit", input?.sessionID, { prompt: text, cwd });
    const session = input?.sessionID;
    const message = output?.message?.id || input?.messageID;
    if (!session || !message || !Array.isArray(output?.parts)) return;
    const note = await ask(["note"], JSON.stringify({ session_id: session, prompt: text, cwd }));
    if (note) output.parts.push({ id: partId(), sessionID: session, messageID: message, type: "text", text: note, synthetic: true });
  },

  "tool.execute.after": async (input, output) => {
    if (!input?.sessionID || !restartPending() || typeof output?.output !== "string") return;
    const note = await ask(["note", "--restart"], JSON.stringify({ session_id: input.sessionID }), 5000);
    if (note) output.output += `\n\n${note}`;
  },

  "tool.execute.before": async (input, output) => {
    event("PreToolUse", input?.sessionID, { tool_name: input?.tool || "", tool_input: output?.args || {} });
  },

  event: async ({ event: e }) => {
    const status = e?.type === "session.status" ? e.properties?.status : null;
    if (status?.type === "retry") {
      const why = /limit|rate|429|quota/i.test(status.message || "") ? "Model limit reached" : "Model busy";
      event("PreToolUse", e.properties?.sessionID, {
        tool_name: "Retry",
        tool_input: { description: `${why}, retrying (try ${(status.attempt || 0) + 1})` },
      });
      return;
    }
    if (e?.type !== "session.idle") return;
    const session = e.properties?.sessionID;
    if (!session) return;
    event("Stop", session);
    run(["--session", session]);
  },
});
