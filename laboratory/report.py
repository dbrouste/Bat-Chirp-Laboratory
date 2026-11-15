"""Offline HTML report: all assets are embedded, no web access required."""

from __future__ import annotations

from html import escape
from pathlib import Path


def write_html(report: dict, path: Path) -> None:
    import plotly.graph_objects as go

    details = report.get("details", [])
    fig = go.Figure()
    for matched, label, colour in ((True, "Suivi", "#0b8177"),
                                    (False, "Non suivi", "#d65a51")):
        subset = [row for row in details
                  if bool(row.get("tracking_success")) == matched and
                  row.get("snr_max_db") is not None]
        fig.add_trace(go.Scattergl(
            x=[row["snr_max_db"] for row in subset],
            y=[row.get("mean_normalized_error") if matched else 0
               for row in subset],
            mode="markers", name=label,
            marker={"color": colour, "size": 8},
            text=[f'{row["file"]} : {row["chirp_id"]}' for row in subset],
            hovertemplate="%{text}<br>SNR : %{x:.1f} dB"
                          "<br>Erreur normalisée : %{y:.2f}<extra></extra>",
        ))
    fig.add_hline(y=1.0, line_dash="dash", line_color="gray")
    fig.update_layout(title="Chirps manuels validés · SNR / erreur du suivi",
                      xaxis_title="SNR manuel (dB)",
                      yaxis_title="Erreur normalisée (0 = non suivi si rouge)")
    totals = report["totals"]
    geometry = report.get("metadata", {}).get("scoring_protocol") == "square_normals_v1"
    summary = [("WAV évalués", totals["files_evaluated"]),
               ("Chirps manuels", totals["annotated"]),
               ("Candidats", totals["candidates"]),
               ("Détectés", totals["matched"]),
               ("Suivis", totals["tracked"]),
               ("Écart normal moyen (kHz_eq)", totals.get("mean_geometric_error_khz_eq"))
               if geometry else ("Écart vertical moyen (kHz)",
                                 totals.get("mean_tracking_error_khz")),
               ("Couverture des normales", totals.get("mean_geometric_coverage_fraction"))
               if geometry else ("Couverture temporelle", None),
               ("Rappel pondéré", totals["weighted_detector_recall"]),
               ("Suivi tolérance pondéré", totals[
                   "weighted_full_pipeline_within_tolerance_fraction"])]
    neg = totals.get("reviewed_no_chirp", {})
    if neg.get("wav_count", 0):
        summary.extend([
            ("WAV revus No chirp", neg["wav_count"]),
            ("WAV No chirp avec candidats", neg["wavs_with_candidate_alert"]),
            ("Alertes candidates sur No chirp", neg["candidate_alerts"]),
            ("Fraction des WAV No chirp avec alerte", neg["wav_false_alarm_fraction"]),
            ("Candidats par WAV No chirp", neg["candidate_alerts_per_wav"]),
            ("Alertes candidates par heure No chirp", neg["candidate_alerts_per_hour"]),
        ])
    rows = "".join(f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
                   for key, value in summary)
    bad = "".join(f"<li>{escape(str(x['file']))}: "
                  f"{escape(str(x['error']))}</li>"
                  for x in report["per_file"] if "error" in x)
    plot = fig.to_html(full_html=False, include_plotlyjs=True)
    html = ("<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            "<title>Chirp laboratory</title><style>body{font:16px system-ui;"
            "margin:2rem auto;max-width:1100px;color:#203040}table{border-collapse:collapse}"
            "th,td{padding:.5rem 1rem;border-bottom:1px solid #ddd;text-align:left}"
            "</style></head><body><h1>Laboratoire de chirps</h1>"
            f"<p>Protocole : {escape(str(report.get('metadata', {}).get('scoring_protocol')))}. "
            "Le score géométrique utilise les distances perpendiculaires en kHz_eq "
            "et une couverture explicite ; les seuils SNR restent exploratoires. "
            "Les chirps non suivis ont une ordonnée zéro uniquement pour l'affichage.</p>"
            "<p>Les WAV positifs ne sont pas annotés exhaustivement. Seuls "
            "les WAV explicitement revus No chirp fournissent des exemples "
            "négatifs. Les alertes sur ces WAV comptent des candidats du "
            "détecteur, pas des trajectoires entièrement reconstruites. "
            "La précision globale demeure inconnue.</p>"
            f"<table>{rows}</table>{plot}<h2>WAV en erreur</h2><ul>{bad}</ul>"
            "</body></html>")
    path.write_text(html, encoding="utf-8")
