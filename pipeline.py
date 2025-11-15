# pipeline.py
from resumeParser import parse_resume
from formatter import pretty_markdown
import sys
import json

def main(resume_path):
    parsed = parse_resume(resume_path)        # returns dict
    # optionally save it:
    with open("parsed.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)
    # pretty print to stdout (or save to file)
    md = pretty_markdown(parsed)
    print(md)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py path/to/resume.pdf")
        sys.exit(1)
    resume_path = sys.argv[1]
    main(resume_path)
