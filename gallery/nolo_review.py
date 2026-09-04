import os, logging, json, re
logger = logging.getLogger(__name__)


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
        try:
            has_req = any("requirements" in p.lower() for p in (project.file_tree or {}).keys()) or "requirements" in readme.lower()
            if has_req:
                score += 1
                pros.append("Has requirements.txt")
            else:
                fixes.append("Add requirements.txt or package.json")
        except Exception: pass
        score = max(0, min(10, score))
        return {"score": score, "fixes": fixes[:3], "pros": pros[:3], "source": "heuristic"}
    except Exception as e:
        logger.exception(f"heuristic_review crush: {e}")
        return {"score": 5, "fixes": [], "pros": [], "source": "heuristic"}

def nolo_review(project):
    """Try Gemini (free) first, then Groq (free, fastest), then OpenAI, then heuristic — crush silently, backend only."""
    try:
        heuristic = heuristic_review(project)
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel("gemini-1.5-flash")
                prompt = f"Review this vibe for BlaqVibes. Title: {project.title}\nTech: {project.tech_stack}\nFiles: {project.file_count}\nLanguages: {project.language_stats}\nREADME:\n{project.readme[:2000]}\n\nReturn ONLY JSON: {{\"score\": 0-10, \"fixes\": [3 strings], \"pros\": [3 strings]}}"
                resp = model.generate_content(prompt, generation_config={"temperature":0.2, "max_output_tokens":400})
                txt = getattr(resp, 'text', '') or ""
                m = re.search(r'\{.*\}', txt, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    return {"score": int(data.get("score", heuristic["score"])), "fixes": data.get("fixes", heuristic["fixes"])[:3], "pros": data.get("pros", heuristic["pros"])[:3], "source": "gemini"}
            except Exception as e:
                logger.warning(f"Gemini review failed, try Groq/OpenAI/heuristic: {e}")
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                client = Groq(api_key=groq_key)
                prompt = f"Review this vibe for BlaqVibes. Title: {project.title}\nTech: {project.tech_stack}\nFiles: {project.file_count}\nLanguages: {project.language_stats}\nREADME:\n{project.readme[:2000]}\n\nReturn ONLY JSON: {{\"score\": 0-10, \"fixes\": [3 strings], \"pros\": [3 strings]}}"
                resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user","content":prompt}], max_tokens=400, temperature=0.2)
                txt = resp.choices[0].message.content or ""
                m = re.search(r'\{.*\}', txt, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    return {"score": int(data.get("score", heuristic["score"])), "fixes": data.get("fixes", heuristic["fixes"])[:3], "pros": data.get("pros", heuristic["pros"])[:3], "source": "groq"}
            except Exception as e:
                logger.warning(f"Groq review failed, try OpenAI/heuristic: {e}")
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            try:
                import openai
                openai.api_key = api_key
                prompt = f"Review this vibe for BlaqVibes. Title: {project.title}\nTech: {project.tech_stack}\nFiles: {project.file_count}\nLanguages: {project.language_stats}\nREADME:\n{project.readme[:2000]}\n\nReturn JSON: {{\"score\": 0-10, \"fixes\": [3 strings], \"pros\": [3 strings]}}"
                resp = openai.ChatCompletion.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], max_tokens=300, temperature=0.2)
                txt = resp.choices[0].message.content
                m = re.search(r'\{.*\}', txt, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    return {"score": int(data.get("score", heuristic["score"])), "fixes": data.get("fixes", heuristic["fixes"])[:3], "pros": data.get("pros", heuristic["pros"])[:3], "source": "openai"}
            except Exception as e:
                logger.warning(f"OpenAI review failed, fallback heuristic: {e}")
        return heuristic
    except Exception as e:
        logger.exception(f"nolo_review crush: {e}")
        return {"score": 5, "fixes": [], "pros": [], "source": "error"}
