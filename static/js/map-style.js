// Dark basemap built on OpenFreeMap vector tiles (OpenMapTiles schema).
// Kept deliberately quiet so the taste glow is the brightest thing on screen.

export const PALETTE = {
  background: "#04060b",
  land: "#0c111c",
  water: "#020307",
  stateLine: "#1a2131",
  countryLine: "#2a3247",
  road: "#111725",
  label: "#465067",
};

export function baseStyle() {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": PALETTE.land } },
      {
        id: "land",
        type: "fill",
        source: "omt",
        "source-layer": "landcover",
        paint: { "fill-color": "#0e1420", "fill-opacity": 0.35 },
      },
      {
        id: "water",
        type: "fill",
        source: "omt",
        "source-layer": "water",
        paint: { "fill-color": PALETTE.water },
      },
      {
        id: "coastline",
        type: "line",
        source: "omt",
        "source-layer": "water",
        paint: { "line-color": "#1b2335", "line-width": 0.8, "line-opacity": 0.8 },
      },
      {
        id: "landuse-urban",
        type: "fill",
        source: "omt",
        "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["residential", "commercial", "industrial"]]],
        minzoom: 8,
        paint: { "fill-color": "#0d1220", "fill-opacity": 0.8 },
      },
      {
        id: "roads",
        type: "line",
        source: "omt",
        "source-layer": "transportation",
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
        minzoom: 5,
        paint: {
          "line-color": PALETTE.road,
          "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 10, 1.4, 14, 3],
        },
      },
      {
        id: "roads-minor",
        type: "line",
        source: "omt",
        "source-layer": "transportation",
        filter: ["in", ["get", "class"], ["literal", ["secondary", "tertiary", "minor"]]],
        minzoom: 10,
        paint: {
          "line-color": PALETTE.road,
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.3, 14, 1.4],
        },
      },
      {
        id: "states",
        type: "line",
        source: "omt",
        "source-layer": "boundary",
        filter: ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
        paint: {
          "line-color": PALETTE.stateLine,
          "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 8, 1.2],
          "line-dasharray": [3, 2],
        },
      },
      {
        id: "countries",
        type: "line",
        source: "omt",
        "source-layer": "boundary",
        filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": PALETTE.countryLine, "line-width": 1 },
      },
      {
        id: "place-labels",
        type: "symbol",
        source: "omt",
        "source-layer": "place",
        filter: ["in", ["get", "class"], ["literal", ["city", "town", "suburb", "neighbourhood"]]],
        minzoom: 9,
        layout: {
          "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
          "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 9, 10, 14, 13],
          "text-transform": "uppercase",
          "text-letter-spacing": 0.12,
        },
        paint: {
          "text-color": PALETTE.label,
          "text-halo-color": PALETTE.background,
          "text-halo-width": 1.2,
        },
      },
    ],
  };
}

// Warm "city lights" ramp: transparent → ember → orange → amber → hot white.
export const HEAT_RAMP = [
  "interpolate", ["linear"], ["heatmap-density"],
  0, "rgba(0,0,0,0)",
  0.12, "rgba(70,18,2,0.35)",
  0.3, "rgba(140,40,4,0.6)",
  0.5, "#d4500e",
  0.7, "#ff8f2e",
  0.86, "#ffc874",
  1, "#fff4dc",
];
