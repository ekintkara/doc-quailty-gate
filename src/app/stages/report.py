from __future__ import annotations

from datetime import datetime, timezone

import structlog
from jinja2 import Template

from app.config import ThresholdConfig
from app.schemas import (
    RunArtifacts,
    RunMetadata,
    Scorecard,
    Validation,
    ValidationDecision,
)

logger = structlog.get_logger("report")

MARKDOWN_TEMPLATE = """\
# Doküman Kalite Kapısı Raporu

**Çalışma ID:** {{ run_id }}
**Zaman Damgası:** {{ metadata.timestamp }}
**Doküman Türü:** {{ metadata.document_type }}
**Yürütme Durumu:** {{ metadata.execution_status }}

---

## Kapı Kararı

| Alan | Değer |
|------|-------|
| **Genel Puan** | {{ scorecard.overall_score }}/10 |
| **Sonuç** | {{ "GEÇTİ" if scorecard.passed else "KALDI" }} |
| **Sonraki Adım** | {{ scorecard.recommended_next_action.value }} |
| **Çözülmemiş Kritik Sorunlar** | {{ scorecard.unresolved_critical_issues_count }} |
| **Scorer Çalışma Sayısı** | {{ scorecard.scorer_run_count }} |
| **Skor Varyansı** | {{ "%.4f"|format(scorecard.scorer_score_variance) }} |
| **Güven** | {{ "%.2f"|format(scorecard.confidence_in_scoring * 100) }}% |

{% if scorecard.blocking_reasons %}
### Engelleyici Nedenler
{% for reason in scorecard.blocking_reasons %}
- {{ reason }}
{% endfor %}
{% endif %}

{% if scorecard.promptfoo_agreement %}
### Promptfoo Uyuşma
| Alan | Değer |
|------|-------|
| **Uyuşma** | {{ scorecard.promptfoo_agreement }} |
{% if scorecard.promptfoo_dimension_scores %}
| **Promptfoo Model** | Farklı model ile değerlendirildi |
{% endif %}
{% endif %}

{% if scorecard.meta_judge_result %}
### Meta-Judge Değerlendirmesi
| Alan | Değer |
|------|-------|
| **Karar** | {{ scorecard.meta_judge_result.verdict }} |
{% if scorecard.meta_judge_result.adjustments %}
| **Düzeltmeler** | {{ scorecard.meta_judge_result.adjustments }} |
{% endif %}
{% if scorecard.meta_judge_result.reasoning %}
| **Gerekçe** | {{ scorecard.meta_judge_result.reasoning }} |
{% endif %}
{% endif %}

---

## Boyut Puanları

| Boyut | Puan | Ağırlık | Ağırlıklı |
|-------|------|---------|-----------|
{% for dim in dimensions %}
| {{ dim.name }} | {{ dim.score }}/10 | {{ dim.weight }}x | {{ dim.weighted }} |
{% endfor %}
| **Genel** | **{{ scorecard.overall_score }}/10** | | |

---

## Sorun Özeti

**Toplam Bulunan Sorun:** {{ issues|length }}
**Geçerli:** {{ valid_count }} | **Geçersiz:** {{ invalid_count }} | **Belirsiz:** {{ uncertain_count }}

{% if issues %}
### Sorunlar (Önem Derecesine Göre)

| ID | Başlık | Önem | Kategori | Kaynak | Doğrulama |
|----|--------|------|----------|--------|-----------|
{% for issue in issues %}
| {{ issue.id }} | {{ issue.title }} |
{{ issue.severity }} | {{ issue.category }} |
{{ issue.source_pass }} | {{ issue.validation_status }} |
{% endfor %}
{% endif %}

{% if scorecard.key_strengths %}
### Temel Güçlü Yönler
{% for s in scorecard.key_strengths %}
- {{ s }}
{% endfor %}
{% endif %}

{% if scorecard.remaining_concerns %}
### Kalan Endişeler
{% for c in scorecard.remaining_concerns %}
- {{ c }}
{% endfor %}
{% endif %}

---

## Model Yapılandırması

| Aşama | Takma Ad | Kullanılan Model |
|-------|----------|-----------------|
{% for stage, alias in metadata.model_aliases_used.items() %}
| {{ stage }} | {{ alias }} | {{ metadata.actual_models_used.get(stage, "Yok") }} |
{% endfor %}

{% if metadata.warnings %}
### Uyarılar
{% for w in metadata.warnings %}
- {{ w }}
{% endfor %}
{% endif %}

---

*Doc Quality Gate v0.1.0 tarafından oluşturuldu*
"""

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Doküman Kalite Kapısı Raporu - {{ run_id }}</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
    Roboto, sans-serif; color: #1a1a2e; background: #f8f9fa; line-height: 1.6; }
  .container { max-width: 960px; margin: 0 auto; padding: 2rem; }
  h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.4rem; margin: 1.5rem 0 1rem; color: #2d3436;
    border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3rem; }
  h3 { font-size: 1.1rem; margin: 1rem 0 0.5rem; }
  .meta { color: #636e72; margin-bottom: 1.5rem; }
  .gate-box { padding: 1.5rem; border-radius: 8px; margin: 1rem 0; }
  .gate-pass { background: #d4edda; border: 1px solid #c3e6cb; }
  .gate-fail { background: #f8d7da; border: 1px solid #f5c6cb; }
  .gate-box table { width: 100%; }
  .gate-box td, .gate-box th { padding: 0.3rem 0.6rem; text-align: left; }
  table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
  th, td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #dee2e6; text-align: left; }
  th { background: #f1f3f5; font-weight: 600; }
  tr:hover { background: #f8f9fa; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.85rem; font-weight: 600; }
  .badge-critical { background: #ff6b6b; color: white; }
  .badge-high { background: #ffa502; color: white; }
  .badge-medium { background: #ffd93d; color: #333; }
  .badge-low { background: #6c5ce7; color: white; }
  .badge-valid { background: #00b894; color: white; }
  .badge-invalid { background: #e17055; color: white; }
  .badge-uncertain { background: #fdcb6e; color: #333; }
  .score-bar { height: 8px; border-radius: 4px; background: #e0e0e0; }
  .score-fill { height: 100%; border-radius: 4px; }
  .score-good { background: #00b894; }
  .score-ok { background: #fdcb6e; }
  .score-bad { background: #ff6b6b; }
  .warnings { background: #fff3cd; border: 1px solid #ffc107; padding: 1rem; border-radius: 6px; margin: 1rem 0; }
  .footer { margin-top: 2rem; text-align: center; color: #adb5bd; font-size: 0.85rem; }
  ul { padding-left: 1.5rem; margin: 0.5rem 0; }
  li { margin: 0.25rem 0; }
</style>
</head>
<body>
<div class="container">
  <h1>Doküman Kalite Kapısı Raporu</h1>
  <div class="meta">
    <strong>Çalışma ID:</strong> {{ run_id }} &bull;
    <strong>Zaman Damgası:</strong> {{ metadata.timestamp }} &bull;
    <strong>Tür:</strong> {{ metadata.document_type }} &bull;
    <strong>Durum:</strong> {{ metadata.execution_status }}
  </div>

    <div class="gate-box {{ 'gate-pass' if scorecard.passed else 'gate-fail' }}">
    <h2 style="border:none;margin:0 0 0.5rem;">Kapı Kararı: {{ "GEÇTİ" if scorecard.passed else "KALDI" }}</h2>
    <table>
      <tr><th>Genel Puan</th><td>{{ scorecard.overall_score }}/10</td></tr>
      <tr><th>Sonraki Adım</th><td>{{ scorecard.recommended_next_action.value }}</td></tr>
      <tr><th>Çözülmemiş Kritik Sorunlar</th><td>{{ scorecard.unresolved_critical_issues_count }}</td></tr>
      <tr><th>Scorer Çalışma Sayısı</th><td>{{ scorecard.scorer_run_count }}</td></tr>
      <tr><th>Güven</th><td>{{ "%.2f"|format(scorecard.confidence_in_scoring * 100) }}%</td></tr>
    </table>
    {% if scorecard.blocking_reasons %}
    <h3>Engelleyici Nedenler</h3>
    <ul>
    {% for reason in scorecard.blocking_reasons %}
      <li>{{ reason }}</li>
    {% endfor %}
    </ul>
    {% endif %}
    {% if scorecard.promptfoo_agreement %}
    <h3>Promptfoo Uyuşma: {{ scorecard.promptfoo_agreement }}</h3>
    {% endif %}
    {% if scorecard.meta_judge_result %}
    <h3>Meta-Judge: {{ scorecard.meta_judge_result.verdict }}</h3>
    {% if scorecard.meta_judge_result.reasoning %}
    <p>{{ scorecard.meta_judge_result.reasoning }}</p>
    {% endif %}
    {% endif %}
    </div>

  <h2>Boyut Puanları</h2>
  <table>
    <tr><th>Boyut</th><th>Puan</th><th>Çubuk</th></tr>
    {% for dim in dimensions %}
    <tr>
      <td>{{ dim.name }}</td>
      <td>{{ dim.score }}/10</td>
      <td>
        <div class="score-bar">
          <div class="score-fill
            {{ 'score-good' if dim.score >= 8 else ('score-ok' if dim.score >= 6 else 'score-bad') }}"
            style="width: {{ dim.score * 10 }}%"></div>
        </div>
      </td>
    </tr>
    {% endfor %}
    <tr style="font-weight:bold">
      <td>Genel (ağırlıklı)</td>
      <td>{{ scorecard.overall_score }}/10</td>
      <td>
        <div class="score-bar">
          <div class="score-fill
            {{ 'score-good' if scorecard.overall_score >= 8
               else ('score-ok' if scorecard.overall_score >= 6
               else 'score-bad') }}"
            style="width: {{ scorecard.overall_score * 10 }}%"></div>
        </div>
      </td>
    </tr>
  </table>

  <h2>Sorun Özeti</h2>
  <p><strong>Toplam:</strong> {{ issues|length }} &bull;
     <span class="badge badge-valid">Geçerli: {{ valid_count }}</span>
     <span class="badge badge-invalid">Geçersiz: {{ invalid_count }}</span>
     <span class="badge badge-uncertain">Belirsiz: {{ uncertain_count }}</span>
  </p>

  {% if issues %}
  <table>
    <tr><th>ID</th><th>Başlık</th><th>Önem</th><th>Kategori</th><th>Kaynak</th><th>Durum</th></tr>
    {% for issue in issues %}
    <tr>
      <td>{{ issue.id }}</td>
      <td>{{ issue.title }}</td>
      <td><span class="badge badge-{{ issue.severity }}">{{ issue.severity }}</span></td>
      <td>{{ issue.category }}</td>
      <td>{{ issue.source_pass }}</td>
      <td><span class="badge badge-{{ issue.validation_status }}">{{ issue.validation_status }}</span></td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}

  {% if scorecard.key_strengths %}
  <h2>Temel Güçlü Yönler</h2>
  <ul>
  {% for s in scorecard.key_strengths %}
    <li>{{ s }}</li>
  {% endfor %}
  </ul>
  {% endif %}

  {% if scorecard.remaining_concerns %}
  <h2>Kalan Endişeler</h2>
  <ul>
  {% for c in scorecard.remaining_concerns %}
    <li>{{ c }}</li>
  {% endfor %}
  </ul>
  {% endif %}

  {% if metadata.warnings %}
  <div class="warnings">
    <strong>Uyarılar:</strong>
    <ul>
    {% for w in metadata.warnings %}
      <li>{{ w }}</li>
    {% endfor %}
    </ul>
  </div>
  {% endif %}

  <div class="footer">Doc Quality Gate v0.1.0 tarafından oluşturuldu</div>
</div>
</body>
</html>
"""


def _build_validation_lookup(validations: list[Validation]) -> dict[str, Validation]:
    lookup = {}
    for v in validations:
        lookup[v.issue_id] = v
    return lookup


def generate_reports(
    artifacts: RunArtifacts,
    threshold_config: "ThresholdConfig",
) -> tuple[str, str]:

    scorecard = artifacts.scorecard or Scorecard()
    metadata = artifacts.metadata or RunMetadata(
        timestamp=datetime.now(timezone.utc).isoformat(),
        document_type="custom",
    )

    val_lookup = _build_validation_lookup(artifacts.validations)
    weights = threshold_config.dimension_weights

    dimensions = []
    ds = scorecard.dimension_scores.model_dump()
    for dim_name in [
        "correctness",
        "completeness",
        "implementability",
        "consistency",
        "edge_case_coverage",
        "testability",
        "risk_awareness",
        "clarity",
    ]:
        score = ds.get(dim_name, 0.0)
        w = weights.get(dim_name, 1.0)
        dimensions.append(
            {
                "name": dim_name.replace("_", " ").title(),
                "score": score,
                "weight": w,
                "weighted": round(score * w, 2),
            }
        )

    valid_count = sum(1 for v in artifacts.validations if v.decision == ValidationDecision.VALID)
    invalid_count = sum(1 for v in artifacts.validations if v.decision == ValidationDecision.INVALID)
    uncertain_count = sum(1 for v in artifacts.validations if v.decision == ValidationDecision.UNCERTAIN)

    issues_data = []
    for issue in artifacts.issues:
        val = val_lookup.get(issue.id)
        if not val and "+" in issue.id:
            for part in issue.id.split("+"):
                if part in val_lookup:
                    val = val_lookup[part]
                    break
        val_status = val.decision.value if val else "unvalidated"
        issues_data.append(
            {
                "id": issue.id,
                "title": issue.title,
                "severity": issue.severity.value,
                "category": issue.category,
                "source_pass": issue.source_pass.value,
                "validation_status": val_status,
            }
        )

    template_vars = {
        "run_id": artifacts.run_id,
        "scorecard": scorecard,
        "metadata": metadata,
        "dimensions": dimensions,
        "issues": issues_data,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "uncertain_count": uncertain_count,
    }

    md_report = Template(MARKDOWN_TEMPLATE).render(**template_vars)
    html_report = Template(HTML_TEMPLATE).render(**template_vars)

    logger.info("reports_generated", md_length=len(md_report), html_length=len(html_report))
    return md_report, html_report
