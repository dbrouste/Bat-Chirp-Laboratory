"""Offline HTML report: all assets are embedded, no web access required."""

from __future__ import annotations

from html import escape
from pathlib import Path


def write_html(report: dict, path: Path) -> None:
    import plotly.graph_objects as go

    details = report.get("details", [])
    geometry_mode = report["metadata"].get("scoring") == "geometry"
    fig = go.Figure()
    for matched, label, colour in ((True, "Apparié", "#0b8177"),
                                    (False, "Non apparié", "#d65a51")):
        subset = [row for row in details
                  if bool(row.get("detected" if geometry_mode else
                                  "tracking_success")) == matched and
                  row.get("snr_max_db") is not None]
        fig.add_trace(go.Scattergl(
            x=[row["snr_max_db"] for row in subset],
            y=[row.get("score", 0.0) if geometry_mode else
               (row.get("mean_normalized_error") if matched else 0)
               for row in subset],
            mode="markers", name=label,
            marker={"color": colour, "size": 8},
            text=[f'{row["file"]} : {row["chirp_id"]}' for row in subset],
            hovertemplate=("%{text}<br>SNR : %{x:.1f} dB"
                           "<br>Score : %{y:.1f}/100<extra></extra>"
                           if geometry_mode else
                           "%{text}<br>SNR : %{x:.1f} dB"
                           "<br>Erreur normalisée : %{y:.2f}<extra></extra>"),
        ))
    if not geometry_mode:
        fig.add_hline(y=1.0, line_dash="dash", line_color="gray")
    fig.update_layout(title=("Chirps manuels validés · SNR / score géométrique"
                             if geometry_mode else
                             "Chirps manuels validés · SNR / erreur du suivi"),
                      xaxis_title="SNR manuel (dB)",
                      yaxis_title=("Score géométrique (/100)" if geometry_mode else
                                   "Erreur normalisée (0 = non suivi si rouge)"))
    totals = report["totals"]
    summary = [("WAV évalués", totals["files_evaluated"]),
               ("Chirps manuels", totals["annotated"]),
               ("Candidats", totals["candidates"]),
               ("Appariés" if geometry_mode else "Détectés", totals["matched"]),
               ("Suivis", totals["tracked"])]
    if geometry_mode:
        summary.extend((("Rappel pondéré SNR", totals["weighted_recall"]),
                        ("Score géométrique pondéré (0–1)",
                         totals["weighted_trajectory_score"]),
                        ("Faux positifs (si annotations exhaustives)",
                         totals["false_positive_count"])))
    else:
        summary.extend((("Rappel pondéré", totals["weighted_detector_recall"]),
                        ("Suivi tolérance pondéré", totals[
                            "weighted_full_pipeline_within_tolerance_fraction"])))
    rows = "".join(f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
                   for key, value in summary)
    bad = "".join(f"<li>{escape(str(x['file']))}: "
                  f"{escape(str(x['error']))}</li>"
                  for x in report["per_file"] if "error" in x)
    plot = fig.to_html(full_html=False, include_plotlyjs=True)
    description = ("Score géométrique v1 : normales locales, pénalités de couverture "
                   "et de prolongement. Seuils à calibrer sur annotations réelles."
                   if geometry_mode else
                   "Benchmark existant SNR/trajectoire. Les chirps non suivis ont "
                   "une ordonnée zéro uniquement pour l'affichage.")
    html = ("<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            "<title>Chirp laboratory</title><style>body{font:16px system-ui;"
            "margin:2rem auto;max-width:1100px;color:#203040}table{border-collapse:collapse}"
            "th,td{padding:.5rem 1rem;border-bottom:1px solid #ddd;text-align:left}"
            "</style></head><body><h1>Laboratoire de chirps</h1>"
            f"<p>{escape(description)}</p>"
            f"<table>{rows}</table>{plot}<h2>WAV en erreur</h2><ul>{bad}</ul>"
            "</body></html>")
    path.write_text(html, encoding="utf-8")
