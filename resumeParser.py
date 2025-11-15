# resume_parser.py
from datetime import datetime
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

# === URL extraction ===
URL_RE = re.compile(r"https?://\S+|www\.\S+")
SOCIAL_HOSTS = ["linkedin.com", "github.com", "gitlab.com", "behance.net", "dribbble.com", "medium.com", "portfolio"]

def extract_urls(text):
    urls = set(m.group(0).rstrip('.,;') for m in URL_RE.finditer(text))
    profile = {"linkedin": None, "github": None, "portfolio": None, "other": []}
    for u in urls:
        lu = u.lower()
        if "linkedin.com" in lu:
            profile["linkedin"] = u
        elif "github.com" in lu:
            profile["github"] = u
        elif any(host in lu for host in ["behance", "dribbble", "medium", "portfolio", "gitlab"]):
            profile["portfolio"] = profile["portfolio"] or u
        else:
            profile["other"].append(u)
    return profile


# === Summary / Objective ===
def extract_summary(text, max_lines=6):
    # take the text before first recognized section header
    lower = text.lower()
    sec_pos = len(text)
    for marker in SECTION_MARKERS:
        m = re.search(marker, lower)
        if m:
            sec_pos = min(sec_pos, m.start())
    pre = text[:sec_pos].strip()
    lines = [l for l in pre.splitlines() if l.strip()]
    # often summary is first 1-3 lines with 30-200 chars
    candidate_lines = lines[:max_lines]
    joined = " ".join(candidate_lines)
    # if joined is too long, keep only first sentence
    if len(joined) > 400:
        return joined.split(".")[0] + "."
    return joined

# === Certifications & Projects ===
CERT_KEY = re.compile(r"\b(certif|certificat|certificate|certified)\b", re.I)
PROJECT_KEY = re.compile(r"\bprojects?\b", re.I)

def extract_certifications(text):
    certs = []
    # search for lines with certification keywords or common cert names
    for line in text.splitlines():
        if CERT_KEY.search(line) or any(k in line.lower() for k in ["aws certified", "google cloud", "tensorflow", "professional certificate", "coursera", "udemy"]):
            line = line.strip(" -•\t")
            if 5 < len(line) < 200:
                certs.append(line)
    # also look inside 'certifications' section if present
    # naive section extraction:
    lower = text.lower()
    idx = lower.find("cert")
    if idx != -1:
        chunk = text[idx: idx + 800]  # grab a window
        for l in chunk.splitlines():
            if l.strip() and len(l.strip())>3:
                if l.strip() not in certs:
                    certs.append(l.strip())
    return certs


def extract_projects(sections):
    # sections may be dict from split_sections(); prefer 'projects' key
    proj_text = ""
    for k in sections.keys():
        if "project" in k.lower():
            proj_text = sections[k]
            break
    if not proj_text:
        # fallback: look for "project" marker anywhere
        whole = "\n".join(sections.values()) if isinstance(sections, dict) else sections
        m = PROJECT_KEY.search(whole)
        if m:
            proj_text = whole[m.start(): m.start()+1000]
    projects = []
    for line in proj_text.splitlines():
        line = line.strip(" -•\t")
        if not line: continue
        # likely a project line if it contains ':' or '—' or 'http' or has a short title
        if ":" in line or "—" in line or "http" in line or len(line.split()) < 12:
            projects.append(line)
    # dedupe
    return list(dict.fromkeys(projects))

# === Locations (spaCy GPE + heuristics) ===
def extract_locations(text, top_n=5):
    doc = nlp(text)
    locs = []
    for ent in doc.ents:
        if ent.label_ in ("GPE", "LOC"):
            locs.append(ent.text)
    # fallback: look for "City, State" patterns
    pattern = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z]{2}|[A-Za-z]{2,})\b")
    for m in pattern.finditer(text):
        locs.append(m.group(0))
    # return top unique
    uniq = []
    for l in locs:
        if l not in uniq:
            uniq.append(l)
        if len(uniq) >= top_n:
            break
    return uniq

# === Role title extraction (heuristic) ===
TITLE_KEYWORDS = [
    "engineer","developer","scientist","researcher","intern","manager","lead","principal",
    "analyst","architect","consultant","director","assistant","associate","trainer","specialist"
]

def extract_role_titles(section_text):
    titles = []
    # scan lines for title-like patterns
    for line in section_text.splitlines():
        clean = line.strip(" -•\t")
        if not clean: continue
        low = clean.lower()
        # pattern: "Title — Company" or "Title at Company" or contains a title keyword
        if " at " in low or "—" in clean or "|" in clean or any(k in low for k in TITLE_KEYWORDS):
            # try to isolate the title portion
            parts = re.split(r"—|-|–|\|| at ", clean)
            title_cand = parts[0].strip()
            # short filter
            if 2 <= len(title_cand.split()) <= 6 and len(title_cand) < 80:
                titles.append(title_cand)
    # spaCy fallback: look for PERSON? no — look for nouns preceding ORG
    doc = nlp(section_text)
    for sent in doc.sents:
        for ent in sent.ents:
            if ent.label_ == "ORG":
                # take preceding noun chunk
                start = ent.start
                if start > 0:
                    nc = doc[max(0, start-6): start].text.strip()
                    if any(k in nc.lower() for k in TITLE_KEYWORDS):
                        titles.append(nc)
    # dedupe and return
    out = []
    for t in titles:
        if t not in out:
            out.append(t)
    return out

# === Years of experience (approx) ===
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
MONTH_YEAR_RE = re.compile(r"(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)\s*,?\s*(\d{4})", re.I)
RANGE_RE = re.compile(r"(?P<start>[\w\s\.\-\/]+?)\s*(?:to|[-–—]|–)\s*(?P<end>[\w\s\.\-\/]+)", re.I)

def parse_year_from_token(tok):
    # tok might be 'Jan 2018' or '2018'
    m = YEAR_RE.search(tok)
    if m:
        return int(m.group(0))
    my = MONTH_YEAR_RE.search(tok)
    if my:
        return int(my.group(2))
    return None

def estimate_years_experience(text):
    now_year = datetime.now().year
    years = []
    # find ranges like "Jan 2018 - Mar 2020" or "2015 - present"
    for m in RANGE_RE.finditer(text):
        s = m.group("start")
        e = m.group("end")
        sy = parse_year_from_token(s) or parse_year_from_token(e) or None
        ey = None
        if re.search(r"present|current|now", e, re.I):
            ey = now_year
        else:
            ey = parse_year_from_token(e)
        if sy and ey:
            if ey >= sy:
                years.append((sy, ey))
    # if no ranges found, fallback to scanning for years and assume earliest->latest
    if not years:
        found = sorted({int(y.group(0)) for y in YEAR_RE.finditer(text)})
        if found:
            years = [(found[0], found[-1] if found[-1] <= now_year else now_year)]
    if not years:
        return 0.0
    # compute span: earliest start to latest end
    earliest = min(s for s,e in years)
    latest = max(e for s,e in years)
    duration = latest - earliest
    # return float years
    return float(duration)

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

RANGE_RE = re.compile(r"(?P<start>[\w\s\.\-\/]+?)\s*(?:to|[-–—]|–)\s*(?P<end>[\w\s\.\-\/]+)", re.I)
DATE_WORDS = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4}|present|current)\b", re.I)

def parse_experience(section_text):
    if not section_text:
        return []
    lines = [l.strip() for l in section_text.splitlines() if l.strip()]
    entries = []
    cur = None
    for line in lines:
        # skip lines that are obvious headers/labels
        if re.fullmatch(r"(experience|work experience|professional experience|personal|internships|projects|skills)", line.lower()):
            continue
        # if line looks like a date-only or contains date range, start new entry
        if DATE_WORDS.search(line) and (RANGE_RE.search(line) or re.search(r"\b(19|20)\d{2}\b", line)):
            # new entry: attach date info
            if cur:
                entries.append(cur)
            cur = {"dates": line.strip(), "title": None, "company": None, "description": ""}
            continue
        # if line contains separators indicating Title — Company or Title, Company
        if re.search(r"—|-|—|, at | at | \| ", line) and len(line.split()) <= 12:
            parts = re.split(r"—|-|–|\||, at | at ", line)
            # first part likely title, second company
            title = parts[0].strip()
            company = parts[1].strip() if len(parts) > 1 else None
            if cur and not cur.get("title"):
                cur = cur or {}
                cur.update({"title": title, "company": company})
                continue
            else:
                # start new entry
                if cur:
                    entries.append(cur)
                cur = {"dates": None, "title": title, "company": company, "description": ""}
                continue
        # generic description line: attach to current entry; if none, start a loose entry
        if not cur:
            cur = {"dates": None, "title": None, "company": None, "description": line}
        else:
            cur["description"] = (cur.get("description","") + " " + line).strip()
    if cur:
        entries.append(cur)
    # filter out entries that are just header garbage (like single word "PERSONAL")
    filtered = []
    for e in entries:
        # if entry has no useful info (no title/company/description/dates) skip
        if not (e.get("title") or e.get("company") or e.get("description") or e.get("dates")):
            continue
        # drop entries that are exactly "PERSONAL" or "EXPERIENCE"
        if (e.get("title") and e["title"].strip().upper() in ("EXPERIENCE", "PERSONAL", "PROJECTS")):
            continue
        filtered.append(e)
    return filtered


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
    urls = extract_urls(text)
    summary = extract_summary(text)
    certifications = extract_certifications(text)
    projects = extract_projects(sections)
    locations = extract_locations(text)
    role_titles = extract_role_titles(sections.get('experience', '') if isinstance(sections, dict) else '')
    years_exp = estimate_years_experience(sections.get('experience', '') if isinstance(sections, dict) else text)
    
    
    out = {
        "name": name,
        "email": email,
        "phones": phones,
        "skills": skills,
        "education": education,
        "experience": experience,
        # "sections": sections,
        "linkedin": urls["linkedin"], "github": urls["github"], "portfolio": urls["portfolio"],
    "certifications": certifications, "projects": projects, "summary": summary,
    "locations": locations, "role_titles": role_titles, "years_experience": years_exp

        # "raw_text_snippet": text[:2000]
    }
    return out

# CLI
if __name__ == "__main__":
    import sys
    print("Enter the path to the resume: ")
    p = r"C:\Users\sanke\Desktop\parser\sample_resumes\final_Sankeerth_Resume.pdf"
    parsed = parse_resume(p)
    print(json.dumps(parsed, indent=2))
