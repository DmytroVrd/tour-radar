# Qloo fixtures (development and tests only)

These files back `QLOO_MOCK=1`. They are **not** Qloo data (except the tiny
real-shape sample below) and are never shown as real in the hosted demo. Every
mock response carries `"_source": "mock"`.

- `heatmap_portland_real.json`: the first three cells and the `query` block of
  a real heatmap response (artist signal, Portland). Tests check that mock
  heatmaps have exactly this shape. Note that `query.localities.filter` is a
  list and locality `location` is `{lat, lon}`, unlike the docs example.
- `search.json`: `/search` responses for the example artists.
- `taste_profiles.json`: hand-tuned city weights the mock source uses to make
  heatmaps look plausible during development.
