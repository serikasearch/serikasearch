"""Reference objects across every scale, for the Scale of the Universe widget.

Each entry is ``(log10 of size in metres, name, emoji, blurb)``. The slider
spans from the Planck length (~10⁻³⁵ m) to the observable universe (~10²⁷ m),
and at any point on it the widget shows the nearest object and its neighbours.

Sizes are order-of-magnitude reference values — the point is the comparison
between scales, not four significant figures.
"""

from __future__ import annotations

# Sorted small → large. log10 metres.
OBJECTS: list[tuple[float, str, str, str]] = [
    (-35.0, "Planck length", "•", "The smallest length that has meaning in physics — below it, space itself stops making sense."),
    (-18.0, "Quark", "⚛", "A fundamental particle. Protons and neutrons are each built from three of them."),
    (-15.0, "Proton", "🔴", "The positively charged core of a hydrogen atom; about a femtometre across."),
    (-14.0, "Atomic nucleus", "⚛", "The dense centre of an atom, holding almost all of its mass."),
    (-10.0, "Hydrogen atom", "🔵", "One proton and one electron. An ångström wide — the basic unit of chemistry."),
    (-9.0, "DNA helix (width)", "🧬", "The double helix is about two nanometres across."),
    (-8.0, "Cell membrane", "🧫", "The lipid wall around a living cell, a few nanometres thick."),
    (-7.0, "Virus", "🦠", "A typical virus, around 100 nanometres — too small for visible light to resolve."),
    (-6.0, "Bacterium", "🦠", "A micrometre-scale single-celled organism, like E. coli."),
    (-5.0, "Human cell", "🔬", "A red blood cell is about 8 micrometres wide."),
    (-4.0, "Human hair (width)", "〰", "Roughly 100 micrometres across — near the limit of the naked eye."),
    (-3.0, "Grain of sand", "🟤", "About a millimetre; the smallest thing you can easily pick up."),
    (-2.0, "Bumblebee", "🐝", "A couple of centimetres — everyday, tangible scale."),
    (-1.0, "Human hand", "✋", "Around 10 to 20 centimetres."),
    (0.0, "Human", "🧍", "A person is between one and two metres tall — the reference scale for our intuition."),
    (1.0, "Blue whale", "🐋", "The largest animal ever to have lived, up to 30 metres long."),
    (2.0, "Football pitch", "🏟", "About 100 metres end to end."),
    (2.5, "Great Pyramid", "🔺", "146 metres tall when built — the tallest structure on Earth for millennia."),
    (2.9, "Burj Khalifa", "🏙", "The tallest building, 828 metres."),
    (3.5, "Mount Everest", "🏔", "Nearly 9 kilometres from base to summit."),
    (4.5, "Marathon", "🏃", "42 kilometres — a city-sized distance."),
    (5.8, "Moon (diameter)", "🌕", "3,474 km across — about a quarter of Earth's width."),
    (6.8, "Earth (diameter)", "🌍", "12,742 km. Everything you have ever known fits on it."),
    (7.9, "Jupiter (diameter)", "🪐", "Eleven Earths across; the largest planet."),
    (9.1, "Sun (diameter)", "☀", "1.39 million km — you could line up 109 Earths across its face."),
    (11.0, "Earth's orbit", "🌐", "The Earth circles the Sun at about 150 million km — one astronomical unit."),
    (13.0, "Solar System (planets)", "🪐", "Out to Neptune, the planetary system spans tens of astronomical units."),
    (16.0, "One light-year", "✨", "The distance light travels in a year — about 9.5 trillion km."),
    (16.3, "Nearest star", "⭐", "Proxima Centauri is 4.2 light-years away."),
    (18.2, "Orion Nebula", "🌌", "A stellar nursery about 24 light-years across."),
    (21.0, "Milky Way (diameter)", "🌌", "Our galaxy spans roughly 100,000 light-years and holds hundreds of billions of stars."),
    (22.4, "Local Group", "🌌", "The cluster of galaxies the Milky Way belongs to, about 10 million light-years across."),
    (24.0, "Galaxy supercluster", "🌌", "Laniakea, our home supercluster, is around 500 million light-years wide."),
    (25.4, "Cosmic web", "🕸", "On the largest scales, galaxies string together into filaments and voids."),
    (26.9, "Observable universe", "🌠", "About 93 billion light-years across — everything light has had time to reach us from."),
]

MIN_LOG = -35.0
MAX_LOG = 27.0


def object_data() -> list[dict]:
    """A JSON-serialisable form for embedding in the widget."""
    return [
        {"log": log, "name": name, "emoji": emoji, "blurb": blurb}
        for log, name, emoji, blurb in OBJECTS
    ]
