# Build an interactive map of the Risk Pulse hotspots.
# Reads:  data/curated/hotspots.json
# Writes: data/curated/hotspots_map.html  (open in any browser)

import json
import folium
import config

def score_color(score):
    # Red = high risk, orange = medium, green = lower. Simple bands.
    if score >= 70:
        return "red"
    if score >= 50:
        return "orange"
    return "green"

def build_map():
    data = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))
    hotspots = data["hotspots"]

    # Center the map on the average of all pins
    avg_lat = sum(h["location"]["lat"] for h in hotspots) / len(hotspots)
    avg_lon = sum(h["location"]["lon"] for h in hotspots) / len(hotspots)

    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=6, tiles="CartoDB positron")

    for h in hotspots:
        score = h["risk_score"]
        lat, lon = h["location"]["lat"], h["location"]["lon"]

        # Popup with the explainable breakdown
        c = h["components"]
        popup_html = (
            f"<b>{h['suburb']}</b> ({h['metro']})<br>"
            f"Risk score: <b>{score}</b><br>"
            f"Incidents: {h['stats']['incident_count']}<br>"
            f"Total cost: R{h['stats']['total_cost']:,.0f}<br>"
            f"<hr style='margin:4px 0'>"
            f"Frequency: {c['frequency']}<br>"
            f"Severity: {c['severity']}<br>"
            f"Recency: {c['recency']}<br>"
            f"Peak-time: {c['peak_time']}"
        )

        # Circle size scales with score so high-risk pops visually
        folium.CircleMarker(
            location=[lat, lon],
            radius=6 + score / 8,
            color=score_color(score),
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{h['suburb']}: {score}",
        ).add_to(m)

    out = config.CURATED_DIR / "hotspots_map.html"
    m.save(str(out))
    print(f"Map saved: {out}")
    print("Open it in a browser (double-click the file, or right-click > Open in browser).")

if __name__ == "__main__":
    build_map()