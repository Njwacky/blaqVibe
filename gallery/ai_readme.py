import os, logging, re
logger = logging.getLogger(__name__)

def generate_ai_readme(project):
    """Backend only, crush silently, no JS. Uses Gemini/Groq if keys, else heuristic template."""
    try:
        heuristic = f"# {project.title}\n{project.short_description}\n\n## What is this?\nThis vibe has {project.file_count} files. Tech: {project.tech_stack or '—'}.\n\n## How to Run\n```bash\npip install -r requirements.txt\npython manage.py runserver\n```\n\n## Features\n- {', '.join(list(project.language_stats.keys())[:3]) if project.language_stats else 'See file tree'}\n"
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel("gemini-1.5-flash")
                prompt = f"Write a markdown README for BlaqVibes vibe. Title: {project.title}\nTech: {project.tech_stack}\nFiles: {list(project.file_tree.keys())[:10] if project.file_tree else []}\nLanguages: {project.language_stats}\nShort: {project.short_description}\n\nReturn ONLY markdown README with # Title, ## What is this?, ## Tech Stack, ## How to Run (code block), ## Features."
                resp = model.generate_content(prompt, generation_config={"temperature":0.3, "max_output_tokens":600})
                txt = getattr(resp, 'text', '') or ""
                if txt and "# " in txt:
                    return txt.strip()[:5000]
            except Exception as e:
                logger.warning(f"Gemini readme failed: {e}")
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                client = Groq(api_key=groq_key)
                prompt = f"Write markdown README for {project.title} — tech {project.tech_stack}, files {project.file_count}, languages {project.language_stats}"
                resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user","content":prompt}], max_tokens=600, temperature=0.3)
                txt = resp.choices[0].message.content or ""
                if txt and "# " in txt:
                    return txt.strip()[:5000]
            except Exception as e:
                logger.warning(f"Groq readme failed: {e}")
        return heuristic
    except Exception as e:
        logger.exception(f"ai_readme crush: {e}")
        return f"# {project.title}\n{project.short_description}\n"
