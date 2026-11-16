# cteforge

**Paste the SQL you already trust. No semantic model required.**

cteforge turns a team's trusted SQL into a library that AI agents can search, reuse,
change safely, and build on. Trusted queries are kept as written; only the parts that
change (date ranges, regions, …) are described as **typed variables**. Agents pick the
values. cteforge writes the SQL, so verified logic never passes back through the LLM.

> Status: pre-alpha (milestone M1). See [docs/DESIGN.md](docs/DESIGN.md) for the full
> design and [docs/decisions/](docs/decisions/) for why it's built this way.

## Try it (M1)

Requires [uv](https://docs.astral.sh/uv/) (or install with pip: `pip install -e .`).

```bash
uv sync
uv run python examples/seed_warehouse.py          # synthetic DuckDB warehouse

# 1. See which literals in a trusted query could become variables
uv run cteforge parse examples/queries/realization_rate.sql -w examples/warehouse.duckdb

# 2. Annotate them (examples/definitions/realization_rate.annotations.yml) and import.
#    cteforge proves the template reproduces the original query and its results.
uv run cteforge import examples/queries/realization_rate.sql \
  examples/definitions/realization_rate.annotations.yml \
  -w examples/warehouse.duckdb -o examples/definitions/realization_rate.yml

# 3. Render with new values — for any warehouse — and run it
uv run cteforge render examples/definitions/realization_rate.yml \
  --vars '{"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}' \
  -w examples/warehouse.duckdb --run
uv run cteforge render examples/definitions/realization_rate.yml -d bigquery
```

## How it works

| The LLM | cteforge |
|---|---|
| Understands the question, picks a definition | Stores trusted SQL as a template with typed holes |
| Chooses values: `{"start": "2026-07-01", "end": "2026-10-01"}` | Validates them strictly (`"2026-Q3"` is rejected with the expected format) |
| | Writes dialect-correct SQL from AST nodes (no string splicing) |
| | Proves on import that the template reproduces the original query |

## Development

```bash
uv run pytest          # tests
uv run lint-imports    # module layering contracts
uv run ruff check .    # lint
```

The codebase is split into small modules with strict import rules; each has a
`CLAUDE.md` describing what belongs there. Start with [CLAUDE.md](CLAUDE.md).

## License

Apache-2.0
