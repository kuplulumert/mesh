"""The rule base: Fluent says X -> do Y.

Each rule carries an escalation ladder.  The first time a symptom appears the
agent tries the cheapest fix that could work (usually an in-place repair);
if the same symptom comes back it moves up a step (change the sizing), and
eventually to the structural fix (change the workflow, drop the prisms).
That escalation is what stops the loop from repeating a failed remedy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .actions import (
    Action,
    Diagnosis,
    Step,
    abort_action,
    plan_action,
    retry_action,
    surface_action,
    volume_action,
)

# Stages a rule can apply to.
ANY = "any"
IMPORT = "import"
SURFACE = "surface"
BOUNDARY_LAYER = "boundary-layer"
VOLUME = "volume"
QUALITY = "quality"

SEV_FATAL = "fatal"
SEV_ERROR = "error"
SEV_WARNING = "warning"


@dataclass
class Rule:
    id: str
    title: str
    patterns: Sequence[str]
    explanation: str
    steps: Sequence[Step]
    stage: str = ANY
    severity: str = SEV_ERROR
    priority: int = 50                       # lower runs first
    anti_patterns: Sequence[str] = field(default_factory=tuple)

    def compiled(self) -> List[re.Pattern]:
        if not hasattr(self, "_compiled"):
            object.__setattr__(self, "_compiled",
                               [re.compile(p, re.IGNORECASE) for p in self.patterns])
        return getattr(self, "_compiled")

    def anti_compiled(self) -> List[re.Pattern]:
        if not hasattr(self, "_anti"):
            object.__setattr__(self, "_anti",
                               [re.compile(p, re.IGNORECASE) for p in self.anti_patterns])
        return getattr(self, "_anti")

    def match(self, text: str, stage: str) -> Optional[str]:
        """Return the matching line as evidence, or ``None``."""
        if self.stage != ANY and stage != ANY and self.stage != stage:
            return None
        for pattern in self.anti_compiled():
            if pattern.search(text):
                return None
        for pattern in self.compiled():
            found = pattern.search(text)
            if not found:
                continue
            start = text.rfind("\n", 0, found.start()) + 1
            end = text.find("\n", found.end())
            end = len(text) if end == -1 else end
            return text[start:end].strip()[:400]
        return None

    def step_for(self, occurrence: int) -> Tuple[Step, bool]:
        """The remedy for the ``occurrence``-th time this rule fires."""
        index = max(0, occurrence - 1)
        exhausted = index >= len(self.steps)
        return self.steps[min(index, len(self.steps) - 1)], exhausted


def _step(*actions: Action, note: str = "") -> Step:
    return Step(actions=list(actions), note=note)


# --------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------

RULES: List[Rule] = [

    # ---- unrecoverable ------------------------------------------------
    Rule(
        id="license",
        title="Lisans hatası",
        priority=1,
        severity=SEV_FATAL,
        patterns=(r"\blicen[sc]e\b.*(error|fail|denied|not available|expired)",
                  r"flexlm", r"could not (check ?out|obtain).*licen[sc]e",
                  r"no licen[sc]es? available"),
        explanation=(
            "Fluent Meshing lisansı alınamadı. ANSYS lisans sunucusu erişilebilir mi, "
            "aynı anda başka bir oturum lisansı tutuyor olabilir mi kontrol edin."),
        steps=(_step(abort_action("Lisans sorunu otomatik çözülemez.")),),
    ),
    Rule(
        id="file_missing",
        title="Geometri dosyası okunamadı",
        priority=2,
        severity=SEV_FATAL,
        stage=IMPORT,
        patterns=(r"no such file or directory", r"file not found",
                  r"cannot open (the )?file", r"unable to (open|read) (the )?file",
                  r"permission denied"),
        explanation=(
            "Fluent geometri dosyasını açamadı. Yol, dosya izinleri ve dosyanın "
            "başka bir program tarafından kilitlenmiş olup olmadığını kontrol edin."),
        steps=(_step(abort_action("Dosya erişimi olmadan devam edilemez.")),),
    ),
    Rule(
        id="cad_unsupported",
        title="CAD formatı okunamadı",
        priority=3,
        severity=SEV_FATAL,
        stage=IMPORT,
        patterns=(r"unsupported (file )?(format|type)", r"unknown file format",
                  r"cad (import|reader) failed", r"failed to (import|load) (the )?(cad|geometry)"),
        explanation=(
            "Fluent bu CAD formatını okuyamadı. SpaceClaim'den STEP (.stp) veya "
            "Parasolid (.x_t) olarak dışa aktarıp tekrar deneyin."),
        steps=(_step(abort_action(
            "Geometriyi STEP olarak dışa aktarıp tekrar çalıştırın.")),),
    ),

    # ---- connection / resources ---------------------------------------
    Rule(
        id="out_of_memory",
        title="Bellek yetersiz",
        priority=5,
        patterns=(r"not enough memory", r"out of memory", r"bad_alloc",
                  r"memory allocation (failed|error)", r"insufficient memory"),
        explanation=(
            "Fluent istenen çözünürlükte mesh için yeterli belleği ayıramadı. "
            "Hücre sayısını düşürmek tek gerçekçi çözüm."),
        steps=(
            _step(plan_action("scale_sizes", "Hücre boyutunu %50 kabalaştır", factor=1.5)),
            _step(plan_action("scale_sizes", "Tekrar %50 kabalaştır", factor=1.5),
                  plan_action("reduce_layers", "Prizma katmanlarını azalt", count=2)),
            _step(plan_action("scale_sizes", "Boyutu iki katına çıkar", factor=2.0),
                  plan_action("disable_boundary_layers", "Prizma katmanlarını kapat")),
        ),
    ),
    Rule(
        id="session_lost",
        title="Fluent oturumu koptu",
        priority=6,
        patterns=(r"connection (lost|closed|refused)", r"grpc.*(unavailable|error)",
                  r"server (terminated|disconnected)", r"fluent.*(crashed|exited unexpectedly)",
                  r"broken pipe"),
        explanation=(
            "Fluent süreci beklenmedik şekilde sonlandı. Genellikle bellek baskısı "
            "veya çok ince bir boyut alanı yüzünden olur."),
        steps=(
            _step(retry_action("Fluent'i yeniden başlatıp aynı planla dene")),
            _step(plan_action("scale_sizes", "Daha kaba bir mesh ile yeniden başlat",
                              factor=1.4)),
        ),
    ),

    # ---- surface mesh --------------------------------------------------
    Rule(
        id="surface_intersecting_faces",
        title="Kesişen yüzeyler",
        priority=10,
        stage=SURFACE,
        patterns=(r"intersecting faces", r"self[- ]intersect", r"faces (that )?intersect",
                  r"overlapping faces"),
        explanation=(
            "Yüzey ağı kendisiyle kesişiyor. Genellikle minimum hücre boyutu dar "
            "bir boşluğu çözemeyecek kadar kaba olduğunda ya da CAD'de üst üste "
            "binen yüzeyler olduğunda görülür."),
        steps=(
            _step(surface_action("improve_surface_mesh",
                                 "Improve Surface Mesh ile yüzeyi onar",
                                 face_quality_limit=0.85, iterations=5)),
            _step(plan_action("scale_min_size", "Minimum boyutu yarıya indir", factor=0.5),
                  plan_action("adjust_cells_per_gap", "Boşluk başına hücreyi artır",
                              delta=1.0)),
            _step(plan_action("switch_to_fault_tolerant",
                              "Kirli geometri için fault-tolerant akışa geç")),
        ),
    ),
    Rule(
        id="free_faces",
        title="Serbest yüzeyler / su geçirmez değil",
        priority=11,
        stage=SURFACE,
        patterns=(r"\bfree faces\b", r"free edges", r"not watertight",
                  r"geometry is not closed", r"open (shell|surface)s? (found|detected)"),
        explanation=(
            "Yüzey ağında kapanmamış kenarlar var; watertight akışı kapalı bir "
            "hacim bulamaz. Ya CAD'deki boşluklar kapatılmalı ya da geometriyi "
            "saran fault-tolerant akışa geçilmeli."),
        steps=(
            _step(surface_action("delete_unused", "Kullanılmayan düğüm/yüzeyleri sil"),
                  surface_action("merge_nodes", "Yakın düğümleri birleştir",
                                 tolerance=0.01)),
            _step(plan_action("switch_to_fault_tolerant",
                              "Boşlukları kapatmak için fault-tolerant akışa geç")),
            _step(abort_action(
                "Geometri fault-tolerant akışla da kapatılamadı; CAD'i SpaceClaim'de "
                "onarmak gerekiyor.")),
        ),
    ),
    Rule(
        id="multi_connected_faces",
        title="Çok bağlantılı yüzeyler",
        priority=12,
        stage=SURFACE,
        patterns=(r"multi[- ]connected faces", r"multiply connected",
                  r"non[- ]manifold"),
        explanation=(
            "Bir kenar ikiden fazla yüzey tarafından paylaşılıyor. Genellikle "
            "birbirine yapışmış ya da çakışan gövdelerden kaynaklanır."),
        steps=(
            _step(surface_action("surface_ladder", "Yüzey onarım merdivenini çalıştır",
                                 quality_limit=0.85)),
            _step(plan_action("switch_to_fault_tolerant",
                              "Fault-tolerant akışa geç (parçaları ayrı ele alır)")),
        ),
    ),
    Rule(
        id="duplicate_nodes",
        title="Çakışan düğümler",
        priority=13,
        stage=SURFACE,
        patterns=(r"duplicate nodes", r"coincident nodes", r"duplicate faces"),
        explanation="Aynı konumda birden fazla düğüm var; birleştirmek gerekiyor.",
        steps=(
            _step(surface_action("merge_nodes", "Düğümleri birleştir", tolerance=0.01)),
            _step(surface_action("merge_nodes", "Toleransı artırarak birleştir",
                                 tolerance=0.05)),
        ),
    ),
    Rule(
        id="zero_area_faces",
        title="Sıfır alanlı / dejenere yüzeyler",
        priority=14,
        stage=SURFACE,
        patterns=(r"zero[- ]area", r"degenerate (face|element)", r"sliver face"),
        explanation=(
            "Geometride çözülemeyecek kadar küçük yüzeyler var. Minimum hücre "
            "boyutunu yükseltmek bu yüzeyleri ağ içinde eritir."),
        steps=(
            _step(plan_action("scale_min_size", "Minimum boyutu iki katına çıkar",
                              factor=2.0)),
            _step(surface_action("surface_ladder", "Collapse ağırlıklı yüzey onarımı",
                                 quality_limit=0.9)),
        ),
    ),
    Rule(
        id="surface_mesh_failed",
        title="Yüzey ağı oluşturulamadı",
        priority=20,
        stage=SURFACE,
        patterns=(r"failed to (generate|create) the surface mesh",
                  r"surface mesh(ing)? failed",
                  r"unable to (generate|create) the surface mesh"),
        anti_patterns=(r"intersecting faces", r"free faces"),
        explanation=(
            "Yüzey ağı üretimi genel bir hatayla durdu. Önce yerinde onarım, "
            "sonra boyutlandırma, en son akış değişikliği denenir."),
        steps=(
            _step(surface_action("improve_surface_mesh", "Yüzey ağını iyileştir",
                                 face_quality_limit=0.9, iterations=5)),
            _step(plan_action("scale_sizes", "Yüzey boyutlarını incelt", factor=0.7),
                  plan_action("adjust_curvature_angle", "Eğrilik çözünürlüğünü artır",
                              delta=-3.0)),
            _step(plan_action("switch_to_fault_tolerant", "Fault-tolerant akışa geç")),
        ),
    ),
    Rule(
        id="sharp_angle",
        title="Keskin açı / özellik açısı uyarısı",
        priority=30,
        severity=SEV_WARNING,
        stage=SURFACE,
        patterns=(r"sharp angle", r"feature angle.*(too|exceed)", r"sharp edges? detected"),
        explanation=(
            "Çok keskin geçişler yüzey ağını bozuyor. Eğrilik açısını gevşetmek "
            "bu bölgelerde aşırı incelmeyi engeller."),
        steps=(
            _step(plan_action("adjust_curvature_angle", "Eğrilik açısını gevşet",
                              delta=4.0)),
        ),
    ),

    # ---- geometry description / regions --------------------------------
    Rule(
        id="no_fluid_region",
        title="Akışkan bölgesi bulunamadı",
        priority=15,
        patterns=(r"no fluid region", r"could not (create|identify) (any )?regions?",
                  r"zero regions", r"no (closed )?volume(s)? (found|detected)"),
        explanation=(
            "Fluent kapalı bir akışkan hacmi bulamadı. Ya geometri tanımı yanlış "
            "(katı + akışkan karışık) ya da açık ağızların kapatılması gerekiyor."),
        steps=(
            _step(plan_action(
                "set_geometry_setup", "Geometriyi katı+akışkan olarak tanımla",
                setup="The geometry consists of both fluid and solid regions and/or voids")),
            _step(plan_action("switch_to_fault_tolerant",
                              "Ağızları kapatmak için fault-tolerant akışa geç")),
        ),
    ),
    Rule(
        id="leakage",
        title="Sızıntı tespit edildi",
        priority=16,
        patterns=(r"\bleak(age)?\b", r"leak path", r"material point.*(outside|leak)"),
        explanation=(
            "Akışkan bölgesi dışarı sızıyor: kapatılmamış bir delik ya da yetersiz "
            "çözünürlükle atlanan ince bir boşluk var."),
        steps=(
            _step(plan_action("scale_min_size", "Sızıntıyı yakalamak için incelt",
                              factor=0.5)),
            _step(plan_action("switch_to_fault_tolerant",
                              "Sızıntı eşiği kontrollü fault-tolerant akışa geç")),
        ),
    ),
    Rule(
        id="nonconformal",
        title="Uyumsuz (non-conformal) arayüz",
        priority=17,
        severity=SEV_WARNING,
        patterns=(r"non[- ]conformal", r"interfaces? (are )?not conformal",
                  r"share topology"),
        explanation=(
            "Gövdeler arasında paylaşılmayan yüzeyler var. Share Topology açmak "
            "genelde çözer."),
        steps=(
            _step(plan_action("set_geometry_setup",
                              "Çok gövdeli kurulum olarak tanımla",
                              setup="The geometry consists of both fluid and solid regions and/or voids")),
        ),
    ),

    # ---- boundary layers ------------------------------------------------
    Rule(
        id="prism_failure",
        title="Prizma (sınır tabaka) katmanları oluşturulamadı",
        priority=18,
        stage=BOUNDARY_LAYER,
        patterns=(r"prism (layer|generation).*(fail|error)",
                  r"prisms? collapsed", r"failed to (grow|generate) (the )?prisms?",
                  r"boundary layer.*(fail|could not)",
                  r"only \d+ of \d+ layers"),
        explanation=(
            "Prizma yığını geometriye sığmıyor: ya toplam yükseklik dar bir "
            "kanaldan büyük, ya da ilk katman çok kalın. Katman sayısını ve ilk "
            "yüksekliği düşürmek doğru sırayla denenir."),
        steps=(
            _step(plan_action("reduce_layers", "Katman sayısını 2 azalt", count=2)),
            _step(plan_action("scale_first_height", "İlk katmanı yarıya indir",
                              factor=0.5),
                  plan_action("reduce_layers", "Bir katman daha azalt", count=1)),
            _step(plan_action("set_bl_method", "Aspect-ratio yöntemine geç",
                              method="aspect-ratio"),
                  plan_action("reduce_layers", "Katman sayısını düşür", count=2)),
            _step(plan_action("disable_boundary_layers",
                              "Prizma katmanlarını tamamen kapat (son çare)")),
        ),
    ),

    # ---- volume mesh -----------------------------------------------------
    Rule(
        id="negative_volume",
        title="Negatif hacimli / geçersiz hücreler",
        priority=19,
        stage=VOLUME,
        patterns=(r"negative (cell )?volume", r"invalid cells", r"left[- ]handed faces",
                  r"inverted (cells|elements)"),
        explanation=(
            "Ağda ters dönmüş hücreler var; çözücü bu ağı başlatamaz. Önce düğüm "
            "hareketi ile düzeltmeye çalışılır, sonra büyüme oranı düşürülür."),
        steps=(
            _step(volume_action("auto_node_move", "Auto node move ile düğümleri düzelt",
                                quality_limit=0.15, iterations=10)),
            _step(plan_action("adjust_growth_rate", "Büyüme oranını düşür", delta=-0.05),
                  plan_action("reduce_layers", "Bir prizma katmanı azalt", count=1)),
            _step(plan_action("downgrade_volume_fill",
                              "Daha toleranslı hacim doldurma tipine geç")),
        ),
    ),
    Rule(
        id="hexcore_failure",
        title="Hexcore oluşturulamadı",
        priority=21,
        stage=VOLUME,
        patterns=(r"hexcore.*(fail|error|could not)", r"failed.*poly-?hexcore",
                  r"cartesian core.*(fail|error)"),
        explanation=(
            "Hexcore çekirdeği geometriye oturmadı. Tampon katmanı artırmak ya da "
            "saf polyhedra'ya geçmek çözer."),
        steps=(
            _step(plan_action("relax_hexcore", "Hexcore tampon katmanını artır")),
            _step(plan_action("set_volume_fill", "Polyhedra'ya geç", fill="polyhedra")),
        ),
    ),
    Rule(
        id="volume_mesh_failed",
        title="Hacim ağı oluşturulamadı",
        priority=25,
        stage=VOLUME,
        patterns=(r"failed to (generate|create) the volume mesh",
                  r"volume mesh(ing)? failed",
                  r"unable to (generate|create) the volume mesh",
                  r"mesh generation (failed|aborted)"),
        anti_patterns=(r"prism", r"negative volume", r"memory", r"hexcore"),
        explanation=(
            "Hacim ağı üretimi durdu. Yerinde onarım, ardından doldurma tipinin "
            "düşürülmesi ve gerekirse kabalaştırma denenir."),
        steps=(
            _step(volume_action("repair_improve", "Ağı onar ve iyileştir")),
            _step(plan_action("downgrade_volume_fill",
                              "Daha toleranslı doldurma tipine geç")),
            _step(plan_action("scale_sizes", "Ağı kabalaştır", factor=1.3),
                  plan_action("reduce_layers", "Prizma katmanlarını azalt", count=2)),
        ),
    ),
    Rule(
        id="too_many_cells",
        title="Hücre sayısı sınırı aşıldı",
        priority=26,
        patterns=(r"exceeds the maximum number of cells", r"too many cells",
                  r"cell (count|limit).*exceed"),
        explanation="İstenen boyut alanı makine için fazla ince.",
        steps=(
            _step(plan_action("scale_sizes", "Hücre boyutunu büyüt", factor=1.5)),
            _step(plan_action("scale_sizes", "Daha da büyüt", factor=1.5)),
        ),
    ),
    Rule(
        id="sliver_cells",
        title="Sliver (iğne) hücreler",
        priority=27,
        stage=VOLUME,
        severity=SEV_WARNING,
        patterns=(r"\bsliver\b", r"degenerate cells", r"very thin cells"),
        explanation="Çok yassı hücreler kalite metriğini aşağı çekiyor.",
        steps=(
            _step(volume_action("auto_node_move", "Düğüm hareketi ile düzelt",
                                quality_limit=0.2, iterations=10)),
            _step(plan_action("adjust_growth_rate", "Büyümeyi yavaşlat", delta=-0.03)),
        ),
    ),
]

RULES.sort(key=lambda r: r.priority)
RULES_BY_ID: Dict[str, Rule] = {rule.id: rule for rule in RULES}


# --------------------------------------------------------------------------

def diagnose(
    text: str,
    stage: str = ANY,
    occurrences: Optional[Dict[str, int]] = None,
    max_results: int = 3,
) -> List[Diagnosis]:
    """Match ``text`` against the rule base.

    ``occurrences`` maps rule ids to how many times that rule has already
    fired in this run; it selects the escalation step.
    """
    occurrences = occurrences or {}
    found: List[Diagnosis] = []
    for rule in RULES:
        evidence = rule.match(text or "", stage)
        if evidence is None:
            continue
        count = occurrences.get(rule.id, 0) + 1
        step, exhausted = rule.step_for(count)
        found.append(Diagnosis(
            rule_id=rule.id,
            title=rule.title,
            stage=rule.stage,
            severity=rule.severity,
            explanation=rule.explanation,
            evidence=evidence,
            occurrence=count,
            actions=list(step.actions),
            exhausted=exhausted,
        ))
        if rule.severity == SEV_FATAL:
            return found[-1:]
        if len(found) >= max_results:
            break
    return found
