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

// ── Views (ADR 0029: unified or side by side, changes only or full) ──────────────────────

/** A row of a diff view: a body line, or a gap of unchanged lines left out. */
export type ViewLine = (DiffLine & { kind: "add" | "remove" | "context" }) | { kind: "gap"; count: number };

const isBody = (l: DiffLine): l is DiffLine & { kind: "add" | "remove" | "context" } =>
  (l.kind === "add" || l.kind === "remove" || l.kind === "context") && (l.oldNo !== null || l.newNo !== null);

/** Strip the marker (+, -, space) of a body line. */
export const lineText = (l: DiffLine): string => (l.kind === "add" || l.kind === "remove" || l.kind === "context" ? l.text.slice(1) : l.text);

/** Every body line (file headers and hunk headers dropped): the whole file when the diff has
 * full context, else what the diff carries, with gaps between hunks. */
export function fullView(lines: DiffLine[]): ViewLine[] {
  const out: ViewLine[] = [];
  let lastOld = 0;
  for (const l of lines) {
    if (!isBody(l)) continue;
    const old = l.oldNo;
    if (old !== null && old > lastOld + 1) out.push({ kind: "gap", count: old - lastOld - 1 });
    if (old !== null) lastOld = old;
    out.push(l);
  }
  return out;
}

/** Changes and `context` unchanged lines around each; the rest collapse into gaps. */
export function changesOnly(lines: DiffLine[], context = 3): ViewLine[] {
  const body = fullView(lines);
  const keep = body.map(() => false);
  body.forEach((l, i) => {
    if (l.kind !== "add" && l.kind !== "remove") return;
    for (let j = Math.max(0, i - context); j <= Math.min(body.length - 1, i + context); j++) keep[j] = true;
  });
  const out: ViewLine[] = [];
  let skipped = 0;
  body.forEach((l, i) => {
    if (keep[i] && l.kind !== "gap") {
      if (skipped) out.push({ kind: "gap", count: skipped });
      skipped = 0;
      out.push(l);
    } else {
      skipped += l.kind === "gap" ? l.count : 1;
    }
  });
  if (skipped) out.push({ kind: "gap", count: skipped });
  return out;
}

/** One side-by-side row: old line on the left, new on the right (either may be empty). */
export type SplitRow = { kind: "gap"; count: number } | { kind: "pair"; left: DiffLine | null; right: DiffLine | null };

/** Side by side: context on both sides; a run of removals pairs with the additions after it. */
export function splitRows(view: ViewLine[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let i = 0;
  while (i < view.length) {
    const l = view[i]!;
    if (l.kind === "gap") {
      rows.push(l);
      i++;
    } else if (l.kind === "context") {
      rows.push({ kind: "pair", left: l, right: l });
      i++;
    } else {
      const removed: DiffLine[] = [];
      const added: DiffLine[] = [];
      while (i < view.length && view[i]!.kind === "remove") removed.push(view[i++] as DiffLine);
      while (i < view.length && view[i]!.kind === "add") added.push(view[i++] as DiffLine);
      for (let k = 0; k < Math.max(removed.length, added.length); k++)
        rows.push({ kind: "pair", left: removed[k] ?? null, right: added[k] ?? null });
    }
  }
  return rows;
}
