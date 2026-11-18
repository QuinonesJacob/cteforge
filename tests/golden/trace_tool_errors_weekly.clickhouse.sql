/* Tool-call error rate per tool and day for traces of the chosen models (synthetic */
/* agent-trace schema: traces -> turns -> tool_calls). Trusted ClickHouse query used by */
/* cteforge's tests (ADR 0025). */
SELECT
  toMonday(tc.called_at) AS period,
  tc.tool_name AS tool,
  count() AS calls,
  countIf(tc.status = 'error') AS errors,
  round(countIf(tc.status = 'error') / count(), 4) AS error_rate
FROM agent_traces.tool_calls AS tc
INNER JOIN agent_traces.turns AS u
  ON u.turn_id = tc.turn_id
INNER JOIN agent_traces.traces AS t
  ON t.trace_id = u.trace_id
WHERE
  t.started_at >= toDateTime64('2026-01-05', 3, 'UTC')
  AND t.started_at < toDateTime64('2026-01-19', 3, 'UTC')
  AND t.model IN ('claude-haiku-4')
  AND has(t.tags, 'eval')
GROUP BY
  period,
  tool
ORDER BY
  period,
  tool
