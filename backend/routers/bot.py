# =============================================================================
# bot.py — K Recruit AI Assistant (Chat Bot)
#
# Provides a conversational AI assistant embedded in the K Recruit UI.
# Recruiters can ask questions about candidates, pipeline status, scores, etc.
#
# How it works:
#   1. Recruiter types a message in the chat panel
#   2. The browser sends the message + current page context to POST /bot/chat
#   3. This file builds a context snapshot (pipeline stats, top candidates, JDs)
#   4. Combines it with the recruiter's message and a system prompt
#   5. Sends the full prompt to the configured AI provider (Ollama/Azure/Groq)
#   6. Returns the AI's reply to the browser
#
# Access: get_current_user — any logged-in user can use the chatbot
#
# IMPORTANT: The AI only receives pipeline summary data (counts, scores, statuses).
# No raw resume text is sent to the AI in this endpoint — only structured metadata.
# =============================================================================

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user

router = APIRouter(prefix="/bot", tags=["Bot"])

# The system prompt defines the AI assistant's behavior and boundaries.
# It tells the AI what it can and cannot do, and how to respond.
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


# -----------------------------------------------------------------------------
# Helper: Build a context snapshot from the current database state
#
# This is what gets sent to the AI as background information.
# It includes pipeline statistics and the currently viewed candidate's details.
# This allows the AI to give relevant, data-driven answers.
#
# What IS included: candidate counts, scores, statuses, skill gaps, JD names
# What is NOT included: raw resume text, personal contact details
# -----------------------------------------------------------------------------
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

        # Top 5 candidates by score
        scored = sorted([c for c in candidates if c.score], key=lambda x: x.score, reverse=True)
        if scored:
            top5 = scored[:5]
            lines.append("Top candidates: " + ", ".join(f"{c.name} ({c.score}%, {c.status})" for c in top5))

        # Flag low-scoring candidates for the AI to mention
        low = [c for c in candidates if c.score and c.score < 70]
        if low:
            lines.append("Low score (<70%): " + ", ".join(f"{c.name} ({c.score}%)" for c in low))

        # If the recruiter is viewing a specific candidate, include their full detail
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

        # List active job descriptions so the AI knows what roles are open
        jds = db.query(ClientRequirement).all()
        if jds:
            lines.append("Active JDs: " + ", ".join(f"{j.client_name} — {j.job_title}" for j in jds[:6]))

    except Exception as e:
        lines.append(f"(context error: {e})")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# POST /bot/chat — Send a Message to the AI Assistant
#
# Request body:
#   message  — the recruiter's question or instruction
#   context  — optional dict with current page and candidate_id for context
#
# The full prompt sent to the AI = system_prompt + context snapshot + message.
# AI replies are limited to 500 tokens to keep responses concise.
# If the AI provider is offline → returns a helpful "go to Settings" message.
# -----------------------------------------------------------------------------
@router.post("/chat")
def bot_chat(payload: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    message = (payload.get("message") or "").strip()
    if not message:
        return {"reply": "Please send a message."}

    # Build the live context from current database state
    context_str = _build_context(db, payload.get("context", {}))

    # Assemble the full prompt: instructions + context + recruiter's question
    prompt = f"{SYSTEM_PROMPT}\n\nContext:\n{context_str}\n\nRecruiter: {message}\n\nReply:"

    try:
        from services.llm_service import _call_llm
        reply = _call_llm(prompt, max_tokens=500)
        if not reply:
            return {"reply": "AI provider is offline or not configured. Go to Settings to configure it."}
        return {"reply": reply}
    except Exception as e:
        return {"reply": f"Error calling AI: {e}"}
