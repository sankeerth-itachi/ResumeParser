#!/usr/bin/env python3
"""
ai_module_llm_validation.py

Sends resume text (extracted by your local parser) to an LLM endpoint and **lets the LLM decide**
whether the file is a resume. The LLM MUST return ONLY a JSON object matching the schema below,
or {} if the input is NOT a resume.

Place this next to ResumeParserModel2.py which must expose parse_resume(path) -> dict.

Usage:
  python ai_module_llm_validation.py /path/to/resume.pdf --endpoint https://your-llm-endpoint --out final.json

Config:
  - Provide endpoint via --endpoint or GEMINI_ENDPOINT env var
  - Provide API key via --key or GEMINI_API_KEY env var
"""

import os
import re
import json
import argparse
from pathlib import Path
from typing import Optional, Dict, Any
import requests

# import your parser function (must exist)
try:
    from ResumeParserModel2 import parse_resume
except Exception as e:
    raise ImportError("Failed to import parse_resume from ResumeParserModel2.py: " + str(e))

# expected keys (for minor sanity heuristics only)
EXPECTED_KEYS = {
    "name", "email", "phone_number", "skill_set", "experience",
    "education", "projects", "total_experience_years",
    "achievements", "certifications", "technical_skills", "soft_skills"
}

# helper to find JSON object inside a text blob
def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    # try direct
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # brute-force balanced braces extraction
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    # continue search (in case of nested junk)
                    continue
    return None

# Build the prompt / payload that tells the model to validate and return only the schema or {}
def build_payload_for_model(parsed_basic: Dict[str, Any]) -> Dict[str, Any]:
    instruction = (
        "You are a STRICT JSON-only extractor/validator for resumes. "
        "You will be given the raw text of a document and some basic pre-extracted fields. "
        "Your job: decide whether the document is a resume. "
        "If it IS a resume, RETURN ONLY a JSON object with the EXACT keys listed below, no extra text, no explanation, no markdown. "
        "If it is NOT a resume (random junk, invoice, advertisement, cover letter alone, corrupted file, etc.), RETURN ONLY an empty JSON object: {}.\n\n"

        "Exact required keys (use these exact names):\n"
        "- name (string or null),\n"
        "- email (string or null),\n"
        "- phone_number (string or null),\n"
        "- skill_set (array of strings),\n"
        "- experience (array of objects: each object has title, company, start, end, description),\n"
        "- education (array of objects: degree, institution, start, end, description),\n"
        "- projects (array of objects: title, description, start, end, technologies),\n"
        "- total_experience_years (number, float),\n"
        "- achievements (array of strings),\n"
        "- certifications (array of strings),\n"
        "- technical_skills (array of strings),\n"
        "- soft_skills (array of strings)\n\n"

        "If you cannot determine a field, set it to null or an empty array as appropriate. Be conservative: do not invent details. "
        "Now examine the provided 'parsed_basic' object and produce the JSON output as specified.\n\n"
    )
    return {
        "model_input": {
            "instruction": instruction,
            "parsed_basic": parsed_basic
        }
    }

# Call LLM endpoint; return parsed JSON (or raise)
def call_llm(endpoint: str, api_key: Optional[str], payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    # try direct JSON
    try:
        j = resp.json()
        # If endpoint returned a dict that already looks like our final JSON (has expected keys or is empty), return appropriately
        if isinstance(j, dict):
            # If it's exactly empty -> model says not a resume
            if not j:
                return {}
            # if it contains our expected keys, assume this is final JSON
            if EXPECTED_KEYS.intersection(set(j.keys())):
                return j
            # otherwise, maybe it's an envelope; try to find JSON inside string fields below
    except Exception:
        j = None

    # fallback: parse text response and try to extract JSON substring
    text = resp.text
    extracted = extract_json_from_text(text)
    if extracted is not None:
        return extracted
    # if nothing found, raise to help debugging
    raise RuntimeError("LLM response did not contain a parsable JSON object")

# top-level process function — NOTE: no local judgement about resume-ness
def process_with_llm_validation(input_path: str, endpoint: str, api_key: Optional[str] = None, save_diagnostic: bool = True) -> Dict[str, Any]:
    p = Path(input_path)
    if not p.exists():
        raise FileNotFoundError(input_path)

    # 1) Use your parser to extract text and basic fields
    parsed_basic = parse_resume(str(p))
    # ensure raw_text exists for context
    if "raw_text" not in parsed_basic:
        try:
            parsed_basic["raw_text"] = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            parsed_basic["raw_text"] = ""

    # 2) Build the model payload and call the LLM
    payload = build_payload_for_model(parsed_basic)
    raw_model_output = call_llm(endpoint, api_key, payload)

    # 3) If model returns {} -> it's not a resume (per your request). Return {}.
    if isinstance(raw_model_output, dict) and not raw_model_output:
        # optional diagnostic
        if save_diagnostic:
            Path(str(p) + ".diagnostic_model_empty.json").write_text(json.dumps({"parsed_basic": parsed_basic}, indent=2), encoding="utf-8")
        return {}

    # 4) If model returned a dict — return it (no further 'is-resume' checks)
    if isinstance(raw_model_output, dict):
        # Save diagnostic if requested
        if save_diagnostic:
            Path(str(p) + ".diagnostic_model.json").write_text(json.dumps({"parsed_basic": parsed_basic, "model_output": raw_model_output}, indent=2), encoding="utf-8")
        return raw_model_output

    # Shouldn't reach here — defensive
    raise RuntimeError("Unexpected non-dict model output")

# CLI
def main():
    ap = argparse.ArgumentParser(description="Send resume parsed_basic to LLM and let the LLM decide if it's a resume (returns schema or {}).")
    ap.add_argument("input", help="Input resume file (.pdf, .docx, .txt)")
    ap.add_argument("--endpoint", help="LLM endpoint URL (or set GEMINI_ENDPOINT env var)")
    ap.add_argument("--key", help="API key / Bearer token (or set GEMINI_API_KEY env var)")
    ap.add_argument("--out", "-o", help="Output JSON file to save the LLM's response (optional)")
    args = ap.parse_args()

    endpoint = args.endpoint or os.environ.get("GEMINI_ENDPOINT")
    api_key = args.key or os.environ.get("GEMINI_API_KEY")
    if not endpoint:
        raise RuntimeError("No endpoint provided. Use --endpoint or set GEMINI_ENDPOINT env var")

    result = process_with_llm_validation(args.input, endpoint, api_key)
    out_text = json.dumps(result, indent=2, ensure_ascii=False)
    print(out_text)
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"Saved output to {args.out}")

if __name__ == "__main__":
    main()
