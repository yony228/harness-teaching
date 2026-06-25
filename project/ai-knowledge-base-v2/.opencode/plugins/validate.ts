import { $ } from "bun";

const KNOWSYS_REGEX = /knowledge\/articles\/.*\.json$/;

export default function validateJsonPlugin(): object {
  return {
    setup({ on }: { on: (event: string, handler: (input: Record<string, unknown>) => void) => void }) {
      on("tool.execute.after", (input: Record<string, unknown>) => {
        // Only react to write/edit tools
        const toolName = input.tool;
        if (toolName !== "write" && toolName !== "edit") {
          return;
        }

        // Determine the file path from tool arguments (both camelCase and snake_case variants)
        const args = input.args as Record<string, unknown>;
        const filePath =
          (typeof args.file_path === "string" && args.file_path) ||
          (typeof args.filePath === "string" && args.filePath);

        if (!filePath) {
          return;
        }

        // Only validate files matching knowledge/articles/*.json
        if (!KNOWSYS_REGEX.test(filePath)) {
          return;
        }

        // Execute validation via Bun shell
        // Must use .nothrow() (never .quiet() — it causes OpenCode to freeze)
        // Must use try/catch (uncaught exceptions block the Agent)
        void (async () => {
          try {
            const result = await $`python3 hooks/validate_json.py ${filePath}`.nothrow();
            const exitCode = result.exitCode ?? 0;
            if (exitCode !== 0) {
              const stderr = result.stderr?.toString?.() ?? "";
              const stdout = result.stdout?.toString?.() ?? "";
              console.error(
                `[validate-json] "${filePath}" 校验失败 (exit=${exitCode}):`,
                stderr || stdout,
              );
            } else {
              console.log(
                `[validate-json] "${filePath}" 校验通过 ✓`,
              );
            }
          } catch (err) {
            // Swallow shell invocation errors so they don't block the Agent
            console.error(
              `[validate-json] 执行 python3 hooks/validate_json.py 时发生错误:`,
              err,
            );
          }
        })();
      });
    },
  };
}
