"""Run all non-destructive SAPS enhancements on the existing hotspot output.

The copy-over bundle includes audited prepared CSVs, so the default command works
without the large raw SAPS workbooks. Pass ``--rebuild-source`` only after copying
the archive's ``annual`` and ``quarterly`` directories into ``data/partner``.
"""

import argparse

import prepare_saps_2025_2026
import prepare_saps_history
import step8_fuse_saps
import step9_feature_analysis
import step10_fuse_saps_typed
import step11_add_saps_history
import route_inputs
import audit_saps_outputs


def run(*, rebuild_source: bool = False):
    if rebuild_source:
        prepare_saps_2025_2026.prepare()
        prepare_saps_history.prepare()
    step8_fuse_saps.fuse()
    step9_feature_analysis.analyse()
    step10_fuse_saps_typed.fuse_typed()
    step11_add_saps_history.add_history()
    route_inputs.build_route_inputs()
    audit_saps_outputs.audit()
    try:
        import map_hotspots
        map_hotspots.build_map()
    except ModuleNotFoundError as exc:
        if exc.name not in {"folium", "geopy"}:
            raise
        print(f"Map refresh skipped because optional package '{exc.name}' is not installed.")
        print("The supplied hotspots_map.html remains available.")
    print("\nEnhancement complete. Existing claims data and original SAPS files were preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rebuild-source",
        action="store_true",
        help="Rebuild prepared CSVs from the raw annual and quarterly workbooks first.",
    )
    args = parser.parse_args()
    run(rebuild_source=args.rebuild_source)
