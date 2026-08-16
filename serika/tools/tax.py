"""Take-home pay and income-tax estimates — bundled brackets, no network.

Tax is just arithmetic over published brackets, so this needs no API: the 2025
(or 2025/26) rates for several countries are encoded below and a small marginal-
bracket engine does the rest. It reports gross, income tax, social
contributions, net pay, and the effective and marginal rates.

It is an **estimate**, and it says so: it uses the standard personal
allowance/deduction and the primary employee contributions, and it excludes the
things that genuinely can't be guessed — US state and Canadian provincial tax
(offered as an optional flat rate), tax credits, pensions, benefits-in-kind, and
every personal circumstance. For anything that matters, check a payslip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["estimate", "COUNTRIES", "parse_salary", "TaxResult"]

TAX_YEAR = "2025"


# --------------------------------------------------------------------------- #
# bracket engine
# --------------------------------------------------------------------------- #

def _marginal(amount: float, brackets: list[tuple[float, float]]) -> float:
    """Tax on ``amount`` given (upper_bound, rate) bands; last bound is inf."""
    tax = 0.0
    lower = 0.0
    for upper, rate in brackets:
        if amount <= lower:
            break
        taxable = min(amount, upper) - lower
        tax += taxable * rate
        lower = upper
    return tax


def _marginal_rate(amount: float, brackets: list[tuple[float, float]]) -> float:
    lower = 0.0
    for upper, rate in brackets:
        if amount <= upper:
            return rate
        lower = upper
    return brackets[-1][1]


# --------------------------------------------------------------------------- #
# country definitions — 2025 / 2025-26 tax year
# --------------------------------------------------------------------------- #

@dataclass
class Country:
    code: str
    name: str
    currency: str
    symbol: str
    note: str
    allows_local_rate: str = ""      # label for an optional state/province rate
    compute: object = None           # callable(gross, local_rate) -> list[line]


@dataclass
class TaxLine:
    label: str
    amount: float
    detail: str = ""


@dataclass
class TaxResult:
    country: Country
    gross: float
    lines: list[TaxLine]
    total_deductions: float
    net: float
    effective_rate: float
    marginal_rate: float
    period_net: dict = field(default_factory=dict)   # monthly/weekly/etc.


_INF = float("inf")


def _us(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025 single filer, standard deduction.
    std = 15000.0
    taxable = max(0.0, gross - std)
    brackets = [(11925, .10), (48475, .12), (103350, .22), (197300, .24),
                (250525, .32), (626350, .35), (_INF, .37)]
    income_tax = _marginal(taxable, brackets)
    # FICA: Social Security 6.2% to the 2025 wage base, Medicare 1.45% + 0.9%
    # additional over $200k.
    ss = min(gross, 176100.0) * 0.062
    medicare = gross * 0.0145 + max(0.0, gross - 200000.0) * 0.009
    lines = [
        TaxLine("Federal income tax", income_tax,
                f"after ${std:,.0f} standard deduction"),
        TaxLine("Social Security", ss, "6.2% up to $176,100"),
        TaxLine("Medicare", medicare, "1.45% (+0.9% over $200k)"),
    ]
    if local_rate:
        lines.append(TaxLine("State income tax", taxable * local_rate / 100,
                             f"{local_rate:g}% flat estimate"))
    return lines


def _uk(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025/26. Personal allowance tapers away above £100k.
    allowance = 12570.0
    if gross > 100000:
        allowance = max(0.0, allowance - (gross - 100000) / 2)
    taxable = max(0.0, gross - allowance)
    # Bands are measured from the end of the allowance.
    brackets = [(50270 - allowance if allowance < 50270 else 0, .20),
                (125140 - allowance, .40), (_INF, .45)]
    brackets = [(b, r) for b, r in brackets if b > 0] or [(_INF, .20)]
    income_tax = _marginal(taxable, brackets)
    # National Insurance (employee, 2025/26): 8% £12,570–£50,270, 2% above.
    ni = 0.0
    if gross > 12570:
        ni += (min(gross, 50270) - 12570) * 0.08
    if gross > 50270:
        ni += (gross - 50270) * 0.02
    return [
        TaxLine("Income tax", income_tax,
                f"personal allowance £{allowance:,.0f}"),
        TaxLine("National Insurance", ni, "8% then 2%"),
    ]


def _canada(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025 federal only. Basic personal amount ~$16,129.
    bpa = 16129.0
    taxable = max(0.0, gross - bpa)
    brackets = [(57375, .15), (114750, .205), (177882, .26), (253414, .29),
                (_INF, .33)]
    income_tax = _marginal(taxable, brackets)
    # CPP2 and EI simplified: CPP 5.95% to ~$71,300 (over $3,500 exemption),
    # EI 1.64% to ~$65,700.
    cpp = max(0.0, min(gross, 71300) - 3500) * 0.0595
    ei = min(gross, 65700) * 0.0164
    lines = [
        TaxLine("Federal income tax", income_tax,
                f"basic personal amount ${bpa:,.0f}"),
        TaxLine("CPP", cpp, "5.95% on eligible earnings"),
        TaxLine("EI", ei, "1.64%"),
    ]
    if local_rate:
        lines.append(TaxLine("Provincial income tax", taxable * local_rate / 100,
                             f"{local_rate:g}% flat estimate"))
    return lines


def _australia(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025-26 resident rates.
    brackets = [(18200, 0.0), (45000, .16), (135000, .30), (190000, .37),
                (_INF, .45)]
    income_tax = _marginal(gross, brackets)
    medicare = gross * 0.02 if gross > 27222 else 0.0
    return [
        TaxLine("Income tax", income_tax, "resident rates, no offsets"),
        TaxLine("Medicare levy", medicare, "2%"),
    ]


def _ireland(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025 single. Standard rate band €44,000.
    income_tax = _marginal(gross, [(44000, .20), (_INF, .40)])
    # Tax credits (personal + employee) ~€4,000 reduce the bill.
    income_tax = max(0.0, income_tax - 4000.0)
    # USC 2025 bands.
    usc = _marginal(gross, [(12012, .005), (27382, .02), (70044, .03),
                            (_INF, .08)]) if gross > 13000 else 0.0
    prsi = gross * 0.041
    return [
        TaxLine("Income tax", income_tax, "after ~€4,000 tax credits"),
        TaxLine("USC", usc, "universal social charge"),
        TaxLine("PRSI", prsi, "4.1%"),
    ]


def _germany(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025 Einkommensteuer for a single person (Grundtarif), official formula.
    zve = gross
    g = 12096.0
    if zve <= g:
        income_tax = 0.0
    elif zve <= 17443:
        y = (zve - g) / 10000
        income_tax = (932.30 * y + 1400) * y
    elif zve <= 68480:
        z = (zve - 17443) / 10000
        income_tax = (176.64 * z + 2397) * z + 1015.13
    elif zve <= 277825:
        income_tax = 0.42 * zve - 10911.92
    else:
        income_tax = 0.45 * zve - 19246.67
    income_tax = max(0.0, income_tax)
    # Employee social security (approx 2025 employee shares, capped).
    pension = min(gross, 96600) * 0.093
    health = min(gross, 66150) * 0.083          # incl. avg Zusatzbeitrag
    care = min(gross, 66150) * 0.023
    unemp = min(gross, 96600) * 0.013
    return [
        TaxLine("Income tax", income_tax, "Grundtarif, single"),
        TaxLine("Pension insurance", pension, "9.3%"),
        TaxLine("Health insurance", health, "~8.3%"),
        TaxLine("Care insurance", care, "2.3%"),
        TaxLine("Unemployment insurance", unemp, "1.3%"),
    ]


def _netherlands(gross: float, local_rate: float) -> list[TaxLine]:
    # 2025 Box 1 (below state-pension age), combined with national insurance.
    brackets = [(38441, .3582), (76817, .3748), (_INF, .495)]
    income_tax = _marginal(gross, brackets)
    # General + labour tax credits reduce this substantially; approximate.
    credit = 3068.0 + max(0.0, 5599 - max(0.0, gross - 43071) * 0.0651)
    income_tax = max(0.0, income_tax - min(credit, income_tax))
    return [
        TaxLine("Income tax + NI", income_tax,
                "Box 1, after general & labour credits"),
    ]


COUNTRIES: dict[str, Country] = {
    "us": Country("us", "United States", "USD", "$",
                  "Federal tax + FICA. Excludes state tax (add a rate below), "
                  "credits, and 401(k).", "State tax rate", _us),
    "uk": Country("uk", "United Kingdom", "GBP", "£",
                  "Income tax + National Insurance, England/Wales/NI bands.",
                  "", _uk),
    "ca": Country("ca", "Canada", "CAD", "$",
                  "Federal tax + CPP + EI. Excludes provincial tax (add a rate "
                  "below).", "Provincial tax rate", _canada),
    "au": Country("au", "Australia", "AUD", "$",
                  "Resident income tax + Medicare levy. Excludes HELP and "
                  "offsets.", "", _australia),
    "ie": Country("ie", "Ireland", "EUR", "€",
                  "Income tax (after standard credits) + USC + PRSI, single.",
                  "", _ireland),
    "de": Country("de", "Germany", "EUR", "€",
                  "Income tax (Grundtarif) + employee social insurance, single, "
                  "tax class I.", "", _germany),
    "nl": Country("nl", "Netherlands", "EUR", "€",
                  "Box 1 income tax + national insurance, after general & "
                  "labour credits.", "", _netherlands),
}

_COUNTRY_ALIASES = {
    "usa": "us", "united states": "us", "america": "us", "american": "us",
    "uk": "uk", "britain": "uk", "united kingdom": "uk", "england": "uk",
    "canada": "ca", "canadian": "ca", "australia": "au", "australian": "au",
    "ireland": "ie", "irish": "ie", "germany": "de", "german": "de",
    "netherlands": "nl", "holland": "nl", "dutch": "nl",
}


def resolve_country(text: str) -> str:
    key = (text or "").strip().lower()
    if key in COUNTRIES:
        return key
    return _COUNTRY_ALIASES.get(key, "")


def estimate(gross: float, country_code: str = "us",
             local_rate: float = 0.0) -> TaxResult | None:
    country = COUNTRIES.get(country_code)
    if country is None or gross < 0 or gross > 1e12:
        return None
    lines = country.compute(gross, local_rate)
    total = sum(line.amount for line in lines)
    net = gross - total
    effective = (total / gross * 100) if gross else 0.0
    # Marginal rate: tax on one more unit of income.
    bump = country.compute(gross + 100, local_rate)
    marginal = (sum(l.amount for l in bump) - total) / 100 * 100
    return TaxResult(
        country=country, gross=gross, lines=lines, total_deductions=total,
        net=net, effective_rate=effective, marginal_rate=marginal,
        period_net={
            "year": net, "month": net / 12, "week": net / 52,
            "day": net / 260, "hour": net / 2080,
        },
    )


# --------------------------------------------------------------------------- #
# query parsing
# --------------------------------------------------------------------------- #

_SALARY_RE = re.compile(
    r"^\s*(?:take[\s-]?home(?:\s+pay)?|salary(?:\s+calculator)?|net\s+pay|"
    r"paycheck(?:\s+calculator)?|income\s+tax(?:\s+calculator)?|"
    r"tax\s+calculator|after[\s-]?tax)\b(.*)$",
    re.I,
)
_AMOUNT_RE = re.compile(r"([£$€]?\s*[\d,]+(?:\.\d+)?)\s*(k|000)?", re.I)


@dataclass
class SalaryQuery:
    gross: float = 0.0
    country: str = ""
    empty: bool = False


def parse_salary(query: str) -> SalaryQuery | None:
    match = _SALARY_RE.match(query.strip())
    if not match:
        return None
    body = match.group(1)

    country = ""
    for word, code in _COUNTRY_ALIASES.items():
        if re.search(r"\b" + re.escape(word) + r"\b", body, re.I):
            country = code
            break

    amount = 0.0
    m = _AMOUNT_RE.search(body)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "")
        raw = re.sub(r"[£$€]", "", raw)
        try:
            amount = float(raw)
            if m.group(2):                      # "80k" or "80 000"
                amount *= 1000 if m.group(2).lower() == "k" else 1
        except ValueError:
            amount = 0.0

    if amount <= 0:
        return SalaryQuery(empty=True, country=country or "us")
    return SalaryQuery(gross=amount, country=country or "us")
