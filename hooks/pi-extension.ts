import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { spawn } from "node:child_process";
import { homedir } from "node:os";

interface HookOutput {
  hookSpecificOutput?: {
    additionalContext?: string;
  };
  additionalContext?: string;
}

type HookInput = Record<string, unknown> & {
  cwd: string;
  hook_event_name: string;
};

async function runHook(hookPath: string, hookInput: HookInput, cwd: string): Promise<string> {
  return new Promise((resolve) => {
    const child = spawn("python3", [hookPath], {
      cwd,
      env: { ...process.env, HOME: homedir() },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, 10000);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      console.error(`Fritz hook error (${hookPath}):`, err.message);
      resolve("");
    });

    child.on("close", (code, signal) => {
      clearTimeout(timer);

      if (timedOut) {
        console.error(`Fritz hook error (${hookPath}): timed out after 10s`);
        resolve("");
        return;
      }

      if (code !== 0) {
        const detail = stderr.trim() || `exit code ${code}${signal ? ` (${signal})` : ""}`;
        console.error(`Fritz hook error (${hookPath}):`, detail);
        resolve("");
        return;
      }

      const trimmed = stdout.trim();
      if (!trimmed) {
        resolve("");
        return;
      }

      try {
        const output: HookOutput = JSON.parse(trimmed);
        resolve(output.hookSpecificOutput?.additionalContext || output.additionalContext || "");
      } catch (err: any) {
        console.error(`Fritz hook error (${hookPath}): invalid JSON output:`, err.message);
        resolve("");
      }
    });

    child.stdin.end(JSON.stringify(hookInput));
  });
}

export default function (pi: ExtensionAPI): void {
  pi.on("session_start", async (_event, ctx) => {
    const cwd = ctx.cwd;
    const brainContext = await runHook(
      `${homedir()}/.brain/hooks/brain_session_start.py`,
      { cwd, hook_event_name: "SessionStart" },
      cwd,
    );

    if (brainContext) {
      pi.sendMessage({
        customType: "fritz-brain-context",
        content: brainContext,
        display: false,
      }, { deliverAs: "nextTurn" });
    }
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const promptCheck = await runHook(
      `${homedir()}/.brain/hooks/brain_prompt_check.py`,
      {
        cwd: ctx.cwd,
        hook_event_name: "UserPromptSubmit",
        user_prompt: event.prompt,
        message: { content: event.prompt },
      },
      ctx.cwd,
    );

    if (promptCheck) {
      return {
        message: {
          customType: "fritz-brain-prompt-check",
          content: promptCheck,
          display: false,
        },
      };
    }
  });

  pi.on("session_before_compact", async (_event, ctx) => {
    const transcriptPath = ctx.sessionManager.getSessionFile();
    if (!transcriptPath) return;

    await runHook(
      `${homedir()}/.brain/hooks/brain_capture.py`,
      {
        cwd: ctx.cwd,
        hook_event_name: "PiSessionBeforeCompact",
        transcript_path: transcriptPath,
      },
      ctx.cwd,
    );
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    const transcriptPath = ctx.sessionManager.getSessionFile();
    if (!transcriptPath) return;

    await runHook(
      `${homedir()}/.brain/hooks/brain_capture.py`,
      {
        cwd: ctx.cwd,
        hook_event_name: "PiSessionShutdown",
        transcript_path: transcriptPath,
      },
      ctx.cwd,
    );
  });
}
