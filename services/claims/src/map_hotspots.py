# Interactive map of enriched hotspots: risk (color), confidence (border),
# blended score, drivers, SAPS corroboration, all in the popup.
# Reads:  data/curated/hotspots.json
# Writes: data/curated/hotspots_map.html

import json
import folium
import config

def risk_color(score):
    if score >= 70: return "#c0392b"   # red
    if score >= 50: return "#e67e22"   # orange
    return "#27ae60"                    # green

def confidence_border(band):
    return {"HIGH": "#1a5276", "MEDIUM": "#7d6608", "LOW": "#7b241c"}.get(band, "#555")

def build_map():
    data = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))
    hotspots = data if isinstance(data, list) else data["hotspots"]

    avg_lat = sum(h["latitude"] for h in hotspots) / len(hotspots)
    avg_lon = sum(h["longitude"] for h in hotspots) / len(hotspots)
    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=6, tiles="CartoDB positron")

    for h in hotspots:
        risk = h["risk_score"]
        blended = h.get("blended_risk_score", risk)
        conf = h.get("confidence", {})
        saps = h.get("saps", {})
        pw = h.get("peak_window") or {}

        popup = (
            f"<b>{h['hotspot_id']} &middot; {h['name']}</b> ({h.get('metro','')})<br>"
            f"<hr style='margin:4px 0'>"
            f"Claims risk: <b>{risk}</b><br>"
            f"Blended (with SAPS): <b>{blended}</b><br>"
            f"Confidence: <b>{conf.get('score','?')}</b> ({conf.get('band','?')})<br>"
            f"Primary driver: {h.get('primary_driver','?')}<br>"
            f"Main peril: {h.get('main_peril','?')}<br>"
            f"SAPS corroborated: {'YES' if saps.get('corroborated') else 'no'} "
            f"(station {saps.get('station','?')}, {saps.get('theft_incidents','?')} thefts)<br>"
            f"Peak: {pw.get('day','?')} {pw.get('start','')}-{pw.get('end','')}<br>"
            f"Incidents: {h['claim_count']} | Value: R{h['total_claim_value']:,.0f}"
        )

        folium.CircleMarker(
            location=[h["latitude"], h["longitude"]],
            radius=6 + risk / 8,
            color=confidence_border(conf.get("band")),  # border = confidence
            weight=3,
            fill=True,
            fill_color=risk_color(risk),                 # fill = risk
            fill_opacity=0.75,
            popup=folium.Popup(popup, max_width=280),
            tooltip=f"{h['name']}: risk {risk}, conf {conf.get('band','?')}",
        ).add_to(m)

    # Simple legend
    legend = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999;
         background: white; padding: 10px 14px; border: 1px solid #ccc;
         border-radius: 6px; font: 12px sans-serif; line-height: 1.6">
      <b>Fill = risk</b><br>
      <span style="color:#c0392b">&#9679;</span> high (70+)&nbsp;
      <span style="color:#e67e22">&#9679;</span> med (50-69)&nbsp;
      <span style="color:#27ae60">&#9679;</span> lower<br>
      <b>Border = confidence</b><br>
      <span style="color:#1a5276">&#9679;</span> high&nbsp;
      <span style="color:#7d6608">&#9679;</span> medium&nbsp;
      <span style="color:#7b241c">&#9679;</span> low
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    out = config.CURATED_DIR / "hotspots_map.html"
    m.save(str(out))
    print(f"Map saved: {out.name}")
    print("Open it in a browser to see risk (fill) + confidence (border).")

if __name__ == "__main__":
    build_map()