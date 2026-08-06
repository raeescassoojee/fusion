# Sentinel Final V9

- Added a compact dedicated backend WhatsApp sending block to the Security workspace.
- Clicking any specific patrol unit now shows a route to the current active incident.
- A newly detected intruder opens an Incident Time Machine report-ready popup with direct download.
- Updated the featured vehicle from Porsche Cayenne to Mazda CX-3.

## Security response desk route and WhatsApp refinement

- Restored a dedicated **Optimize route** control for every primary and backup response vehicle inside each incoming alert.
- Replaced the always-visible WhatsApp badge with an emergency-only backend activity log tied to the latest active dispatch.
- Removed straight-line transit dots from emergency routes so only the road route, start point and incident destination are shown.
- Synced the selected responding vehicle's map movement to the same Mapbox road geometry used by the blue route.
- Preserved the selected backup-unit route during live security refreshes.

## Live-unit patrol routing and integrated WhatsApp log

- Every live unit can now be selected and routed without requiring an active incident.
- Units not attached to an emergency show an optimised patrol preview from their latest position.
- Added an **Optimize route** action directly to the live-unit detail card.
- Removed the fallback that incorrectly attached every selected unit to the newest emergency.
- Moved the emergency-only WhatsApp backend activity log beneath the main response workspace and restyled it as part of the dashboard.

## Test-alert reliability patch

- Restored one-click test-alert creation on a fresh installation with no prior camera detections.
- The Security control now creates an anonymous seeded sighting at 10 Ness Avenue only when the camera history is empty, then continues through the normal Member incident, neighbour watch, WhatsApp, routing and report flow.
- Existing live incidents and real stored sightings remain the preferred source whenever available.
