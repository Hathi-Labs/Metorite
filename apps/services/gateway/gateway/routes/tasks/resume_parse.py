"""Tasks · résumé parsing — turn an uploaded CV (PDF/DOCX) into skills + a
profile, to auto-update a person's HR record (spec §4).

Two-tier, LLM-first with a deterministic fallback:
  • text extraction — PyMuPDF for PDF, python-docx for DOCX, utf-8 for text.
  • skill extraction — keyword match against the org's known skill vocabulary
    (always runs, never fails) MERGED with an LLM pass that also pulls the
    experience summary / years / domain (best-effort — degrades to keyword-only
    when the model is unavailable).

Parsing NEVER raises to the caller: a bad/empty file yields an empty result so
the upload route can respond gracefully.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# A small base vocabulary so a résumé skill that no one in people has yet is
# still detected. The PRIMARY vocabulary is the org's existing skills (passed in),
# so this only needs to cover common tech/PM terms the seed might miss.
_BASE_SKILL_VOCAB: tuple[str, ...] = (
    "python", "javascript", "typescript", "react", "next.js", "node.js", "fastapi",
    "django", "flask", "sql", "postgresql", "mongodb", "redis", "aws", "gcp",
    "azure", "docker", "kubernetes", "terraform", "ci/cd", "git", "linux",
    "c", "c++", "c#", "go", "rust", "java", "kotlin", "swift", "php", "ruby",
    "html", "css", "tailwind", "graphql", "rest", "grpc", "kafka", "rabbitmq",
    "machine learning", "deep learning", "pytorch", "tensorflow", "nlp",
    "computer vision", "data science", "pandas", "numpy", "llm", "rag",
    "embedded", "firmware", "rtos", "stm32", "arduino", "esp32", "pcb", "altium",
    "kicad", "solidworks", "fusion 360", "cad", "3d printing", "cnc", "gd&t",
    "mechanical design", "electronics", "iot", "robotics", "ros", "control systems",
    "figma", "ui/ux", "product management", "project management", "scrum", "agile",
    "jira", "clickup", "notion", "marketing", "seo", "content", "sales", "finance",
    "accounting", "hr", "recruiting", "operations", "supply chain", "leadership",
)


def extract_text(content: bytes, filename: str, mime: str | None) -> str:
    """Extract plain text from a résumé file. Returns "" on any failure."""
    suffix = Path(filename or "").suffix.lower()
    m = (mime or "").lower()
    try:
        if suffix == ".pdf" or "pdf" in m:
            return _pdf_text(content)
        if suffix in (".docx",) or "wordprocessingml" in m:
            return _docx_text(content)
        if suffix in (".txt", ".md", ".rtf") or m.startswith("text/"):
            return content.decode("utf-8", errors="replace")
    except Exception:
        return ""
    # Unknown type — last-ditch utf-8 (covers mislabelled text résumés).
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _pdf_text(content: bytes) -> str:
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf
    parts: list[str] = []
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            parts.append(page.get_text())
    return "\n".join(parts)


def _docx_text(content: bytes) -> str:
    import io

    import docx
    d = docx.Document(io.BytesIO(content))
    return "\n".join(p.text for p in d.paragraphs)


def extract_skills(text: str, known_skills: list[str] | None = None) -> list[str]:
    """Keyword match résumé text against the org's skills + a base vocabulary.
    Case-insensitive, boundary-aware so 'r' doesn't match inside 'react'."""
    if not text:
        return []
    hay = text.lower()
    vocab = {s.strip().lower() for s in (known_skills or []) if s and s.strip()}
    vocab.update(_BASE_SKILL_VOCAB)
    found: list[str] = []
    for skill in vocab:
        # Boundaries = start/end or a non-(alnum/+/#/./ ) char, so 'c++',
        # 'node.js', 'ci/cd' still match while 'c' won't match inside 'cad'.
        pat = rf"(?<![a-z0-9+#./]){re.escape(skill)}(?![a-z0-9+#./])"
        if re.search(pat, hay):
            found.append(skill)
    return sorted(set(found))


async def llm_extract_profile(text: str) -> dict[str, Any]:
    """LLM pass: pull {skills[], credentials[], experience_summary,
    years_experience, domain} from résumé text. Returns {} on any failure
    (caller keeps the keyword hits)."""
    snippet = (text or "")[:12000]
    if not snippet.strip():
        return {}
    try:
        from acb_llm.context import acompletion_with_fallback
        system = (
            "You extract a structured profile from résumé text. Return ONLY JSON: "
            '{"skills": [str], "experience_summary": str, '
            '"years_experience": int|null, "domain": str, '
            '"credentials": [{"kind": "education"|"certification"|"prior_role", '
            '"title": str, "issuer": str|null, "year_from": int|null, '
            '"year_to": int|null}]}. '
            "skills = concrete technical/professional competencies (lowercase, "
            "deduplicated). experience_summary = one sentence. domain = the "
            "person's primary field (e.g. 'firmware', 'web', 'mechanical', 'sales'). "
            "credentials = degrees (kind=education, issuer=institution), "
            "certifications (issuer=certifying body), and previous jobs "
            "(kind=prior_role, title=the role, issuer=the employer). Only what "
            "the text actually states — never infer a credential."
        )
        resp, _used = await acompletion_with_fallback(
            model="tier-balanced",
            fallback_model="tier-balanced",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": snippet}],
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    out: dict[str, Any] = {}
    if isinstance(data.get("skills"), list):
        out["skills"] = [str(s).strip().lower() for s in data["skills"] if str(s).strip()]
    if data.get("experience_summary"):
        out["experience_summary"] = str(data["experience_summary"]).strip()[:1000]
    yrs = data.get("years_experience")
    if isinstance(yrs, (int, float)):
        out["years_experience"] = int(yrs)
    if data.get("domain"):
        out["domain"] = str(data["domain"]).strip()[:80]
    if isinstance(data.get("credentials"), list):
        out["credentials"] = _clean_credentials(data["credentials"])
    return out


def _clean_credentials(raw: list[Any]) -> list[dict[str, Any]]:
    """Coerce the model's credential rows to the store's shape, dropping what
    cannot be used. Tolerant on the way in for the same reason the whole
    pipeline is: a hallucinated year should cost its own row, never the parse.
    The store validates again (`person_skills.validate_credentials`) — this
    pass only makes rows well-typed enough to reach it."""
    out: list[dict[str, Any]] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        title = str(item.get("title") or "").strip()
        if kind not in ("education", "certification", "prior_role") or not title:
            continue
        years: dict[str, int | None] = {}
        for field in ("year_from", "year_to"):
            value = item.get(field)
            years[field] = int(value) if isinstance(value, (int, float)) else None
        out.append({
            "kind": kind, "title": title[:200],
            "issuer": (str(item.get("issuer") or "").strip()[:200] or None),
            **years,
        })
    return out


async def parse_resume(
    content: bytes, filename: str, mime: str | None,
    known_skills: list[str] | None = None,
) -> dict[str, Any]:
    """Full pipeline: text → keyword skills ⊕ LLM profile. Always returns a dict
    (possibly with empty skills) — never raises."""
    text = extract_text(content, filename, mime)
    kw_skills = extract_skills(text, known_skills)
    llm = await llm_extract_profile(text)
    # Union keyword + LLM skills (both lowercased); LLM adds ones not in our vocab.
    skills = sorted(set(kw_skills) | set(llm.get("skills", [])))
    return {
        "text": text,
        "skills": skills,
        # LLM-only: the keyword pass has no way to know a degree from a job
        # title, and an empty list on LLM failure degrades exactly like the
        # rest of the profile half.
        "credentials": llm.get("credentials", []),
        "experience_summary": llm.get("experience_summary"),
        "years_experience": llm.get("years_experience"),
        "domain": llm.get("domain"),
    }
