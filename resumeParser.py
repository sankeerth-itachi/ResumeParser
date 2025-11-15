# resume_parser.py
import re
import json
from pathlib import Path
import pdfplumber
import docx
import spacy
from rapidfuzz import process, fuzz
from datetime import datetime

nlp = spacy.load("en_core_web_sm")

# --- Helpers: text extraction ------------------------------------------------
def extract_text_from_pdf(path):
    text = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text.append(t)
    return "\n".join(text)

def extract_text_from_docx(path):
    try:
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        print(f"Error reading DOCX file {path}: {e}")
        # Fallback: try reading as plain text
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except:
            return ""

def extract_text_from_doc(path):
    """Extract text from .doc files (older Word format)"""
    # Method 1: Try using python-oletools (for older .doc format)
    try:
        from oletools.olevba import VBA_Parser
        from oletools import rtfobj
        # This is a basic attempt - .doc files are complex binary format
        print("Warning: .doc files are not fully supported. Consider converting to .docx")
        return Path(path).read_text(encoding="latin-1", errors="ignore")
    except ImportError:
        pass
    
    # Method 2: Try using textract if available
    try:
        import textract
        text = textract.process(str(path)).decode('utf-8')
        return text
    except ImportError:
        print("For better .doc support, install textract: pip install textract")
    except Exception as e:
        print(f"Error using textract: {e}")
    
    # Method 3: Try using antiword if available (requires external binary)
    try:
        import subprocess
        result = subprocess.run(['antiword', str(path)], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        print("antiword not found. For better .doc support, install antiword")
    
    # Fallback: try reading as plain text (will likely be garbled but might extract some text)
    try:
        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252', 'ascii']:
            try:
                text = Path(path).read_text(encoding=encoding, errors="ignore")
                # Basic cleanup of binary junk
                import string
                printable = set(string.printable)
                cleaned = ''.join(filter(lambda x: x in printable, text))
                if len(cleaned) > 100:  # If we got some reasonable text
                    return cleaned
            except:
                continue
        return ""
    except Exception as e:
        print(f"Error reading .doc file {path}: {e}")
        return ""

def extract_text(path):
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return extract_text_from_pdf(path)
    if path.suffix.lower() == ".docx":
        return extract_text_from_docx(path)
    if path.suffix.lower() == ".doc":
        return extract_text_from_doc(path)
    # For other file types, try reading as plain text
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"Error reading file {path}: {e}")
        return ""

# --- Regex extractors -------------------------------------------------------
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{6,10}")
URL_RE   = re.compile(r"https?://\S+|www\.\S+")

def extract_email(text):
    m = EMAIL_RE.search(text)
    return m.group(0) if m else None

def extract_phones(text):
    phones = PHONE_RE.findall(text)
    # PHONE_RE with groups returns tuples; flatten & uniq:
    xs = set()
    for p in PHONE_RE.finditer(text):
        s = p.group(0).strip()
        if len(re.sub(r"\D", "", s)) >= 7:
            xs.add(s)
    return list(xs)

# --- Section splitting ------------------------------------------------------
SECTION_MARKERS = [
    r"\bexperience\b", r"\bwork experience\b", r"\bemployment\b",
    r"\beducation\b", r"\bprojects\b", r"\bskills\b", r"\bsummary\b",
    r"\bcertifications\b", r"\bpublications\b"
]

def split_sections(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = "\n".join(lines)
    # naive: find section headers by regex positions
    pattern = "(" + "|".join(SECTION_MARKERS) + ")"
    # lower for matching
    lower = joined.lower()
    indices = []
    for m in re.finditer(pattern, lower):
        indices.append((m.start(), m.group(0)))
    if not indices:
        return {"raw": joined}
    sections = {}
    for i, (pos, hdr) in enumerate(indices):
        start = pos
        end = indices[i+1][0] if i+1 < len(indices) else len(joined)
        snippet = joined[start:end].strip()
        # header line often within snippet beginning; split by newline
        first_line = snippet.splitlines()[0].lower()
        sections[first_line] = snippet
    # fallback - put everything as raw if sections empty
    sections["raw"] = joined
    return sections

# --- Name extraction (heuristic) --------------------------------------------
def extract_name(text):
    doc = nlp(text.strip().split("\n")[0])  # try first line
    names = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    if names:
        return names[0]
    # fallback: use largest capitalized token sequence in top 5 lines
    top = "\n".join(text.splitlines()[:6])
    candidates = re.findall(r"\b([A-Z][a-z]{1,}\s(?:[A-Z][a-z]{1,}\s?)*)\b", top)
    return candidates[0].strip() if candidates else None

# --- Education & Experience parsing (very naive, rule-based) ---------------
def parse_education(section_text):
    # find lines with degree keywords and year
    degrees = []
    degree_keywords = ["bachelor", "master", "phd", "b\.tech", "m\.tech", "b\.e", "m\.e", "mba", "bs", "ms"]
    for line in section_text.splitlines():
        low = line.lower()
        if any(k in low for k in degree_keywords) or re.search(r"\b\d{4}\b", line):
            degrees.append(line.strip())
    return degrees

def parse_experience(section_text):
    exps = []
    lines = [l for l in section_text.splitlines() if l.strip()]
    # naive grouping: lines that start with uppercase likely titles
    cur = {}
    for line in lines:
        # detect date range
        if re.search(r"(20\d{2}|19\d{2})", line):
            if cur:
                exps.append(cur)
                cur = {}
            cur["dates"] = line.strip()
        elif len(line.split()) <= 6 and line[0].isupper():
            # likely "Company — Role" or "Role at Company"
            if cur:
                exps.append(cur)
                cur = {}
            cur["title_or_company"] = line.strip()
        else:
            cur.setdefault("description", "")
            cur["description"] += " " + line.strip()
    if cur:
        exps.append(cur)
    return exps

# --- Skills matching --------------------------------------------------------
SKILL_VOCAB = [
    "python", "java", "c++", "pytorch", "tensorflow", "scikit-learn", "numpy",
    "pandas", "sql", "docker", "kubernetes", "aws", "azure", "nlp", "computer vision",
    "opencv", "matplotlib", "seaborn", "react", "nodejs", "flask", "django"
]

def extract_skills(text, vocab=SKILL_VOCAB, score_cutoff=70):
    tokens = re.findall(r"[A-Za-z#+\-\.]+", text.lower())
    joined = " ".join(tokens)
    found = set()
    for s in vocab:
        match = process.extractOne(s, [joined], scorer=fuzz.partial_ratio)
        # process.extractOne with single item is odd; use direct presence or fuzzy
        if s in joined:
            found.add(s)
        else:
            # fuzzy substring check
            score = fuzz.partial_ratio(s, joined)
            if score >= score_cutoff:
                found.add(s)
    return sorted(found)

# --- Main --------------------------------------------------------------
def parse_resume(path):
    text = extract_text(path)
    text = re.sub(r"\n{2,}", "\n", text)  # normalize
    email = extract_email(text)
    phones = extract_phones(text)
    sections = split_sections(text)
    name = extract_name(text) or ""
    skills = extract_skills(text)
    education = parse_education(sections.get("education", sections.get("education", "")) if isinstance(sections, dict) else "")
    experience = parse_experience(sections.get("experience", sections.get("work experience", "")) if isinstance(sections, dict) else "")

    out = {
        "name": name,
        "email": email,
        "phones": phones,
        "skills": skills,
        "education": education,
        "experience": experience,
        # "sections": sections,
        # "raw_text_snippet": text[:2000]
    }
    return out

# CLI
if __name__ == "__main__":
    import sys
    print("Enter the path to the resume: ")
    p = r"C:\Users\sanke\Downloads\final_Sankeerth_Resume.pdf"
    parsed = parse_resume(p)
    print(json.dumps(parsed, indent=2))
