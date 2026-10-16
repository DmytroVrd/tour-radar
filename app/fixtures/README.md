# Qloo fixtures (development only)

These files back `QLOO_MOCK=1`. They are **not** Qloo data and must never be
shown as real in the hosted demo. Every mock response carries `"_source": "mock"`.

- `heatmap_nyc_docs.json`: the heatmap example from
  https://docs.qloo.com/reference/heatmaps-use-case, copied verbatim (the docs
  truncate the array). Tests check that generated mock heatmaps match its shape.
- `search.json`: `/search` responses for the example artists, following the
  documented schema (`success`, `duration`, `results[]` of entity objects).
- `taste_profiles.json`: hand-tuned city weights the mock source uses to make
  heatmaps look plausible during development.

Fields inside entity objects (`properties`, `tags`, `query`) follow the API
schema but must be re-checked against real responses once the key arrives.
