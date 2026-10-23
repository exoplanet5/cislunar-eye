// Scene legend (ARCHITECTURE §10; review V4, V10): two rows rendered once into #legend; entries keyed by data-legend
// follow their layer or chip (▲ natural ↔ chip `nat`; Sun −12° ↔ layer `twilight`; GEO · Moon distance ↔ `rings`;
// tracklets (beads on their hairline) ↔ `tracklets`; sight line ↔ `sightlines`). Colour is the regime (list swatches,
// help); shape is the object type; hollow means the position is not from a current ephemeris (extrapolated or stale);
// derelicts are dimmed (the same dot at full and reduced alpha).
const ROWS = [
  '<span><b>●</b>payload</span><span><b>■</b>rocket body</span><span><b>◆</b>debris</span><span data-legend="nat"><b>▲</b>natural</span>'
    + '<span><b>○</b>extrap / stale</span><span><b>●<i class="dimb">●</i></b>derelict</span><span><i class="lg-lp" aria-hidden="true"></i>Lagrange</span>',
  '<span><i class="lg-line"></i>past</span><span><i class="lg-line dash"></i>future</span><span><i class="lg-line dot"></i>extrap</span><span><i class="lg-line dim"></i>GP</span>'
    + '<span><i class="lg-line sun"></i>terminator</span><span data-legend="twilight"><i class="lg-line cyan"></i>Sun −12°</span><span data-legend="rings"><i class="lg-ring" aria-hidden="true"></i>GEO · Moon distance</span>'
    + '<span data-legend="tracklets"><i class="lg-trk" aria-hidden="true"></i>tracklets</span><span data-legend="sightlines"><i class="lg-line sight"></i>sight line</span>',
];

export function renderLegend(el) {
  el.innerHTML = ROWS.map(html => `<div class="row">${html}</div>`).join('');
}

// Show or hide the keyed entries; a key names a layer when the layers object has it, else a chip.
export function legendUI(el, layers, chips) {
  for (const e of el.querySelectorAll('[data-legend]')) { const k = e.dataset.legend; e.hidden = !(k in layers ? layers[k] : chips[k]); }
}
