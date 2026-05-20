import fitz
import re
import os

def extract_text_from_pdf(pdf_path: str) -> dict:
    try:
        doc = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        page_count = doc.page_count
        doc.close()

        email = extract_email(full_text)
        mobile = extract_mobile(full_text)
        name = extract_name(full_text)
        summary = extract_summary(full_text)
        location = extract_location(full_text)
        linkedin = extract_linkedin(full_text)

        print(f"Extracted text length: {len(full_text)} characters")
        print(f"Name: {name}, Email: {email}, Mobile: {mobile}")

        return {
            "success": True,
            "full_text": full_text.strip(),
            "email": email,
            "mobile": mobile,
            "name": name,
            "summary": summary,
            "location": location,
            "linkedin": linkedin,
            "pages": page_count
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def extract_email(text: str) -> str:
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, text)
    return match.group() if match else ""

def extract_mobile(text: str) -> str:
    pattern = r'(\+91[\s-]?)?[6-9]\d{9}'
    match = re.search(pattern, text)
    return match.group() if match else ""

def extract_name(text: str) -> str:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return lines[0] if lines else ""

def extract_summary(text: str) -> str:
    lower = text.lower()
    for keyword in ["summary", "objective", "profile", "about"]:
        idx = lower.find(keyword)
        if idx != -1:
            return text[idx:idx+300].strip()
    return text[:200].strip()

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

def extract_linkedin(text: str) -> str:
    pattern = r'linkedin\.com/in/[A-Za-z0-9\-_/]+'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group() if match else ""