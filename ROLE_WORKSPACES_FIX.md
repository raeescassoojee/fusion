# Role workspace fix

## What was wrong

The dashboard had three role buttons, but the role menus still exposed screens that did not match the intended users. In particular:

- Members could open the network-wide overview and camera wall.
- Security partners could open camera details and a general overview containing claim-value metrics.
- Fraud investigators could access live member-camera controls.
- The application opened directly as the fraud user instead of making the role boundary obvious.

That made the product look like one dashboard with hidden tabs rather than three genuinely different user experiences.

## What now displays

### Discovery member

Landing screen: **My property**

Visible:

- Register an opted-in phone or doorbell camera.
- View only the member's registered cameras.
- View suburb-level risk patterns, busiest times and common perils.
- Use **Live AI** to turn a phone into a temporary community sensor.
- View an explicitly illustrative Vitality Secure contribution status.

Hidden:

- Other households and the network-wide camera wall.
- Individual claim files and values.
- Evidence patterns, movement corridors and patrol intelligence.

### Fraud and claims investigator

Landing screen: **Claims**

Visible:

- Search all claims in the supplied workbook.
- Open a traceable analytical report for a claim.
- Compare evidence.
- Reconstruct the incident timeline.
- View anonymous repeat-evidence patterns.
- View aggregate corridor heat and movement trails.

Hidden:

- Member camera registration and live camera controls.
- Security-partner site administration and patrol workspace.

### Security partner

Landing screen: **Shift briefing**

Visible:

- AI-generated hotspot and shift briefing.
- Coverage gaps.
- Risk-weighted patrol optimisation.
- Distance, fuel and protected-risk metrics.
- Add partner-nominated patrol or coverage locations.

Hidden:

- Claim amounts and individual claim files.
- Member identities and household camera details.
- Raw evidence media.

## UI changes

- Added a clear three-card workspace chooser before entering operations.
- Each role now has a strict tab allow-list and a role-specific landing page.
- The global metro selector is shown only to security partners, where it is operationally relevant.
- Added a visible note that the role selector is a hackathon demonstration, not production authentication.
- Added a member Community Watch and provisional Vitality Secure concept panel.
- Updated `/api/roles` so its contract matches the dashboard.

## Security boundary

The current selector controls **display and demo workflow**. It is not a substitute for authentication. A production deployment must enforce the same scopes using authenticated identities and API authorisation, such as AWS Cognito groups and backend RBAC.

## Validation completed

- Camera AI and operations test suites: **23 tests passed**.
- New role contract and role UI tests: passed.
- Full backend integration: passed.
- JavaScript syntax check: passed.
- Python compilation: passed.
- Live HTTP checks passed for:
  - `/health`
  - `/api/roles`
  - `/api/member/summary`
  - `/api/member/cameras`
  - `/api/fraud/claims`
  - `/api/fraud/claims/{id}/report`
  - `/api/security/briefing`
  - `/api/security/sites`
  - `/dashboard`
