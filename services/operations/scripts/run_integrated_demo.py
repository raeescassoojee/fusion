from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinel_ops.integrated_demo import run_integrated_demo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metro", default="Gauteng")
    args = parser.parse_args()

    output = run_integrated_demo(args.metro)
    target = Path("outputs") / f"integrated_{args.metro.lower().replace(' ', '_')}.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding="utf-8")

    patrol = output["patrol"]
    print(f"Metro: {output['metro']}")
    print(f"Claims source: {output['data_source']['source']}")
    print(f"Evidence score: {output['evidence']['score']}")
    print(f"Alert priority: {output['alert']['priority']}")
    print(f"Timeline events: {len(output['timeline']['items'])}")
    print(
        "Distance: "
        f"{patrol['baseline']['distance_km']} km → "
        f"{patrol['optimised']['distance_km']} km"
    )
    print(f"Fuel saved: {patrol['fuel_saved_litres']} L")
    print(f"Output: {target.resolve()}")


if __name__ == "__main__":
    main()
