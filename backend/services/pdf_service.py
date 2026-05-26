# =============================================================================
# services/pdf_service.py — PDF Text Extraction
#
# Extracts text and contact details from uploaded resume PDFs.
# Used during the upload step in candidates.py.
#
# Library used: PyMuPDF (fitz) — fast, works offline, no cloud calls.
# All extraction is done locally on the server — no PDF content leaves the machine.
#
# What is extracted:
#   full_text → all text from all pages (used for AI analysis)
#   name      → first non-empty line of the resume (usually the candidate's name)
#   email     → regex match for email format
#   mobile    → regex match for Indian mobile number format
#   summary   → first "Summary" / "Objective" / "Profile" section
#   location  → city name match (major Indian cities)
#   linkedin  → LinkedIn profile URL if present
# =============================================================================

import fitz   # PyMuPDF — PDF parsing library
import re
import os


# -----------------------------------------------------------------------------
# extract_text_from_pdf — Main Entry Point
# Opens the PDF, reads all pages, then runs each extraction function.
# Returns a dict with success=True and all extracted fields on success.
# Returns success=False with an error message if the PDF cannot be read.
# -----------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: str) -> dict:
    try:
        doc       = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()   # extract plain text from each page
        page_count = doc.page_count
        doc.close()

        # Run each extractor on the full text
        email    = extract_email(full_text)
        mobile   = extract_mobile(full_text)
        name     = extract_name(full_text)
        summary  = extract_summary(full_text)
        location = extract_location(full_text)
        linkedin = extract_linkedin(full_text)

        print(f"Extracted text length: {len(full_text)} characters")
        print(f"Name: {name}, Email: {email}, Mobile: {mobile}")

        return {
            "success":   True,
            "full_text": full_text.strip(),
            "email":     email,
            "mobile":    mobile,
            "name":      name,
            "summary":   summary,
            "location":  location,
            "linkedin":  linkedin,
            "pages":     page_count,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------------------
# extract_email
# Finds the first email address in the resume text using a standard email regex.
# Returns empty string if no email found.
# -----------------------------------------------------------------------------
def extract_email(text: str) -> str:
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, text)
    return match.group() if match else ""


# -----------------------------------------------------------------------------
# extract_mobile
# Finds an Indian mobile number (starting with 6-9, 10 digits, optional +91 prefix).
# Returns empty string if no mobile number found.
# -----------------------------------------------------------------------------
def extract_mobile(text: str) -> str:
    pattern = r'(\+91[\s-]?)?[6-9]\d{9}'
    match = re.search(pattern, text)
    return match.group() if match else ""


# -----------------------------------------------------------------------------
# extract_name
# Returns the first non-empty line of the resume.
# Resumes typically start with the candidate's name at the very top.
# -----------------------------------------------------------------------------
def extract_name(text: str) -> str:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return lines[0] if lines else ""


# -----------------------------------------------------------------------------
# extract_summary
# Finds the professional summary section of the resume.
# Looks for common section headings: "Summary", "Objective", "Profile", "About".
# Returns the first 300 characters after the heading.
# Falls back to the first 200 characters of the full text if no heading found.
# -----------------------------------------------------------------------------
def extract_summary(text: str) -> str:
    lower = text.lower()
    for keyword in ["summary", "objective", "profile", "about"]:
        idx = lower.find(keyword)
        if idx != -1:
            return text[idx:idx+300].strip()
    return text[:200].strip()


# -----------------------------------------------------------------------------
# extract_location
# Searches for major Indian city names in the resume text.
# Returns the matched city with optional state/country context.
# Returns empty string if no recognizable city found.
# -----------------------------------------------------------------------------
def extract_location(text: str) -> str:
    patterns = [
        r'(?:Pune|Mumbai|Bangalore|Hyderabad|Chennai|Delhi|Noida|Gurgaon|Kolkata)[,\s]*(?:India)?',
        r'\b[A-Z][a-z]+,\s*(?:India|Maharashtra|Karnataka|Tamil Nadu)\b'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group().strip()
    return ""


# -----------------------------------------------------------------------------
# extract_linkedin
# Finds a LinkedIn profile URL in the resume text.
# Matches URLs in the format: linkedin.com/in/username
# Returns empty string if no LinkedIn URL found.
# -----------------------------------------------------------------------------
def extract_linkedin(text: str) -> str:
    pattern = r'linkedin\.com/in/[A-Za-z0-9\-_/]+'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group() if match else ""
