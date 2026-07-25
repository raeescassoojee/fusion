import argparse, json
from sentinel_ops.adapters import profile_discovery_claims
parser = argparse.ArgumentParser()
parser.add_argument("workbook")
args = parser.parse_args()
print(json.dumps(profile_discovery_claims(args.workbook), indent=2))
