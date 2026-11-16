import { formatValue } from "../lib/format";

interface Props {
  columns: string[];
  rows: unknown[][];
  caption?: string;
}

export function ResultsTable({ columns, rows, caption = "Query results" }: Props) {
  return (
    <div className="table-wrap results" tabIndex={0} role="region" aria-label={caption}>
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="rownum">
              #
            </th>
            {columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td className="rownum">{i + 1}</td>
              {row.map((cell, j) => (
                <td key={j} className={cell === null ? "null" : typeof cell === "number" ? "num" : undefined}>
                  {formatValue(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
