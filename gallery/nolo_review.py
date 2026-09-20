import os, logging, json, re
logger = logging.getLogger(__name__)

def _env(name):
    try:
        from django.conf import settings
        value = getattr(settings, name, '') or os.getenv(name, '')
    except Exception:
        value = os.getenv(name, '')
    return (value or '').strip()

# Heuristic fallback: with no AI key in dev there's still value, and it
# degrades silently.

def heuristic_review(project):
    """Backend only, no JS, no LLM key needed — checks for quality signals."""
    try:
        score = 5
        fixes = []
        pros = []
        readme = project.readme or ""
        if "# " in readme and len(readme) > 300:
            score += 2
            pros.append("Good README with heading")
        else:
            fixes.append("Add a clear README with # heading and how to run")
        if project.file_count and project.file_count > 5:
            score += 1
            pros.append(f"{project.file_count} files — complete")
        else:
            fixes.append("Add more files (at least 5) — e.g., requirements.txt, views.py")
        if project.tech_stack:
            score += 1
            pros.append(f"Tech stack declared: {project.tech_stack[:40]}")
        else:
            fixes.append("Declare tech stack (e.g., Django, React)")
        if project.language_stats:
            pros.append(f"Languages: {', '.join(project.language_stats.keys())}")
        # Check for requirements
        try:
            has_req = any("requirements" in p.lower() for p in (project.file_tree or {}).keys()) or "requirements" in readme.lower()
            if has_req:
                score += 1
                pros.append("Has requirements.txt")
            else:
                fixes.append("Add requirements.txt or package.json")
        except Exception: pass
        score = max(0, min(10, score))
        # Ensure 3 fixes max, 3 pros
        return {"score": score, "fixes": fixes[:3], "pros": pros[:3], "source": "heuristic"}
    except Exception as e:
        logger.exception(f"heuristic_review crush: {e}")
        return {"score": 5, "fixes": [], "pros": [], "source": "heuristic"}

def _review_prompt(project):
    return f"Review this vibe for BlaqVibes. Title: {project.title}\nTech: {project.tech_stack}\nFiles: {project.file_count}\nLanguages: {project.language_stats}\nREADME:\n{project.readme[:2000]}\n\nReturn ONLY JSON: {{\"score\": 0-10, \"fixes\": [3 strings], \"pros\": [3 strings]}}"

def _parse_review(txt, heuristic, source):
    """Model text → review dict, or None when there is no JSON object in it."""
    m = re.search(r'\{.*\}', txt or '', re.DOTALL)
    if not m:
        return None
    data = json.loads(m.group(0))
    return {"score": int(data.get("score", heuristic["score"])), "fixes": data.get("fixes", heuristic["fixes"])[:3], "pros": data.get("pros", heuristic["pros"])[:3], "source": source}

def nolo_review(project):
    """Try OpenRouter/OpenAI first, then Gemini (free), then Groq (free, fastest), then heuristic — crush silently, backend only."""
    try:
        heuristic = heuristic_review(project)
        prompt = _review_prompt(project)
        # 1. First preference: OpenRouter / OpenAI
        from .ai_providers import openai_compatible_text, preferred_backend
        preferred = preferred_backend()
        if preferred:
            try:
                review = _parse_review(openai_compatible_text(prompt, max_output_tokens=400, temperature=0.2), heuristic, preferred)
                if review:
                    return review
            except Exception as e:
                logger.warning(f"{preferred} review failed, try Gemini/Groq/heuristic: {e}")
        # 2. Try Gemini free
        gemini_key = _env("GEMINI_API_KEY")
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel("gemini-1.5-flash")
                resp = model.generate_content(prompt, generation_config={"temperature":0.2, "max_output_tokens":400})
                review = _parse_review(getattr(resp, 'text', '') or "", heuristic, "gemini")
                if review:
                    return review
            except Exception as e:
                logger.warning(f"Gemini review failed, try Groq/heuristic: {e}")
        # 3. Try Groq (free, fastest)
        groq_key = _env("GROQ_API_KEY")
        if groq_key:
            try:
                from .ai_providers import groq_text
                review = _parse_review(groq_text(prompt, max_output_tokens=400, temperature=0.2), heuristic, "groq")
                if review:
                    return review
            except Exception as e:
                logger.warning(f"Groq review failed, fallback heuristic: {e}")
        return heuristic
    except Exception as e:
        logger.exception(f"nolo_review crush: {e}")
        return {"score": 5, "fixes": [], "pros": [], "source": "error"}
