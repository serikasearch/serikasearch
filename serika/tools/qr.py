"""A dependency-free QR code encoder (byte mode, error-correction level M).

SerikaSearch ships no third-party packages, so the QR tool brings its own
encoder rather than pulling in a library: Reed–Solomon over GF(256), the
standard block interleaving, all eight data masks scored by the penalty rules
from ISO/IEC 18004, and an SVG renderer. Versions 1–10 are supported, which
covers 213 bytes — plenty for URLs, Wi-Fi credentials and vCards.

The output is a plain ``<svg>`` string with no external references, so it can
be inlined straight into a result page.
"""

from __future__ import annotations

__all__ = ["QRError", "encode", "to_svg", "make_svg"]


class QRError(Exception):
    """Raised when the payload is empty or too long to encode."""


# --------------------------------------------------------------------------- #
# GF(256) arithmetic — the field Reed–Solomon operates in
# --------------------------------------------------------------------------- #

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:          # reduce by the QR primitive polynomial 0x11d
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    """The generator polynomial (x-α⁰)(x-α¹)…(x-α^(degree-1)).

    Coefficients are stored highest-power-first, so ``poly[0]`` is always the
    leading 1 that :func:`_rs_remainder` divides by.
    """
    poly = [1]
    for i in range(degree):
        product = [0] * (len(poly) + 1)
        for j, coefficient in enumerate(poly):
            product[j] ^= coefficient                          # × x
            product[j + 1] ^= _gf_mul(coefficient, _EXP[i])    # × α^i
        poly = product
    return poly


def _rs_remainder(data: list[int], ec_count: int) -> list[int]:
    """Reed–Solomon error-correction codewords for one block."""
    generator = _rs_generator(ec_count)
    remainder = [0] * ec_count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(ec_count):
            remainder[i] ^= _gf_mul(generator[i + 1], factor)
    return remainder


# --------------------------------------------------------------------------- #
# version tables (error-correction level M only)
# --------------------------------------------------------------------------- #

# version -> (ec codewords per block, [(block count, total codewords per block)])
_VERSION_SPEC = {
    1:  (10, [(1, 26)]),
    2:  (16, [(1, 44)]),
    3:  (26, [(1, 70)]),
    4:  (18, [(2, 50)]),
    5:  (24, [(2, 67)]),
    6:  (16, [(4, 43)]),
    7:  (18, [(4, 49)]),
    8:  (22, [(2, 60), (2, 61)]),
    9:  (22, [(3, 58), (2, 59)]),
    10: (26, [(4, 69), (1, 70)]),
}

# version -> row/column centres of the alignment patterns
_ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

# Pre-computed BCH format strings for level M, masks 0-7.
_FORMAT_BITS = [
    0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0,
]

# Pre-computed BCH version information for versions 7-10.
_VERSION_BITS = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3}

_PAD_BYTES = (0xEC, 0x11)


def _data_codewords(version: int) -> int:
    ec_per_block, groups = _VERSION_SPEC[version]
    return sum(count * (total - ec_per_block) for count, total in groups)


def _capacity(version: int) -> int:
    """Maximum payload bytes for byte mode at this version."""
    header_bits = 4 + (8 if version < 10 else 16)
    return (_data_codewords(version) * 8 - header_bits) // 8


def _choose_version(length: int) -> int:
    for version in sorted(_VERSION_SPEC):
        if length <= _capacity(version):
            return version
    raise QRError("payload too long for this encoder (max 213 bytes)")


# --------------------------------------------------------------------------- #
# bit stream assembly
# --------------------------------------------------------------------------- #

def _build_codewords(payload: bytes, version: int) -> list[int]:
    bits: list[int] = []

    def push(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)                                   # byte mode
    push(len(payload), 8 if version < 10 else 16)     # character count
    for byte in payload:
        push(byte, 8)

    total_bits = _data_codewords(version) * 8
    push(0, min(4, total_bits - len(bits)))           # terminator
    while len(bits) % 8:                              # pad to a byte boundary
        bits.append(0)

    codewords = [
        int("".join(str(b) for b in bits[i:i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    # The pad sequence alternates 0xEC, 0x11 starting from the first pad byte.
    pad_index = 0
    while len(codewords) < _data_codewords(version):
        codewords.append(_PAD_BYTES[pad_index % 2])
        pad_index += 1
    return codewords[:_data_codewords(version)]


def _interleave(codewords: list[int], version: int) -> list[int]:
    """Split into blocks, add EC codewords, and interleave per the spec."""
    ec_per_block, groups = _VERSION_SPEC[version]
    blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []

    offset = 0
    for count, total in groups:
        data_len = total - ec_per_block
        for _ in range(count):
            block = codewords[offset:offset + data_len]
            offset += data_len
            blocks.append(block)
            ec_blocks.append(_rs_remainder(block, ec_per_block))

    result: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            result.append(block[i])
    return result


# --------------------------------------------------------------------------- #
# matrix construction
# --------------------------------------------------------------------------- #

def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_function_patterns(matrix, version: int) -> None:
    size = len(matrix)

    def finder(row: int, col: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                in_ring = (0 <= r <= 6 and c in (0, 6)) or \
                          (0 <= c <= 6 and r in (0, 6))
                in_core = 2 <= r <= 4 and 2 <= c <= 4
                matrix[rr][cc] = 1 if (in_ring or in_core) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for centre_row in _ALIGNMENT[version]:
        for centre_col in _ALIGNMENT[version]:
            # Alignment patterns never overlap the finders.
            if (centre_row < 8 and centre_col < 8) or \
               (centre_row < 8 and centre_col > size - 9) or \
               (centre_row > size - 9 and centre_col < 8):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    matrix[centre_row + r][centre_col + c] = (
                        0 if (abs(r) == 1 and abs(c) <= 1) or
                             (abs(c) == 1 and abs(r) <= 1) else 1
                    )

    for i in range(8, size - 8):                      # timing patterns
        bit = 1 if i % 2 == 0 else 0
        matrix[6][i] = bit
        matrix[i][6] = bit

    matrix[size - 8][8] = 1                           # dark module

    for i in range(9):                                # reserve format areas
        if matrix[8][i] is None:
            matrix[8][i] = 0
        if matrix[i][8] is None:
            matrix[i][8] = 0
    for i in range(size - 8, size):
        if matrix[8][i] is None:
            matrix[8][i] = 0
        if matrix[i][8] is None:
            matrix[i][8] = 0

    if version >= 7:                                  # reserve version areas
        for i in range(6):
            for j in range(size - 11, size - 8):
                matrix[i][j] = 0
                matrix[j][i] = 0


def _reserved(version: int, size: int) -> set[tuple[int, int]]:
    """Coordinates that carry function patterns, not data."""
    taken = set()

    def block(r0: int, c0: int, r1: int, c1: int) -> None:
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if 0 <= r < size and 0 <= c < size:
                    taken.add((r, c))

    block(0, 0, 8, 8)
    block(0, size - 8, 8, size - 1)
    block(size - 8, 0, size - 1, 8)
    for i in range(size):
        taken.add((6, i))
        taken.add((i, 6))
    for centre_row in _ALIGNMENT[version]:
        for centre_col in _ALIGNMENT[version]:
            if (centre_row < 8 and centre_col < 8) or \
               (centre_row < 8 and centre_col > size - 9) or \
               (centre_row > size - 9 and centre_col < 8):
                continue
            block(centre_row - 2, centre_col - 2, centre_row + 2, centre_col + 2)
    if version >= 7:
        block(0, size - 11, 5, size - 9)
        block(size - 11, 0, size - 9, 5)
    return taken


def _place_data(matrix, bits: list[int], version: int) -> None:
    size = len(matrix)
    taken = _reserved(version, size)
    index = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:                                  # skip the timing column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if (row, c) in taken:
                    continue
                matrix[row][c] = bits[index] if index < len(bits) else 0
                index += 1
        upward = not upward
        col -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _apply_mask(matrix, mask: int, version: int):
    size = len(matrix)
    taken = _reserved(version, size)
    out = [row[:] for row in matrix]
    rule = _MASKS[mask]
    for r in range(size):
        for c in range(size):
            if (r, c) not in taken and rule(r, c):
                out[r][c] ^= 1
    return out


def _place_format(matrix, mask: int) -> None:
    size = len(matrix)
    bits = _FORMAT_BITS[mask]
    for i in range(15):
        bit = (bits >> i) & 1
        # First copy: down column 8, then left along row 8.
        if i < 6:
            matrix[i][8] = bit
        elif i == 6:
            matrix[7][8] = bit
        elif i == 7:
            matrix[8][8] = bit
        elif i == 8:
            matrix[8][7] = bit
        else:
            matrix[8][14 - i] = bit
        # Second copy: right along row 8, then up column 8 near the corners.
        if i < 8:
            matrix[8][size - 1 - i] = bit
        else:
            matrix[size - 15 + i][8] = bit


def _place_version(matrix, version: int) -> None:
    if version < 7:
        return
    size = len(matrix)
    bits = _VERSION_BITS[version]
    for i in range(18):
        bit = (bits >> i) & 1
        row, col = i // 3, i % 3
        matrix[row][size - 11 + col] = bit
        matrix[size - 11 + col][row] = bit


def _penalty(matrix) -> int:
    """The four ISO penalty rules — lower is a better-scanning code."""
    size = len(matrix)
    score = 0

    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        run_value, run_length = line[0], 1
        for value in line[1:]:
            if value == run_value:
                run_length += 1
            else:
                if run_length >= 5:
                    score += 3 + (run_length - 5)
                run_value, run_length = value, 1
        if run_length >= 5:
            score += 3 + (run_length - 5)
        # rule 3: finder-like 1:1:3:1:1 patterns with a quiet run beside them
        text = "".join(str(v) for v in line)
        score += 40 * (text.count("10111010000") + text.count("00001011101"))

    for r in range(size - 1):                          # rule 2: 2×2 blocks
        for c in range(size - 1):
            block = (matrix[r][c], matrix[r][c + 1],
                     matrix[r + 1][c], matrix[r + 1][c + 1])
            if block[0] == block[1] == block[2] == block[3]:
                score += 3

    dark = sum(sum(row) for row in matrix)             # rule 4: dark balance
    total = size * size
    # Every 5% the dark ratio strays from half costs another 10 points.
    deviation = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += 10 * max(0, deviation)
    return score


def encode(text: str) -> list[list[int]]:
    """Encode ``text`` and return the finished module matrix (1 = dark)."""
    payload = text.encode("utf-8")
    if not payload:
        raise QRError("nothing to encode")
    version = _choose_version(len(payload))
    size = version * 4 + 17

    codewords = _interleave(_build_codewords(payload, version), version)
    bits = [(byte >> i) & 1 for byte in codewords for i in range(7, -1, -1)]

    base = _new_matrix(size)
    _place_function_patterns(base, version)
    _place_data(base, bits, version)

    best, best_score = None, None
    for mask in range(8):
        candidate = _apply_mask(base, mask, version)
        _place_format(candidate, mask)
        _place_version(candidate, version)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def to_svg(matrix: list[list[int]], scale: int = 8, quiet: int = 4,
           dark: str = "#000000", light: str = "#ffffff") -> str:
    """Render a matrix as a compact, self-contained SVG string."""
    size = len(matrix)
    total = (size + quiet * 2) * scale
    # One <path> of rectangles beats thousands of <rect> elements.
    segments = []
    for r, row in enumerate(matrix):
        c = 0
        while c < size:
            if row[c]:
                start = c
                while c < size and row[c]:
                    c += 1
                x = (start + quiet) * scale
                y = (r + quiet) * scale
                segments.append(f"M{x} {y}h{(c - start) * scale}v{scale}"
                                f"h-{(c - start) * scale}z")
            else:
                c += 1
    path = "".join(segments)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" shape-rendering="crispEdges" '
        f'role="img" aria-label="QR code">'
        f'<rect width="{total}" height="{total}" fill="{light}"/>'
        f'<path d="{path}" fill="{dark}"/></svg>'
    )


def make_svg(text: str, scale: int = 8, dark: str = "#000000",
             light: str = "#ffffff") -> str:
    """Encode and render in one step."""
    return to_svg(encode(text), scale=scale, dark=dark, light=light)
