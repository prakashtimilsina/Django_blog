#!/usr/bin/env python3
"""
Generate synthetic deeply-nested, multi-namespace XML test data.

Produces XML matching the structure of tests/fixtures/sample_deep_ns.xml.
Use this to create files of any size for load/performance testing without
ever touching real patient data.

Usage
-----
    # 100 records → local file
    python scripts/generate_test_data.py --records 100 --output /tmp/test.xml

    # 50 000 records → GCS  (requires gcloud auth / ADC)
    python scripts/generate_test_data.py --records 50000 --output gs://bucket/raw/test_50k.xml

    # Control density of repeating elements
    python scripts/generate_test_data.py --records 1000 --max-teeth 10 --max-panels 3 --output /tmp/dense.xml

    # Seed for reproducible data
    python scripts/generate_test_data.py --records 500 --seed 42 --output /tmp/seeded.xml

Output conforms to:
    Row tag    : record          (set row_tag: record in pipeline config)
    Namespaces : ehr, dental, lab, pharma, billing, xsi
    Max depth  : 8 levels
"""

import argparse
import io
import random
import sys
from datetime import date, timedelta
from xml.etree import ElementTree as ET

# ── Namespace URIs ────────────────────────────────────────────────────────────

_NS = {
    "ehr":     "http://example.com/ehr/v3",
    "dental":  "http://example.com/dental/v2",
    "lab":     "http://example.com/laboratory/v1",
    "pharma":  "http://example.com/pharmacy/v1",
    "billing": "http://example.com/billing/v2",
    "xsi":     "http://www.w3.org/2001/XMLSchema-instance",
}

for _prefix, _uri in _NS.items():
    ET.register_namespace(_prefix, _uri)

XSI_NIL = f"{{{_NS['xsi']}}}nil"


def _tag(prefix: str, local: str) -> str:
    return f"{{{_NS[prefix]}}}{local}"


def _sub(parent, prefix: str, local: str, text=None):
    el = ET.SubElement(parent, _tag(prefix, local))
    if text is not None:
        el.text = str(text)
    return el


def _nil(parent, prefix: str, local: str):
    el = ET.SubElement(parent, _tag(prefix, local))
    el.set(XSI_NIL, "true")
    return el


# ── Random data pools ─────────────────────────────────────────────────────────

_FIRST_NAMES = ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace",
                "Henry", "Iris", "Jack", "Karen", "Leo", "Maria", "Noah",
                "Olivia", "Paul", "Quinn", "Rachel", "Sam", "Tara"]
_LAST_NAMES  = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
                "Miller", "Davis", "Wilson", "Moore", "Taylor", "Anderson"]
_PREFIXES    = ["Dr.", "Mr.", "Ms.", "Mrs.", None]
_GENDERS     = ["M", "F", "U"]
_RACES       = ["White", "Black", "Hispanic", "Asian", "Other"]
_MARITAL     = ["SINGLE", "MARRIED", "DIVORCED", "WIDOWED"]
_LANGUAGES   = ["EN", "ES", "FR", "ZH", "PT"]
_PLAN_TYPES  = ["PPO", "HMO", "EPO", "HDHP"]
_PAYERS      = ["BlueCross BlueShield", "Aetna", "Cigna", "UnitedHealth",
                "Humana", "Kaiser"]
_STATES      = ["IL", "TX", "CA", "NY", "FL", "WA", "GA", "OH"]
_ADDR_TYPES  = ["HOME", "WORK", "MAILING"]
_QUADRANTS   = ["UL", "UR", "LL", "LR"]
_ARCHES      = {"UL": "UPPER", "UR": "UPPER", "LL": "LOWER", "LR": "LOWER"}
_SURFACES    = ["BUCCAL", "LINGUAL", "MESIAL", "DISTAL", "OCCLUSAL"]
_CALCULUS    = ["NONE", "TRACE", "LIGHT", "MODERATE", "HEAVY"]
_BONE_LOSS   = ["NONE", "SLIGHT", "MODERATE", "SEVERE"]
_PERIAPICAL  = ["NORMAL", "ABNORMAL", "ABSCESS"]
_PERIO_SCORE = ["HEALTHY", "MILD", "MODERATE", "SEVERE"]
_EXAM_TYPES  = ["COMPREHENSIVE", "PERIODIC", "LIMITED", "EMERGENCY"]
_TOOTH_TYPES = ["PERMANENT", "PRIMARY"]
_CDT_CODES   = [("D0120", "Periodic oral evaluation", 65.0),
                ("D0150", "Comprehensive oral evaluation", 95.0),
                ("D0274", "Bitewing radiographic images - four images", 75.0),
                ("D1110", "Prophylaxis - adult", 95.0),
                ("D2140", "Amalgam - one surface, primary or permanent", 145.0),
                ("D2160", "Amalgam - three surfaces, primary or permanent", 225.0),
                ("D2391", "Resin composite - one surface, posterior", 165.0),
                ("D7140", "Extraction, erupted tooth", 185.0),
                ("D4341", "Periodontal scaling and root planing", 250.0)]
_LOINC_TESTS = [
    ("6690-2", "WBC",   "K/uL", 4.5,  11.0,  3.0,  15.0),
    ("789-8",  "RBC",   "M/uL", 4.2,  5.4,   3.0,   7.0),
    ("718-7",  "HGB",   "g/dL", 12.0, 16.0,  8.0,  20.0),
    ("787-2",  "MCV",   "fL",   80.0, 100.0, 60.0, 120.0),
    ("4548-4", "HbA1c", "%",    4.0,  5.6,   4.0,  14.0),
    ("2160-0", "CREAT", "mg/dL",0.6,  1.2,   0.4,   5.0),
    ("2823-3", "K",     "mEq/L",3.5,  5.0,   2.0,   7.0),
]
_LAB_PANELS  = ["CBC", "CMP", "HbA1c", "Lipid Panel", "TSH"]
_LABS        = ["Quest Diagnostics", "LabCorp", "BioReference", "Sonic Healthcare"]
_ROUTES      = ["ORAL", "TOPICAL", "IV", "IM", "SL"]
_FREQUENCIES = ["QD", "BID", "TID", "QID", "PRN", "QHS"]
_MEDS        = [("723",  "Amoxil",    "Amoxicillin",   500, "mg"),
                ("1049",  "Ibuprofen", "Ibuprofen",     400, "mg"),
                ("10832", "Peridex",   "Chlorhexidine", 120, "mL"),
                ("41493", "Lidocaine", "Lidocaine",      20, "mg"),
                ("7052",  "Motrin",    "Ibuprofen",     600, "mg")]
_SPECIALTIES = ["General Dentistry", "Endodontics", "Periodontics",
                "Oral Surgery", "Orthodontics"]
_ADJ_TYPES   = ["CONTRACTUAL", "PATIENT_RESPONSIBILITY", "WRITE_OFF"]
_REASON_CODES= ["CO-45", "CO-97", "PR-1", "OA-23"]


def _rand_date(start_year=1950, end_year=2005) -> str:
    start = date(start_year, 1, 1)
    end   = date(end_year, 12, 31)
    return str(start + timedelta(days=random.randint(0, (end - start).days)))


def _rand_npi() -> str:
    return str(random.randint(1000000000, 9999999999))


def _rand_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n:06d}"


# ── Element builders ──────────────────────────────────────────────────────────

def _build_name(parent, rng):
    name_el = _sub(parent, "ehr", "name")
    pfx = rng.choice(_PREFIXES)
    if pfx:
        _sub(name_el, "ehr", "prefix", pfx)
    else:
        _nil(name_el, "ehr", "prefix")
    _sub(name_el, "ehr", "first",  rng.choice(_FIRST_NAMES))
    middle = rng.choice([rng.choice(_FIRST_NAMES)[0] + ".", None])
    if middle:
        _sub(name_el, "ehr", "middle", middle)
    else:
        _nil(name_el, "ehr", "middle")
    _sub(name_el, "ehr", "last",   rng.choice(_LAST_NAMES))
    if rng.random() < 0.05:
        _sub(name_el, "ehr", "suffix", rng.choice(["Jr.", "Sr.", "III"]))
    else:
        _nil(name_el, "ehr", "suffix")
    return name_el


def _build_address(parent, rng, addr_type):
    addr = _sub(parent, "ehr", "address")
    _sub(addr, "ehr", "addr_type", addr_type)
    _sub(addr, "ehr", "street1",   f"{rng.randint(1,9999)} {rng.choice(['Main','Oak','Elm','Pine','Cedar'])} {rng.choice(['St','Ave','Blvd','Dr','Ln'])}")
    if rng.random() < 0.3:
        _sub(addr, "ehr", "street2", f"Apt {rng.randint(1,50)}{rng.choice(['A','B','C',''])} ")
    else:
        _nil(addr, "ehr", "street2")
    _sub(addr, "ehr", "city",         f"City{rng.randint(1,50)}")
    _sub(addr, "ehr", "state",        rng.choice(_STATES))
    _sub(addr, "ehr", "zip",          f"{rng.randint(10000,99999)}")
    _sub(addr, "ehr", "country",      "US")
    _sub(addr, "ehr", "active_since", _rand_date(2000, 2023))
    if rng.random() < 0.7:
        coords = _sub(addr, "ehr", "coordinates")
        _sub(coords, "ehr", "latitude",   round(rng.uniform(25.0, 49.0), 4))
        _sub(coords, "ehr", "longitude",  round(rng.uniform(-124.0, -70.0), 4))
        _sub(coords, "ehr", "accuracy_m", rng.randint(5, 100))
    else:
        _nil(addr, "ehr", "coordinates")


def _build_coverage(parent, rng):
    cov = _sub(parent, "ehr", "coverage")
    ded = rng.choice([500, 1000, 1500, 2000, 3000])
    _sub(cov, "ehr", "deductible_annual",  float(ded))
    _sub(cov, "ehr", "deductible_met",     round(rng.uniform(0, ded), 2))
    _sub(cov, "ehr", "copay",              rng.choice([15, 20, 25, 30, 40, 50]))
    _sub(cov, "ehr", "coinsurance_pct",    rng.choice([60, 70, 80, 90]))
    oop = rng.choice([3000, 5000, 7000, 10000])
    _sub(cov, "ehr", "out_of_pocket_max",  float(oop))
    _sub(cov, "ehr", "out_of_pocket_met",  round(rng.uniform(0, oop * 0.5), 2))


def _build_insurance(parent, rng):
    ins = _sub(parent, "ehr", "insurance")
    pri = _sub(ins, "ehr", "primary")
    _sub(pri, "ehr", "payer_name",     rng.choice(_PAYERS))
    _sub(pri, "ehr", "policy_id",      f"POL-{rng.randint(100000,999999)}")
    _sub(pri, "ehr", "group_id",       f"GRP-{rng.randint(100,999)}")
    _sub(pri, "ehr", "plan_type",      rng.choice(_PLAN_TYPES))
    _sub(pri, "ehr", "effective_date", f"{rng.randint(2020,2026)}-01-01")
    _sub(pri, "ehr", "term_date",      f"{rng.randint(2020,2026)}-12-31")
    _build_coverage(pri, rng)
    _nil(ins, "ehr", "secondary")   # always absent in synthetic data


def _build_surface(parent, rng, surface_name):
    surf = _sub(parent, "dental", "surface")
    _sub(surf, "dental", "name",            surface_name)
    _sub(surf, "dental", "pocket_depth_mm", rng.randint(1, 9))
    _sub(surf, "dental", "recession_mm",    rng.randint(0, 5))
    _sub(surf, "dental", "bleeding",        str(rng.random() < 0.3).lower())
    _sub(surf, "dental", "plaque",          str(rng.random() < 0.4).lower())
    _sub(surf, "dental", "calculus",        rng.choice(_CALCULUS))
    _sub(surf, "dental", "gingival_margin", rng.randint(-4, 2))


def _build_tooth(parent, rng, tooth_number):
    tooth = _sub(parent, "dental", "tooth")
    quad  = rng.choice(_QUADRANTS)
    _sub(tooth, "dental", "number",         tooth_number)
    _sub(tooth, "dental", "name",           f"Tooth {tooth_number}")
    _sub(tooth, "dental", "arch",           _ARCHES[quad])
    _sub(tooth, "dental", "quadrant",       quad)
    _sub(tooth, "dental", "type",           rng.choice(_TOOTH_TYPES))
    _sub(tooth, "dental", "mobility_grade", rng.randint(0, 3))
    _sub(tooth, "dental", "furcation_class",rng.choice(["NONE","CLASS_I","CLASS_II","CLASS_III"]))

    # surfaces — nested array (L7 → L8)
    n_surfaces = rng.randint(1, 4)
    surfs_el = _sub(tooth, "dental", "surfaces")
    for s in rng.sample(_SURFACES, n_surfaces):
        _build_surface(surfs_el, rng, s)

    # xray struct (L7)
    xray = _sub(tooth, "dental", "xray")
    _sub(xray, "dental", "bone_loss",       rng.choice(_BONE_LOSS))
    _sub(xray, "dental", "furcation",       rng.choice(["NONE","CLASS_I","CLASS_II","CLASS_III"]))
    _sub(xray, "dental", "periapical",      rng.choice(_PERIAPICAL))
    _sub(xray, "dental", "calculus_visible",str(rng.random() < 0.2).lower())

    # restorations array (L7) — 30% chance absent
    if rng.random() < 0.3:
        _nil(tooth, "dental", "restorations")
    else:
        rests = _sub(tooth, "dental", "restorations")
        n_rest = rng.randint(1, 2)
        for _ in range(n_rest):
            rest = _sub(rests, "dental", "restoration")
            _sub(rest, "dental", "material_type", rng.choice(["COMPOSITE","AMALGAM","CROWN","VENEER"]))
            _sub(rest, "dental", "surface_code",  rng.choice(["MO","DO","MOD","B","O"]))
            _sub(rest, "dental", "date_placed",   _rand_date(2010, 2025))
            _sub(rest, "dental", "condition",     rng.choice(["GOOD","FAIR","POOR","REPLACE"]))
            _sub(rest, "dental", "lot_number",    f"LOT{rng.randint(1000,9999)}")


def _build_test(parent, rng, loinc):
    loinc_code, name, unit, ref_low, ref_high, val_min, val_max = loinc
    test = _sub(parent, "lab", "test")
    value = round(rng.uniform(val_min, val_max), 1)
    _sub(test, "lab", "loinc_code",    loinc_code)
    _sub(test, "lab", "name",          name)
    _sub(test, "lab", "value_numeric", value)
    _nil(test, "lab", "value_text")
    _sub(test, "lab", "unit",          unit)
    ref = _sub(test, "lab", "reference_range")
    _sub(ref, "lab", "low",  ref_low)
    _sub(ref, "lab", "high", ref_high)
    _sub(ref, "lab", "unit", unit)
    if value < ref_low:
        flag = "LOW"
    elif value > ref_high:
        flag = "HIGH"
    else:
        flag = "NORMAL"
    _sub(test, "lab", "flag",   flag)
    _sub(test, "lab", "status", "FINAL")


def _build_panel(parent, rng, panel_name):
    panel = _sub(parent, "lab", "panel")
    _sub(panel, "lab", "name",          panel_name)
    _sub(panel, "lab", "ordered_date",  _rand_date(2025, 2026))
    _sub(panel, "lab", "resulted_date", _rand_date(2025, 2026))
    _sub(panel, "lab", "lab_name",      rng.choice(_LABS))
    tests_el = _sub(panel, "lab", "tests")
    # Pick 2-5 loinc tests per panel
    for loinc in rng.sample(_LOINC_TESTS, rng.randint(2, min(5, len(_LOINC_TESTS)))):
        _build_test(tests_el, rng, loinc)


def _build_medication(parent, rng):
    med_data = rng.choice(_MEDS)
    rxnorm, brand, generic, strength, unit = med_data
    med = _sub(parent, "pharma", "medication")
    _sub(med, "pharma", "rxnorm_code",     rxnorm)
    _sub(med, "pharma", "brand_name",      brand)
    _sub(med, "pharma", "generic_name",    generic)
    _sub(med, "pharma", "dosage_strength", strength)
    _sub(med, "pharma", "dosage_unit",     unit)
    _sub(med, "pharma", "route",           rng.choice(_ROUTES))
    _sub(med, "pharma", "frequency",       rng.choice(_FREQUENCIES))
    days = rng.choice([7, 10, 14, 30, 90])
    _sub(med, "pharma", "days_supply",     days)
    start = _rand_date(2025, 2026)
    _sub(med, "pharma", "start_date",  start)
    _sub(med, "pharma", "end_date",    start)   # simplified
    pres = _sub(med, "pharma", "prescriber")
    _sub(pres, "pharma", "npi",       _rand_npi())
    _sub(pres, "pharma", "first_name",rng.choice(_FIRST_NAMES))
    _sub(pres, "pharma", "last_name", rng.choice(_LAST_NAMES))
    _sub(pres, "pharma", "specialty", rng.choice(_SPECIALTIES))
    disp = _sub(med, "pharma", "dispense_info")
    qty = strength * days
    _sub(disp, "pharma", "quantity",           qty)
    _sub(disp, "pharma", "unit",               "TABLET" if unit == "mg" else "BOTTLE")
    refills = rng.randint(0, 3)
    _sub(disp, "pharma", "refills_authorized", refills)
    _sub(disp, "pharma", "refills_remaining",  refills)
    _sub(disp, "pharma", "pharmacy_npi",       _rand_npi())


def _build_line(parent, rng, cdt_info, seq):
    cdt_code, description, base_fee = cdt_info
    line = _sub(parent, "billing", "line")
    _sub(line, "billing", "seq",        seq)
    _sub(line, "billing", "cdt_code",   cdt_code)
    _sub(line, "billing", "units",      1)
    _sub(line, "billing", "billed_fee", base_fee)
    adj_pct = rng.uniform(0.15, 0.45)
    adj_amt = round(base_fee * adj_pct, 2)
    adjs = _sub(line, "billing", "adjustments")
    adj  = _sub(adjs, "billing", "adjustment")
    _sub(adj, "billing", "adj_type",    "CONTRACTUAL")
    _sub(adj, "billing", "amount",      -adj_amt)
    _sub(adj, "billing", "reason_code", rng.choice(_REASON_CODES))
    allowed = round(base_fee - adj_amt, 2)
    paid    = round(allowed * rng.uniform(0.6, 0.9), 2)
    patient = round(allowed - paid, 2)
    _sub(line, "billing", "allowed_amount", allowed)
    _sub(line, "billing", "payer_paid",     paid)
    _sub(line, "billing", "patient_resp",   patient)
    return allowed, paid, patient


# ── Record builder ────────────────────────────────────────────────────────────

def build_record(root, n: int, rng, max_teeth: int, max_panels: int) -> None:
    rec = ET.SubElement(root, _tag("ehr", "record"))

    year  = rng.randint(2024, 2026)
    month = rng.randint(1, 12)

    _sub(rec, "ehr", "record_id",    _rand_id("REC", n))
    _sub(rec, "ehr", "source_system","SYNTHETIC-EHR")
    _sub(rec, "ehr", "batch_id",     _rand_id("BATCH", n // 1000))
    _sub(rec, "ehr", "year",         year)
    _sub(rec, "ehr", "month",        month)

    # ── patient ────────────────────────────────────────────────────────────
    patient = _sub(rec, "ehr", "patient")
    demo    = _sub(patient, "ehr", "demographics")

    # L4 personal
    personal = _sub(demo, "ehr", "personal")
    _build_name(personal, rng)
    _sub(personal, "ehr", "date_of_birth",  _rand_date(1940, 2005))
    _sub(personal, "ehr", "gender",         rng.choice(_GENDERS))
    _sub(personal, "ehr", "race",           rng.choice(_RACES))
    _sub(personal, "ehr", "ethnicity",      rng.choice(["Hispanic", "Non-Hispanic"]))
    _sub(personal, "ehr", "marital_status", rng.choice(_MARITAL))
    _sub(personal, "ehr", "ssn_last4",      f"{rng.randint(1000,9999)}")
    _sub(personal, "ehr", "language",       rng.choice(_LANGUAGES))
    _sub(personal, "ehr", "deceased",       "false")

    # L4 contact
    contact  = _sub(demo, "ehr", "contact")
    addrs_el = _sub(contact, "ehr", "addresses")
    n_addrs  = rng.randint(1, 2)
    for i, atype in enumerate(rng.sample(_ADDR_TYPES, n_addrs)):
        _build_address(addrs_el, rng, atype)
    phones_el = _sub(contact, "ehr", "phones")
    for _ in range(rng.randint(1, 2)):
        _sub(phones_el, "ehr", "phone", f"555-{rng.randint(1000,9999)}")
    _sub(contact, "ehr", "email",             f"patient{n}@example.com")
    _sub(contact, "ehr", "preferred_contact", rng.choice(["EMAIL","PHONE","MAIL"]))

    # L4 insurance
    _build_insurance(demo, rng)

    # ── clinical ──────────────────────────────────────────────────────────
    clinical = _sub(rec, "dental", "clinical")
    exam     = _sub(clinical, "dental", "examination")
    _sub(exam, "dental", "exam_date",    f"{year}-{month:02d}-{rng.randint(1,28):02d}")
    _sub(exam, "dental", "exam_type",    rng.choice(_EXAM_TYPES))
    _sub(exam, "dental", "provider_npi", _rand_npi())

    perio = _sub(exam, "dental", "periodontal")
    _sub(perio, "dental", "overall_score", rng.choice(_PERIO_SCORE))
    _sub(perio, "dental", "bone_loss_pct", rng.randint(0, 60))

    n_teeth  = rng.randint(1, max_teeth)
    teeth_el = _sub(perio, "dental", "teeth")
    tooth_numbers = rng.sample(range(11, 49), n_teeth)
    for tn in sorted(tooth_numbers):
        _build_tooth(teeth_el, rng, tn)

    # treatments
    treats_el = _sub(clinical, "dental", "treatments")
    cdt_picks = rng.sample(_CDT_CODES, rng.randint(1, min(4, len(_CDT_CODES))))
    for cdt in cdt_picks:
        t = _sub(treats_el, "dental", "treatment")
        _sub(t, "dental", "cdt_code",    cdt[0])
        _sub(t, "dental", "description", cdt[1])
        _sub(t, "dental", "fee",         cdt[2])
        _nil(t, "dental", "tooth_numbers")
        _nil(t, "dental", "materials")

    # ── lab results ───────────────────────────────────────────────────────
    if rng.random() < 0.75:
        lab_res  = _sub(rec, "lab", "lab_results")
        panels_el= _sub(lab_res, "lab", "panels")
        n_panels = rng.randint(1, max_panels)
        for pname in rng.sample(_LAB_PANELS, n_panels):
            _build_panel(panels_el, rng, pname)
    else:
        _nil(rec, "lab", "lab_results")

    # ── medications ───────────────────────────────────────────────────────
    if rng.random() < 0.6:
        meds_el = _sub(rec, "pharma", "medications")
        for _ in range(rng.randint(1, 3)):
            _build_medication(meds_el, rng)
    else:
        _nil(rec, "pharma", "medications")

    # ── billing ───────────────────────────────────────────────────────────
    claim  = _sub(rec, "billing", "claim")
    header = _sub(claim, "billing", "header")
    _sub(header, "billing", "claim_id",        _rand_id("CLM", n))
    _sub(header, "billing", "claim_type",      "DENTAL")
    _sub(header, "billing", "submission_date", f"{year}-{month:02d}-{rng.randint(1,28):02d}")
    _sub(header, "billing", "payer_id",        f"PAYER-{rng.randint(100,999)}")
    _sub(header, "billing", "status",          rng.choice(["SUBMITTED","PAID","PENDING","DENIED"]))

    lines_el = _sub(claim, "billing", "lines")
    total_billed = total_allowed = total_paid = total_patient = 0.0
    for i, cdt in enumerate(cdt_picks, 1):
        allowed, paid, patient = _build_line(lines_el, rng, cdt, i)
        total_billed   += cdt[2]
        total_allowed  += allowed
        total_paid     += paid
        total_patient  += patient

    totals = _sub(claim, "billing", "totals")
    _sub(totals, "billing", "total_billed",       round(total_billed, 2))
    _sub(totals, "billing", "total_allowed",      round(total_allowed, 2))
    _sub(totals, "billing", "total_payer_paid",   round(total_paid, 2))
    _sub(totals, "billing", "total_patient_resp", round(total_patient, 2))


# ── XML writer ────────────────────────────────────────────────────────────────

_NS_DECL = "\n".join(
    f'    xmlns:{p}="{u}"' for p, u in _NS.items() if p != "xsi"
) + '\n    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def _write_output(tree: ET.ElementTree, output_path: str) -> None:
    if output_path.startswith("gs://"):
        try:
            from google.cloud import storage as gcs
        except ImportError as exc:
            raise ImportError("pip install google-cloud-storage for gs:// output") from exc
        buf = io.BytesIO()
        tree.write(buf, encoding="utf-8", xml_declaration=True)
        buf.seek(0)
        without_scheme = output_path[5:]
        bucket_name, _, blob_name = without_scheme.partition("/")
        client = gcs.Client()
        client.bucket(bucket_name).blob(blob_name).upload_from_file(
            buf, content_type="application/xml"
        )
        print(f"Uploaded to {output_path}")
    elif output_path == "-":
        tree.write(sys.stdout.buffer, encoding="utf-8", xml_declaration=True)
    else:
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        print(f"Written to {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic deep-nested namespaced XML test data."
    )
    parser.add_argument("--records",    type=int, default=10,
                        help="Number of <record> elements to generate (default: 10)")
    parser.add_argument("--max-teeth",  type=int, default=10,
                        help="Max teeth per record (default: 10)")
    parser.add_argument("--max-panels", type=int, default=3,
                        help="Max lab panels per record (default: 3)")
    parser.add_argument("--seed",       type=int, default=None,
                        help="Random seed for reproducible output")
    parser.add_argument("--output",     default="-",
                        help="Output path: local file, gs:// URI, or - for stdout")
    parser.add_argument("--progress",   action="store_true",
                        help="Print progress every 1 000 records")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    root = ET.Element(_tag("ehr", "records"))

    print(f"Generating {args.records:,} records …", file=sys.stderr)
    for i in range(1, args.records + 1):
        build_record(root, i, rng, args.max_teeth, args.max_panels)
        if args.progress and i % 1000 == 0:
            print(f"  {i:,} / {args.records:,}", file=sys.stderr)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    _write_output(tree, args.output)
    print(f"Done — {args.records:,} records generated.", file=sys.stderr)


if __name__ == "__main__":
    main()
