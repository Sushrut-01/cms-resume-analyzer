from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user

router = APIRouter(prefix="/bot", tags=["Bot"])

SYSTEM_PROMPT = """You are K Recruit AI, an intelligent assistant embedded in Kforce's internal HR resume tool.
You help recruiters with:
- Finding and shortlisting candidates by score, status, or role
- Explaining why a candidate scored high or low (gaps vs strengths)
- Generating targeted interview questions based on a candidate's skill gaps
- Suggesting next actions (run analysis, JD alignment, approve, download)
- Summarising pipeline activity

Rules:
- Be concise — 2-4 sentences unless listing items
- Use bullet points when listing candidates or skills
- Never invent data — only use what is in the context provided
- If context lacks the answer, say so and suggest where to look in the app
- Use plain, professional language — no fluff
"""


def _build_context(db: Session, ctx: dict) -> str:
    lines = [f"Page: {ctx.get('page', 'dashboard')}"]
    try:
        from models.candidate import Candidate
        from models.client_requirement import ClientRequirement

        candidates = db.query(Candidate).all()
        total    = len(candidates)
        pending  = sum(1 for c in candidates if c.status in ("Pending Review", "Uploaded"))
        analyzed = sum(1 for c in candidates if c.status == "Bot Analyzed")
        approved = sum(1 for c in candidates if c.status == "Approved")
        lines.append(f"Pipeline: {total} total | {pending} pending | {analyzed} analyzed | {approved} approved")

        scored = sorted([c for c in candidates if c.score], key=lambda x: x.score, reverse=True)
        if scored:
            top5 = scored[:5]
            lines.append("Top candidates: " + ", ".join(f"{c.name} ({c.score}%, {c.status})" for c in top5))
        low = [c for c in candidates if c.score and c.score < 70]
        if low:
            lines.append("Low score (<70%): " + ", ".join(f"{c.name} ({c.score}%)" for c in low))

        # Current candidate detail
        cid = ctx.get("candidate_id")
        if cid:
            cand = db.query(Candidate).filter(Candidate.id == int(cid)).first()
            if cand:
                lines.append(
                    f"Currently viewing: {cand.name} | Score: {cand.score}% | "
                    f"Semantic: {cand.semantic_score}% | Status: {cand.status} | "
                    f"Role: {cand.role or 'N/A'} | Company: {cand.company_name or 'N/A'}"
                )
                if cand.gaps:
                    lines.append(f"Gaps: {', '.join(cand.gaps[:10])}")
                if cand.strengths:
                    lines.append(f"Strengths: {', '.join(cand.strengths[:8])}")
                if cand.detected_domain:
                    lines.append(f"Domain: {cand.detected_domain} | Injection supported: {cand.injection_supported}")

        jds = db.query(ClientRequirement).all()
        if jds:
            lines.append("Active JDs: " + ", ".join(f"{j.client_name} — {j.job_title}" for j in jds[:6]))

    except Exception as e:
        lines.append(f"(context error: {e})")
    return "\n".join(lines)


@router.post("/chat")
def bot_chat(payload: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    message = (payload.get("message") or "").strip()
    if not message:
        return {"reply": "Please send a message."}

    context_str = _build_context(db, payload.get("context", {}))
    prompt = f"{SYSTEM_PROMPT}\n\nContext:\n{context_str}\n\nRecruiter: {message}\n\nReply:"

    try:
        from services.llm_service import _call_llm
        reply = _call_llm(prompt, max_tokens=500)
        if not reply:
            return {"reply": "AI provider is offline or not configured. Go to Settings to configure it."}
        return {"reply": reply}
    except Exception as e:
        return {"reply": f"Error calling AI: {e}"}
