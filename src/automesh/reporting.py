"""Run reports.

Two artefacts: ``result.json`` for machines and ``report.md`` for the human
who has to decide whether to trust the mesh.  The report's job is to make the
agent's reasoning auditable - which parameters it chose and why, what failed,
what it did about it, and what the final quality actually is.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .models import QualityReport, RunResult
from .units import format_length, resolve_display_unit

_VERDICT_TR = {
    "good": "iyi",
    "acceptable": "kabul edilebilir",
    "poor": "zayıf",
    "unusable": "kullanılamaz",
}

_STATUS_TR = {
    "success": "başarılı",
    "failed": "başarısız",
    "repaired": "onarıldı",
    "aborted": "durduruldu",
}


def write_reports(agent: Any, result: RunResult) -> None:
    path = os.path.join(agent.run_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False)

    markdown = build_markdown(agent, result)
    with open(os.path.join(agent.run_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown)


# --------------------------------------------------------------------------

def build_markdown(agent: Any, result: RunResult) -> str:
    metrics = agent.metrics
    plan = agent.plan
    # Raporda okunabilirlik için gösterim birimi; Fluent'e bildirilen
    # içe aktarma birimi (plan.length_unit) bundan bağımsızdır.
    unit = resolve_display_unit(agent.cfg.output.display_unit, metrics.diagonal)
    lines: List[str] = []

    status = "BAŞARILI" if result.success else "BAŞARISIZ"
    lines.append("# AutoMesh raporu - {0}".format(os.path.basename(agent.geometry_path)))
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append("| Durum | **{0}** |".format(status))
    lines.append("| Sonuç | {0} |".format(result.message or "-"))
    lines.append("| Süre | {0:.1f} s |".format(result.duration_s))
    lines.append("| Deneme sayısı | {0} |".format(len(result.attempts)))
    lines.append("| Çalışma dizini | `{0}` |".format(agent.run_dir))
    if result.mesh_file:
        lines.append("| Mesh dosyası | `{0}` |".format(result.mesh_file))
    lines.append("")

    # ---- geometry ------------------------------------------------------
    lines.append("## 1. Geometri analizi")
    lines.append("")
    lines.append("| Ölçüm | Değer |")
    lines.append("|---|---|")
    lines.append("| Analiz yöntemi | `{0}` |".format(metrics.analyzer))
    dx, dy, dz = metrics.bbox.sizes
    lines.append("| Sınır kutusu | {0} x {1} x {2} |".format(
        format_length(dx, unit), format_length(dy, unit), format_length(dz, unit)))
    lines.append("| Köşegen | {0} |".format(format_length(metrics.diagonal, unit)))
    if metrics.volume:
        lines.append("| Hacim | {0:.6g} m³ |".format(metrics.volume))
    if metrics.area:
        lines.append("| Yüzey alanı | {0:.6g} m² |".format(metrics.area))
    lines.append("| Gövde / yüzey / kenar | {0} / {1} / {2} |".format(
        metrics.body_count, metrics.face_count, metrics.edge_count))
    lines.append("| En küçük özellik | {0} |".format(
        format_length(metrics.min_feature_size, unit)))
    lines.append("| Özellik aralığı | 1 : {0:.0f} |".format(metrics.feature_span))
    lines.append("| Eğrisel yüzey oranı | {0:.0%} |".format(metrics.curved_face_ratio))
    lines.append("| Su geçirmez | {0} |".format(_yesno(metrics.watertight)))
    lines.append("| İç akış olarak değerlendirildi | {0} |".format(
        _yesno(metrics.is_internal_flow)))
    lines.append("| Karmaşıklık skoru | {0:.2f} / 1.00 |".format(metrics.complexity()))
    lines.append("")
    if metrics.warnings:
        lines.append("**Geometri uyarıları**")
        lines.append("")
        for warning in metrics.warnings:
            lines.append("- {0}".format(warning))
        lines.append("")

    # ---- plan ----------------------------------------------------------
    lines.append("## 2. Seçilen mesh parametreleri")
    lines.append("")
    lines.append("| Parametre | Değer |")
    lines.append("|---|---|")
    lines.append("| Akış | {0} |".format(plan.workflow.value))
    lines.append("| Gösterim birimi | {0} |".format(unit))
    lines.append("| Fluent içe aktarma birimi | {0} |".format(plan.length_unit))
    lines.append("| Minimum hücre boyutu | {0} |".format(format_length(plan.min_size, unit)))
    lines.append("| Maksimum hücre boyutu | {0} |".format(format_length(plan.max_size, unit)))
    lines.append("| Büyüme oranı | {0:.3f} |".format(plan.growth_rate))
    lines.append("| Boyut fonksiyonu | {0} |".format(plan.size_function.value))
    lines.append("| Eğrilik normal açısı | {0:.1f}° |".format(plan.curvature_normal_angle))
    lines.append("| Boşluk başına hücre | {0:.1f} |".format(plan.cells_per_gap))
    lines.append("| Hacim doldurma | {0} |".format(plan.volume_fill.value))
    bl = plan.boundary_layer
    if bl.enabled and bl.layer_count:
        lines.append("| Sınır tabakası | {0} katman, {1}, büyüme {2:.2f} |".format(
            bl.layer_count, bl.offset_method.value, bl.growth_rate))
        if bl.first_height:
            lines.append("| İlk katman yüksekliği | {0} |".format(
                format_length(bl.first_height, unit)))
        if bl.y_plus_target:
            lines.append("| Hedef y+ | {0:g} |".format(bl.y_plus_target))
    else:
        lines.append("| Sınır tabakası | kapalı |")
    if plan.estimated_cell_count:
        lines.append("| Tahmini hücre sayısı | {0:,} |".format(plan.estimated_cell_count))
    lines.append("")
    if plan.notes:
        lines.append("**Planlama notları**")
        lines.append("")
        for note in plan.notes:
            lines.append("- {0}".format(note))
        lines.append("")

    # ---- quality -------------------------------------------------------
    lines.append("## 3. Mesh kalitesi")
    lines.append("")
    final = result.final_quality
    if final:
        lines.extend(_quality_table(final, agent.cfg))
    else:
        lines.append("Kalite ölçümü yapılamadı.")
    lines.append("")

    # ---- attempts ------------------------------------------------------
    lines.append("## 4. Deneme geçmişi")
    lines.append("")
    if not result.attempts:
        lines.append("Hiç deneme kaydedilmedi.")
    for attempt in result.attempts:
        lines.append("### Deneme {0} - {1}".format(
            attempt.get("index"), _STATUS_TR.get(attempt.get("status"), attempt.get("status"))))
        lines.append("")
        plan_data = attempt.get("plan") or {}
        lines.append("- Parametreler: min={0:.4g} m, max={1:.4g} m, büyüme={2:.3f}, "
                     "{3} katman, {4}, {5}".format(
                         plan_data.get("min_size", 0.0), plan_data.get("max_size", 0.0),
                         plan_data.get("growth_rate", 0.0),
                         (plan_data.get("boundary_layer") or {}).get("layer_count", 0),
                         plan_data.get("volume_fill", "-"), plan_data.get("workflow", "-")))
        for key, title in (("surface_quality", "Yüzey"), ("volume_quality", "Hacim")):
            data = attempt.get(key)
            if data:
                lines.append("- {0} kalitesi: {1} ({2})".format(
                    title, _quality_line(data),
                    _VERDICT_TR.get(data.get("verdict"), data.get("verdict"))))
        failed_steps = [s for s in attempt.get("steps") or [] if not s.get("ok")]
        if failed_steps:
            lines.append("- Başarısız adımlar: {0}".format(
                ", ".join(s.get("name", "?") for s in failed_steps)))
        for diagnosis in attempt.get("diagnoses") or []:
            lines.append("- **Teşhis `{0}`** - {1}".format(
                diagnosis.get("rule_id"), diagnosis.get("title")))
            explanation = (diagnosis.get("explanation") or "").replace("\n", " ")
            if explanation:
                lines.append("  - {0}".format(explanation))
            if diagnosis.get("evidence"):
                lines.append("  - Kanıt: `{0}`".format(diagnosis["evidence"]))
        for action in attempt.get("actions_applied") or []:
            lines.append("  - Uygulandı: {0}".format(action))
        if attempt.get("error"):
            lines.append("- Hata: {0}".format(attempt["error"]))
        lines.append("")

    # ---- files ---------------------------------------------------------
    lines.append("## 5. Üretilen dosyalar")
    lines.append("")
    for name, description in (
        ("analysis.json", "geometri metrikleri"),
        ("result.json", "makine okunur sonuç"),
        ("transcript.log", "Fluent çıktısının tamamı"),
        ("journal.py", "aynı işlemleri tekrarlayan PyFluent günlüğü"),
        ("automesh.log", "agent günlüğü"),
    ):
        path = os.path.join(agent.run_dir, name)
        if os.path.exists(path):
            lines.append("- `{0}` - {1}".format(name, description))
    if result.mesh_file and os.path.exists(result.mesh_file):
        lines.append("- `{0}` - mesh".format(
            os.path.relpath(result.mesh_file, agent.run_dir)))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------

def _yesno(value: Optional[bool]) -> str:
    if value is None:
        return "bilinmiyor"
    return "evet" if value else "hayır"


def _quality_line(data: Dict[str, Any]) -> str:
    bits = []
    if data.get("max_skewness") is not None:
        bits.append("skewness_max={0:.4f}".format(data["max_skewness"]))
    if data.get("min_orthogonal_quality") is not None:
        bits.append("ortho_min={0:.4f}".format(data["min_orthogonal_quality"]))
    if data.get("max_aspect_ratio") is not None:
        bits.append("AR_max={0:.1f}".format(data["max_aspect_ratio"]))
    if data.get("cell_count"):
        bits.append("{0:,} hücre".format(data["cell_count"]))
    return ", ".join(bits) or "-"


def _quality_table(data: Dict[str, Any], cfg: Any) -> List[str]:
    thresholds = cfg.quality
    rows = [
        ("Maksimum skewness", data.get("max_skewness"),
         "< {0:.2f} hedef, < {1:.2f} kabul".format(
             thresholds.max_skewness_good, thresholds.max_skewness_accept)),
        ("Minimum orthogonal quality", data.get("min_orthogonal_quality"),
         "> {0:.2f} hedef, > {1:.2f} kabul".format(
             thresholds.min_orthogonal_good, thresholds.min_orthogonal_accept)),
        ("Maksimum aspect ratio", data.get("max_aspect_ratio"),
         "< {0:.0f} kabul".format(thresholds.max_aspect_ratio_accept)),
    ]
    lines = ["| Metrik | Değer | Eşik |", "|---|---|---|"]
    for label, value, threshold in rows:
        shown = "{0:.4f}".format(value) if isinstance(value, (int, float)) else "-"
        lines.append("| {0} | {1} | {2} |".format(label, shown, threshold))
    if data.get("cell_count"):
        lines.append("| Hücre sayısı | {0:,} | - |".format(data["cell_count"]))
    verdict = _VERDICT_TR.get(data.get("verdict"), data.get("verdict"))
    lines.append("")
    lines.append("**Genel değerlendirme: {0}**".format(verdict))
    failed = data.get("failed_metrics") or []
    if failed:
        lines.append("")
        for item in failed:
            lines.append("- {0}".format(item))
    return lines
