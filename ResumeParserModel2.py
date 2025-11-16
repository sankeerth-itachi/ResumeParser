#!/usr/bin/env python3
"""
resume_parser_simple.py

Usage:
    python resume_parser_simple.py input_resume.pdf --out parsed.json

Dependencies:
    pip install pdfplumber python-docx spacy rapidfuzz
    (spaCy model optional but recommended: python -m spacy download en_core_web_sm)
"""

import argparse
import json
import re
from pathlib import Path
from datetime import datetime

# Optional deps
try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import docx
except Exception:
    docx = None

try:
    import spacy
    try:
        nlp = spacy.load("en_core_web_sm")
    except Exception:
        nlp = spacy.blank("en")
except Exception:
    nlp = None

# ----- Regexes and helpers -----
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{6,}\d)")  # prefilter; we validate digits count later
URL_RE = re.compile(r"https?://\S+|www\.\S+")

# Common section headers seen on resumes (lowercase)
DEFAULT_HEADERS = [
    "summary", "profile", "objective",
    "experience", "work experience", "professional experience", "employment",
    "projects", "research",
    "education", "academic", "qualifications",
    "skills", "technical skills", "expertise",
    "certifications", "courses", "licenses",
    "publications", "awards", "honors",
    "languages", "interests"
]

# ----- Text extraction -----
def extract_text_from_pdf(path: Path) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber not installed. Install with: pip install pdfplumber")
    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()

def extract_text_from_docx(path: Path) -> str:
    if docx is None:
        raise RuntimeError("python-docx not installed. Install with: pip install python-docx")
    doc = docx.Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(paragraphs).strip()

def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    if suffix == ".docx":
        return extract_text_from_docx(path)
    if suffix in (".txt", ".text"):
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Unsupported file type: {suffix}")

# ----- Field extraction -----
def extract_emails(text: str):
    return sorted(set(m.group(0).strip() for m in EMAIL_RE.finditer(text)))

def normalize_phone_candidate(s: str):
    digits = re.sub(r"\D", "", s)
    # common plausible range: 7..15 digits (local to international)
    if 10<= len(digits) <= 12:
        return digits
    return None

def extract_phones(text: str):
    candidates = set()
    for m in PHONE_RE.finditer(text):
        norm = normalize_phone_candidate(m.group(0))
        if norm:
            candidates.add(norm)
    # sometimes emails have numbers, so filter duplicates
    return sorted(candidates)

def guess_name_by_spacy(text: str):
    if nlp is None:
        return None
    # analyze only top of resume for name (first 1200 chars) to speed up
    top = text.strip().splitlines()[:10]
    top_text = "\n".join(top)
    doc = nlp(top_text)
    # take first PERSON entity of reasonable length
    for ent in doc.ents:
        if ent.label_ == "PERSON" and 2 <= len(ent.text.split()) <= 4:
            return ent.text.strip()
    return None

def heuristic_name(text: str):
    # Heuristic: first non-empty line that is short (<6 words) and contains at least one capitalized token
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # skip lines that look like emails/phones/urls
        if EMAIL_RE.search(s) or PHONE_RE.search(s) or URL_RE.search(s):
            continue
        words = s.split()
        if 1 <= len(words) <= 6:
            # require at least one token with initial capital (not all-lowercase)
            capital_tokens = [w for w in words if w[0].isalpha() and w[0].isupper()]
            if capital_tokens:
                # filter out headings like "SUMMARY" or "SKILLS" (all uppercase)
                if s.isupper() and len(words) <= 2:
                    # likely a header, skip
                    continue
                return s
    return None

def extract_name(text: str):
    # try spaCy
    name = guess_name_by_spacy(text)
    if name:
        return name
    # otherwise heuristic
    return heuristic_name(text)

# ----- Section splitting -----
def split_sections(text: str, headers=None):
    if headers is None:
        headers = DEFAULT_HEADERS
    headers_normal = [h.lower() for h in headers]

    lines = [ln.rstrip() for ln in text.splitlines()]
    sections = {}
    cur = "header"  # start with header area (top)
    sections[cur] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            # keep blank lines (they help readability) but don't create new sections
            sections[cur].append("")
            continue

        low = re.sub(r"[:\s\-–—]+$", "", line.lower())  # trim trailing punctuation
        matched = None
        # exact match or startswith for header detection
        for h in headers_normal:
            if low == h or low.startswith(h + " ") or low.startswith(h + ":") or low.startswith(h + "-"):
                matched = h
                break
        if matched:
            cur = matched
            sections.setdefault(cur, [])
            continue
        # headings often are ALL CAPS and short
        if line.isupper() and len(line.split()) <= 5:
            # treat as header
            cur = line.lower()
            sections.setdefault(cur, [])
            continue

        sections.setdefault(cur, [])
        sections[cur].append(raw)

    # join and clean
    out = {}
    for k, v in sections.items():
        joined = "\n".join(v).strip()
        if joined:
            out[k] = joined
    return out

# ----- Main parse function -----
def parse_resume(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    text = extract_text(p)
    if not text:
        return {"error": "no text extracted"}

    emails = extract_emails(text)
    phones = extract_phones(text)
    name = extract_name(text)

    sections = split_sections(text)

    parsed = {
        "source_filename": p.name,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "name": name,
        "emails": emails,
        "phones": phones,
        "sections": sections,
        # "raw_text_head": "\n".join(text.splitlines()[:40])
    }
    return parsed

# ----- CLI -----
def main():
    parser = argparse.ArgumentParser(description="Simple resume parser: name, email, phone, sections -> JSON")
    parser.add_argument("input", help="input resume file (.pdf, .docx, .txt)")
    parser.add_argument("--out", "-o", help="output JSON file (optional)")
    args = parser.parse_args()

    parsed = parse_resume(args.input)
    output = json.dumps(parsed, indent=2, ensure_ascii=False)
    print(output)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"\nSaved parsed JSON to {args.out}")

if __name__ == "__main__":
    main()
