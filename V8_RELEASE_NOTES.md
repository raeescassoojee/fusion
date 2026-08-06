# Sentinel Final V8

Final client heatmap and report refinements:

- All three client house dots remain visible at 100% opacity.
- House dots remain purple during normal and watch states.
- A house dot turns red only when that camera is linked to a confirmed active intruder.
- Direction arrows now appear between consecutive confirmed camera handoffs, for example 10 Ness Avenue to 12 Ness Avenue.
- Incident reports rebuild their logs from the database on every request.
- Report logs are limited to the selected incident window and display newest events first, preventing old test runs from continually extending the report.
