# Run the entire claims pipeline in order, start to finish.
# Usage: python run_all.py

import step1_ingest
import step2_validate
import step3_clean
import step4_pilot
import step5_geocode
import step6_riskpulse
import step7_publish

STEPS = [
    ("1. Ingest",     step1_ingest.ingest),
    ("2. Validate",   step2_validate.validate),
    ("3. Clean",      step3_clean.clean),
    ("4. Pilot",      step4_pilot.select_pilot),
    ("5. Geocode",    step5_geocode.geocode),
    ("6. Risk Pulse", step6_riskpulse.risk_pulse),
    ("7. Publish",    step7_publish.publish),
]

if __name__ == "__main__":
    for name, fn in STEPS:
        print(f"\n{'='*50}\n{name}\n{'='*50}")
        fn()
    print("\nPipeline complete. Output: data/curated/hotspots.json")