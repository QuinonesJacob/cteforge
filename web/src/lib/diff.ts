// Unified diff → classified lines with old/new line numbers (display only).

export type DiffLineKind = "add" | "remove" | "context" | "hunk" | "file" | "meta";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

/** Classify one line without hunk context (headers win). */
export function classifyDiffLine(line: string): DiffLineKind {
  if (line.startsWith("+++ ") || line.startsWith("--- ") || line === "+++" || line === "---") return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("\\")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "context";
}

/** Classify a line known to be inside a hunk body: only the first character matters. */
function classifyBodyLine(line: string): DiffLineKind {
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  if (line.startsWith("\\")) return "meta";
  return "context";
}

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

export function parseUnifiedDiff(text: string): DiffLine[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  let oldNo = 0;
  let newNo = 0;
  let oldLeft = 0; // lines of the current hunk still expected on each side
  let newLeft = 0;
  return lines.map((line): DiffLine => {
    const inBody = oldLeft > 0 || newLeft > 0;
    const kind = inBody ? classifyBodyLine(line) : classifyDiffLine(line);
    switch (kind) {
      case "hunk": {
        const m = HUNK.exec(line);
        if (m) {
          oldNo = Number(m[1]);
          oldLeft = m[2] === undefined ? 1 : Number(m[2]);
          newNo = Number(m[3]);
          newLeft = m[4] === undefined ? 1 : Number(m[4]);
        }
        return { kind, text: line, oldNo: null, newNo: null };
      }
      case "add":
        newLeft--;
        return { kind, text: line, oldNo: null, newNo: newNo++ };
      case "remove":
        oldLeft--;
        return { kind, text: line, oldNo: oldNo++, newNo: null };
      case "context":
        if (!inBody) return { kind, text: line, oldNo: null, newNo: null };
        oldLeft--;
        newLeft--;
        return { kind, text: line, oldNo: oldNo++, newNo: newNo++ };
      default:
        return { kind, text: line, oldNo: null, newNo: null };
    }
  });
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "remove").length,
  };
}
