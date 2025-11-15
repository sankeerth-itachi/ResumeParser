# local_formatter.py
import json
from datetime import datetime
from resumeParser import parse_resume


def pretty_markdown(parsed):
    lines = []
    # header
    header = f"**{parsed.get('name','')}**  \n"
    contact = []
    if parsed.get('email'): contact.append(parsed['email'])
    if parsed.get('phones'): contact.append(", ".join(parsed['phones']))
    if parsed.get('locations'): contact.append(", ".join(parsed['locations'][:2]))
    if parsed.get('linkedin'): contact.append(parsed['linkedin'])
    if parsed.get('github'): contact.append(parsed['github'])
    header += " • ".join(contact) + "\n\n"
    lines.append(header)

    # summary
    if parsed.get('summary'):
        lines.append("**Summary**\n")
        lines.append(parsed['summary'].strip() + "\n")

    # skills
    if parsed.get('skills'):
        lines.append("**Skills**\n")
        lines.append(", ".join(parsed['skills']) + "\n")

    # experience
    if parsed.get('experience'):
        lines.append("**Experience**\n")
        for e in parsed['experience']:
            title = e.get('title') or e.get('title_or_company') or ""
            company = e.get('company') or ""
            dates = e.get('dates') or ""
            desc = e.get('description') or ""
            head = f"- **{title}**"
            if company:
                head += f", {company}"
            if dates:
                head += f"  ({dates})"
            lines.append(head + "\n")
            if desc:
                # limit long descriptions
                lines.append("  - " + (desc.strip()[:800] + ("..." if len(desc) > 800 else "")) + "\n")
        lines.append("\n")

    # education
    if parsed.get('education'):
        lines.append("**Education**\n")
        for edu in parsed['education']:
            lines.append(f"- {edu}\n")
        lines.append("\n")

    # projects
    if parsed.get('projects'):
        lines.append("**Projects**\n")
        for p in parsed['projects']:
            lines.append(f"- {p}\n")
        lines.append("\n")

    # certifications
    if parsed.get('certifications'):
        lines.append("**Certifications**\n")
        for c in parsed['certifications']:
            lines.append(f"- {c}\n")
        lines.append("\n")

    # footer: quick meta
    lines.append(f"*Parsed on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)

if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "parsed.json"
    parsed = json.load(open(p))
    print(pretty_markdown(parsed))
