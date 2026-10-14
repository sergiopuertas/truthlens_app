from __future__ import annotations

from .models import ItemReport

VECTOR_LABELS = {
    "false": "Información falsa",
    "misleading": "Engañosa / sin contexto",
    "biased": "Sesgada",
    "fallacious": "Contiene falacias",
    "hate": "Posible incitación al odio",
}
VERDICT_LABELS = {"supported": "Respaldada", "refuted": "Falsa", "misleading": "Engañosa", "unverifiable": "No verificable"}


def vector_label(v: str) -> str:
    return VECTOR_LABELS.get(v, v)


def format_report(reports: list[ItemReport]) -> str:
    out: list[str] = []
    for r in reports:
        it = r.item
        meta = " · ".join(x for x in (it.outlet, it.platform) if x)
        out.append("=" * 72)
        out.append(it.headline[:200] + (f"\n({meta})" if meta else "") + (f"\n{it.url}" if it.url else ""))
        if r.error:
            out.append(f"\n  ✘ No se pudo analizar: {r.error}")
            continue
        if r.claims:
            out.append("\nAfirmaciones verificadas:")
            for c in r.claims:
                out.append(f"  • [{VERDICT_LABELS.get(c.label, c.label)} {c.confidence:.0%}] {c.claim}")
                if c.reasoning:
                    out.append(f"      {c.reasoning}")
                for s in c.sources[:3]:
                    rating = f" — {s.rating}" if s.rating else ""
                    out.append(f"      ↳ {s.title or s.domain}{rating} [{s.tier}]\n        {s.url}")
        else:
            out.append("\nNo se encontraron afirmaciones verificables.")
        if r.flags:
            out.append("\n⚠ ALERTAS")
            for f in r.flags:
                out.append(f"  ● {vector_label(f.vector)} (confianza {f.confidence:.0%})")
                if f.claim:
                    out.append(f"    Afirmación: {f.claim}")
                if f.quote:
                    out.append(f"    Cita: «{f.quote}»")
                if f.explanation:
                    out.append(f"    Por qué: {f.explanation}")
                for s in f.sources:
                    rating = f" — {s.rating}" if s.rating else ""
                    out.append(f"    Fuente: {s.title or s.domain}{rating}\n            {s.url}")
        else:
            out.append("\n✔ Sin alertas.")
    flagged = sum(1 for r in reports if r.flags)
    out.append("=" * 72)
    out.append(f"{len(reports)} elemento(s) analizado(s), {flagged} con alertas.")
    return "\n".join(out)
