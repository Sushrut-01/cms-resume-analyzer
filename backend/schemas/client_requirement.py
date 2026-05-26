# =============================================================================
# schemas/client_requirement.py — Request & Response Validation Schemas
#
# Pydantic schemas define the shape of data coming IN (requests) and
# going OUT (responses) for the client requirements (JD) endpoints.
#
# Why schemas instead of raw dicts?
#   - FastAPI automatically validates incoming request fields against the schema
#   - FastAPI automatically serializes outgoing responses using the schema
#   - Missing required fields → 422 Unprocessable Entity returned automatically
#   - Type errors (e.g. int where string expected) are caught before reaching the DB
#
# Two schemas used:
#   ClientRequirementCreate → validates the request body for POST and PUT
#   ClientRequirementOut    → defines exactly which fields are returned in responses
# =============================================================================

from pydantic import BaseModel


# -----------------------------------------------------------------------------
# ClientRequirementOut — Response Schema
#
# Defines which fields are included when a JD is returned in an API response.
# Used as response_model on GET, POST, and PUT endpoints in client_requirements.py.
#
# from_attributes = True allows Pydantic to read values directly from
# SQLAlchemy model objects (ORM instances) instead of plain dicts.
# -----------------------------------------------------------------------------
class ClientRequirementOut(BaseModel):
    id:          int    # unique database ID
    client_name: str    # name of the client company
    job_title:   str    # job role being hired for
    jd_text:     str    # full job description text
    active:      bool   # True = visible in dropdowns, False = deactivated

    class Config:
        from_attributes = True  # allows reading from SQLAlchemy ORM objects


# -----------------------------------------------------------------------------
# ClientRequirementCreate — Request Body Schema
#
# Validates the request body when creating or updating a JD.
# All three fields are required — FastAPI returns 422 if any are missing.
# The "active" field is not included here because:
#   - New JDs are always created as active=True (set automatically in the router)
#   - Deactivation is done via DELETE, not via this schema
# -----------------------------------------------------------------------------
class ClientRequirementCreate(BaseModel):
    client_name: str   # name of the client company (required)
    job_title:   str   # job role title (required)
    jd_text:     str   # full job description text (required)
