// Legend (ARCHITECTURE §10; review V4, V10; design §6): two rows in the time dock's legend strip, rendered once into
// #legend. Colour is the regime (list glyphs, help); shape is the object type; hollow means the position is not from a
// current ephemeris (extrapolated or stale); derelicts are dimmed (the same dot at full and reduced alpha). Entries keyed
// by data-legend follow their layer (tracklets: beads on their hairline; sight line; GEO · Moon distance rings). Long
// labels have a short form (.l / .s) the ≤ 1280 px layout swaps in so the strip keeps two rows at 1101 px (the hollow
// marker's is "stale", so it does not read as a second "extrap" next to the dotted extrapolated line).
const ls = (long, short) => `<span class="l">${long}</span><span class="s">${short}</span>`;
const ROWS = [
  `<span><b>●</b>payload</span><span><b>■</b>${ls('rocket body', 'R/B')}</span><span><b>◆</b>debris</span>`
    + `<span><b>○</b>${ls('extrap / stale', 'stale')}</span><span><b>●<i class="dimb">●</i></b>derelict</span><span><i class="lg-lp" aria-hidden="true"></i>Lagrange</span>`,
  '<span><i class="lg-line"></i>past</span><span><i class="lg-line dash"></i>future</span><span><i class="lg-line dot"></i>extrap</span><span><i class="lg-line dim"></i>GP</span>'
    + `<span><i class="lg-line sun"></i>${ls('Sun 0° terminator', 'Sun 0°')}</span>`
    + '<span data-legend="tracklets"><i class="lg-trk" aria-hidden="true"></i>tracklets</span><span data-legend="sightlines"><i class="lg-line sight"></i>sight line</span>'
    + `<span data-legend="rings"><i class="lg-ring" aria-hidden="true"></i>${ls('GEO · Moon dist.', 'GEO·LD')}</span>`,
];

export function renderLegend(el) {
  el.innerHTML = ROWS.map(html => `<div class="row">${html}</div>`).join('');
}

// Show or hide the keyed entries with their layers.
export function legendUI(el, layers) {
  for (const e of el.querySelectorAll('[data-legend]')) e.hidden = !layers[e.dataset.legend];
}
