# Context files

Anything that helps the AI generate better tests goes here: release
briefs, internal docs, user-type CSVs, account templates, copies of
emails. Files are parsed once at upload time and indexed into the RAG
store so they show up in every subsequent generation.

## Supported types

| Extension                 | Parser                  | Becomes                                          |
|---------------------------|-------------------------|--------------------------------------------------|
| `.csv`                    | `csv.DictReader`        | One row per `context_file_rows` record.          |
| `.xlsx` / `.xls`          | `pandas` + `openpyxl`   | Same as CSV (first sheet only).                  |
| `.pdf`                    | `pypdf`                 | Token-chunked text -> `embeddings`.              |
| `.docx`                   | `python-docx`           | Same.                                            |
| `.md` / `.markdown` / `.txt` | raw read              | Same.                                            |

Cap: 32 MB / file. Storage path: `{DATA_DIR}/context_files/{file_id}{ext}`.

## Where it shows up

* The **/projects/{name}/integrations** page has a "Context files" tab
  with upload + preview + delete.
* Test-data tables (separate uploads, addressable by name) are a
  parallel surface for structured reference data.
* Persona CSV bulk import is a third surface, intentionally
  metadata-only.

## Sample CSVs

Curated templates ship for the most common shapes:

```text
GET /projects/{slug}/test-data-tables/sample-csv?kind=user_types
GET /projects/{slug}/test-data-tables/sample-csv?kind=accounts
GET /projects/{slug}/test-data-tables/sample-csv?kind=opportunities
GET /projects/{slug}/test-data-tables/sample-csv?kind=leads
GET /personas/sample-csv
```

Each returns a tiny seed file you can fill in and re-upload via the
respective endpoint.

## How the AI sees this

For free-text uploads, every chunk lands in the `embeddings` table
under `source_kind='context_chunk'`. For structured rows, the row's
`searchable_text` (a flattened `key: value | key: value` string) lands
under `source_kind='test_data_row'`. Both kinds are pulled by
`services/rag_retrieval.retrieve` when generating drafts and building
suites.
