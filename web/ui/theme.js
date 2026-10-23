// Colour tokens for JS consumers (WebGL materials, chips): web/theme.css is the source; token() reads the inlined custom
// property at call time and falls back to the same value here (tests/palette.test.mjs keeps the two equal), so node tests
// and scene code agree with the page.
const FALLBACK = {
  '--surface': '#04060c', '--bg': '#101418', '--panel': '#171c22', '--raised': '#1e2630', '--ink': '#e8eaed', '--dim': '#a9b3bf',
  '--muted': '#818e9e', '--line': '#303b47', '--accent': '#3987e5', '--accent-soft': '#a1d4fc', '--focus': '#b3d9ff', '--moon': '#c1c7ce',
  '--sun': '#ffd54f', '--cyan': '#4fc3f7', '--warn': '#fcc598', '--warn-bg': '#2a1f18', '--warn-line': '#6e5241', '--warn-line-soft': '#4a3829',
  '--live': '#a1d4fc', '--lagrange': '#ffe9a6', '--hairline': '#e8eaed', '--sightline': '#a1d4fc', '--star': '#d6d6d6',
  '--chip-on': '#213044', '--row-sel': '#1d2a3a', '--sel-ink': '#ffffff', '--q-gp': '#b0bec5', '--q-fit': '#a5d6a7', '--natural': '#cfd8dc',
  '--constellation': '#5a7bb8', '--bead': '#f48fb1',
};
export const TOKENS = FALLBACK;

export function token(name) {
  const v = typeof document !== 'undefined' && getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || FALLBACK[name];
}

// NEOCP tracklet beads, by station in first-seen order (web/scene/overlays.js and the readout's station chips share this
// cycle; 57 stations map onto 6 colours, so the colour is a grouping hint and the chip carries the code). Every entry is
// ≥ 8 ΔE from the (ink) hairline (review V10; tests/palette.test.mjs).
export const STATION_COLORS = [FALLBACK['--bead'], '#80deea', '#ef9a9a', '#a5d6a7', '#ce93d8', '#64b5f6'];
