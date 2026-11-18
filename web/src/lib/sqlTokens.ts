// Display-only SQL tokenizer for syntax colouring. It never decides anything about SQL;
// the backend owns all SQL logic (web/CLAUDE.md).

export type TokenKind =
  | "keyword"
  | "string"
  | "number"
  | "comment"
  | "placeholder"
  | "identifier"
  | "operator"
  | "text";

export interface Token {
  kind: TokenKind;
  text: string;
}

const KEYWORDS = new Set(
  `select from where and or not in is null as on join left right full inner outer cross group by
  order having limit offset with recursive case when then else end between like ilike distinct union
  all except intersect cast try_cast date timestamp interval over partition rows range qualify true
  false asc desc exists any some using nulls first last filter window preceding following current
  row unbounded insert update delete into values set create table view lateral unnest extract
  natural semi anti pivot unpivot within group_concat at time zone fetch next only`
    .split(/\s+/)
    .filter(Boolean),
);

// Order matters: comments, strings, placeholders, quoted identifiers, numbers, words, ops.
const RULES: [TokenKind, RegExp][] = [
  ["comment", /--[^\n]*/y],
  ["comment", /\/\*[\s\S]*?(?:\*\/|$)/y],
  ["string", /'(?:[^']|'')*(?:'|$)/y],
  ["string", /\$\$[\s\S]*?(?:\$\$|$)/y],
  ["placeholder", /\$[A-Za-z_][A-Za-z0-9_]*/y],
  ["placeholder", /(?<!:):[A-Za-z_][A-Za-z0-9_]*/y],
  ["placeholder", /@[A-Za-z_][A-Za-z0-9_]*/y],
  ["placeholder", /%\([A-Za-z_][A-Za-z0-9_]*\)s/y],
  ["placeholder", /\{\{[\s\S]*?\}\}/y],
  ["identifier", /"(?:[^"]|"")*(?:"|$)/y],
  ["identifier", /`[^`]*(?:`|$)/y],
  ["number", /(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\b/y],
  ["text", /[A-Za-z_][A-Za-z0-9_$]*/y],
  ["operator", /::|<=|>=|<>|!=|\|\||[-+*/%=<>(),.;[\]]/y],
  ["text", /\s+/y],
  ["text", /./y],
];

export function tokenize(sql: string): Token[] {
  const tokens: Token[] = [];
  let pos = 0;
  while (pos < sql.length) {
    let matched = false;
    for (const [kind, re] of RULES) {
      re.lastIndex = pos;
      const m = re.exec(sql);
      if (!m || m[0].length === 0) continue;
      let k: TokenKind = kind;
      if (kind === "text" && /^[A-Za-z_]/.test(m[0]) && KEYWORDS.has(m[0].toLowerCase())) k = "keyword";
      const prev = tokens[tokens.length - 1];
      if (prev && prev.kind === k && k === "text") prev.text += m[0];
      else tokens.push({ kind: k, text: m[0] });
      pos += m[0].length;
      matched = true;
      break;
    }
    if (!matched) {
      tokens.push({ kind: "text", text: sql.charAt(pos) });
      pos += 1;
    }
  }
  return tokens;
}

/** Tokens split into lines (multi-line tokens are split at newlines) for line numbering. */
export function tokenizeLines(sql: string): Token[][] {
  const lines: Token[][] = [[]];
  for (const tok of tokenize(sql.replace(/\r\n/g, "\n"))) {
    const parts = tok.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1]!.push({ kind: tok.kind, text: part });
    });
  }
  return lines;
}

/** How a value token is shown on Import (ADR 0029): a variable, a selected value, or a value
 * left fixed (underlined). Colouring only: matched by text, so equal values look alike. */
export type ValueMark = "variable" | "selected" | "value";

export interface MarkedToken extends Token {
  mark?: ValueMark;
}

/** Strip one pair of SQL quotes: "'a''b'" → "a'b". */
const unquote = (s: string) => (s.startsWith("'") && s.endsWith("'") && s.length >= 2 ? s.slice(1, -1).replace(/''/g, "'") : s);

/**
 * Marks string and number tokens whose text is the value of a parsed literal: `variable`
 * when a variable uses a literal with that value, `selected` when one is selected, else
 * `value`. Display only — the import is built from literal IDs, never from this.
 */
export function markValues(
  lines: Token[][],
  literals: { id: string; value: string; is_string: boolean }[],
  variableIds: Set<string>,
  selectedIds: Set<string>,
): MarkedToken[][] {
  const marks = new Map<string, ValueMark>();
  const rank: Record<ValueMark, number> = { value: 0, selected: 1, variable: 2 };
  for (const l of literals) {
    const key = `${l.is_string ? "s" : "n"}:${l.value}`;
    const mark: ValueMark = variableIds.has(l.id) ? "variable" : selectedIds.has(l.id) ? "selected" : "value";
    const prev = marks.get(key);
    if (!prev || rank[mark] > rank[prev]) marks.set(key, mark);
  }
  return lines.map((line) =>
    line.map((t) => {
      if (t.kind === "string") return { ...t, mark: marks.get(`s:${unquote(t.text)}`) };
      if (t.kind === "number") return { ...t, mark: marks.get(`n:${t.text}`) };
      return t;
    }),
  );
}
