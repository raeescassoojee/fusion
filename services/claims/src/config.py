# Central settings for the claims pipeline.
# Change values HERE, never scattered through the code.

from pathlib import Path

# --- Paths (auto-resolved so it works on any teammate's machine) ---
CLAIMS_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = CLAIMS_DIR / "data" / "raw"
CURATED_DIR = CLAIMS_DIR / "data" / "curated"

RAW_FILE = RAW_DIR / "claims_raw.xlsx"

# --- Risk Pulse weights (must sum to 1.0). Handbook page 11. ---
WEIGHTS = {
    "frequency": 0.45,
    "severity": 0.30,
    "recency": 0.15,
    "peak_time": 0.10,
}

# --- Pilot metros: only these suburbs get geocoded and scored ---
# Filled in during Step 4 after we see the cleaned top suburbs.
# --- Pilot suburbs: only these get geocoded and scored ---
# Hand-picked: high-incident and unambiguous (no name shared across cities).
# Format: cleaned suburb name -> metro label + city context for geocoding.
PILOT_SUBURBS = {
    # Cape Town
    "SOMERSET WEST":         {"metro": "Cape Town", "geocode_hint": "Somerset West, Cape Town, South Africa"},
    "RONDEBOSCH":            {"metro": "Cape Town", "geocode_hint": "Rondebosch, Cape Town, South Africa"},
    "CAPE TOWN CITY CENTRE": {"metro": "Cape Town", "geocode_hint": "Cape Town City Centre, Cape Town, South Africa"},
    "CLAREMONT":             {"metro": "Cape Town", "geocode_hint": "Claremont, Cape Town, South Africa"},
    "SEA POINT":             {"metro": "Cape Town", "geocode_hint": "Sea Point, Cape Town, South Africa"},
    "NEWLANDS":              {"metro": "Cape Town", "geocode_hint": "Newlands, Cape Town, South Africa"},
    "TABLE VIEW":            {"metro": "Cape Town", "geocode_hint": "Table View, Cape Town, South Africa"},
    # Gauteng (Johannesburg + Pretoria)
    "BRYANSTON":             {"metro": "Gauteng", "geocode_hint": "Bryanston, Johannesburg, South Africa"},
    "FOURWAYS":              {"metro": "Gauteng", "geocode_hint": "Fourways, Johannesburg, South Africa"},
    "BEDFORDVIEW":           {"metro": "Gauteng", "geocode_hint": "Bedfordview, Gauteng, South Africa"},
    "GARSFONTEIN":           {"metro": "Gauteng", "geocode_hint": "Garsfontein, Pretoria, South Africa"},
    "WATERKLOOF RIDGE":      {"metro": "Gauteng", "geocode_hint": "Waterkloof Ridge, Pretoria, South Africa"},
    "MENLO PARK":            {"metro": "Gauteng", "geocode_hint": "Menlo Park, Pretoria, South Africa"},
    "CENTURION CENTRAL":     {"metro": "Gauteng", "geocode_hint": "Centurion, Gauteng, South Africa"},
}

# --- Formula version, so scores are traceable ---
FORMULA_VERSION = "risk-pulse-v1"

# --- SAPS partner data: pilot suburb -> covering police station ---
# Police precincts cover multiple suburbs; mapping is manually verified.
# Stations chosen from the SAPS station-level theft dataset (open.africa).
SUBURB_TO_STATION = {
    "RONDEBOSCH":            "CLAREMONT",
    "SOMERSET WEST":         "SOMERSET WEST",
    "CAPE TOWN CITY CENTRE":  "CAPE TOWN CENTRAL",
    "CLAREMONT":             "CLAREMONT",
    "SEA POINT":             "CAPE TOWN CENTRAL",
    "NEWLANDS":              "CLAREMONT",
    "TABLE VIEW":            "TABLE VIEW",
    "BRYANSTON":             "SANDTON",
    "FOURWAYS":              "DOUGLASDALE",
    "BEDFORDVIEW":           "GERMISTON",
    "GARSFONTEIN":           "BROOKLYN",
    "WATERKLOOF RIDGE":      "BROOKLYN",
    "MENLO PARK":            "BROOKLYN",
    "CENTURION CENTRAL":     "LYTTELTON",
}

# How much SAPS influences the blended score (0.3 = 30% SAPS, 70% claims)
SAPS_BLEND_WEIGHT = 0.30

PARTNER_DIR = CLAIMS_DIR / "data" / "partner"