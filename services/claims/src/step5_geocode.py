# Step 5: Geocode the 14 unique pilot suburbs ONCE and cache to file.
# Reads:  config.PILOT_SUBURBS
# Writes: data/curated/step5_geocoded.csv  (suburb, metro, lat, lon, confidence, needs_review)
#
# Uses free OpenStreetMap Nominatim. Later swap to Amazon Location; output shape stays same.
# Nominatim policy: max 1 request/second, must set a user-agent.

import time
import pandas as pd
from geopy.geocoders import Nominatim
import config

def geocode():
    cache = config.CURATED_DIR / "step5_geocoded.csv"
    if cache.exists():
        print("Geocode cache exists, skipping. Delete step5_geocoded.csv to force refresh.")
        return
    geolocator = Nominatim(user_agent="sentinel-mesh-gradhack")
    rows = []

    for suburb, meta in config.PILOT_SUBURBS.items():
        hint = meta["geocode_hint"]
        print(f"Geocoding: {hint}")
        location = geolocator.geocode(hint, country_codes="za", addressdetails=True)

        if location:
            rows.append({
                "SUBURB_CLEAN": suburb,
                "METRO": meta["metro"],
                "lat": location.latitude,
                "lon": location.longitude,
                "matched_address": location.address,
                "needs_review": True,   # human verifies before trusting
            })
            print(f"   -> {location.latitude:.4f}, {location.longitude:.4f}")
        else:
            rows.append({
                "SUBURB_CLEAN": suburb, "METRO": meta["metro"],
                "lat": None, "lon": None,
                "matched_address": "NOT FOUND", "needs_review": True,
            })
            print("   -> NOT FOUND")

        time.sleep(1.1)  # respect Nominatim rate limit

    out = config.CURATED_DIR / "step5_geocoded.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nSaved: {out.name}")
    print("IMPORTANT: open the file and eyeball each matched_address before trusting.")

if __name__ == "__main__":
    geocode()