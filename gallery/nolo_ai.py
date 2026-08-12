import os, json, re, logging
logger = logging.getLogger(__name__)

def get_nolo_ai_answer(prompt):
    prompt_text = f"Answer this BlaqVibes question in a short helpful way:\n\n{prompt}\n\nKeep the answer concise and friendly."
    # Prefer Gemini if available
    gemini_key = os.getenv('GEMINI_API_KEY', '')
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            resp = model.generate_content(prompt_text, generation_config={'temperature':0.4, 'max_output_tokens':300})
            return getattr(resp, 'text', '') or str(resp)
        except Exception as e:
            logger.warning(f'Gemini chat failed: {e}')
    # Try Groq if available
    groq_key = os.getenv('GROQ_API_KEY', '')
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(model='llama-3.1-8b-instant', messages=[{'role':'user','content': prompt_text}], max_tokens=300, temperature=0.4)
            return resp.choices[0].message.content
        except Exception as e:
            logger.warning(f'Groq chat failed: {e}')
    # Try OpenAI if available
    openai_key = os.getenv('OPENAI_API_KEY', '')
    if openai_key:
        try:
            import openai
            openai.api_key = openai_key
            resp = openai.ChatCompletion.create(model='gpt-4o-mini', messages=[{'role':'user','content': prompt_text}], max_tokens=300, temperature=0.4)
            return resp.choices[0].message.content
        except Exception as e:
            logger.warning(f'OpenAI chat failed: {e}')
    # Fallback heuristic
    return _heuristic_fallback(prompt_text)


def _heuristic_fallback(prompt):
    prompt = prompt.lower()
    if 'new apps' in prompt or 'new app' in prompt or 'latest' in prompt:
        return 'Check the latest published vibes section on this page for the newest apps and templates. You can also filter by category to find fresh content.'
    if 'template' in prompt or 'react' in prompt or 'vue' in prompt or 'html' in prompt:
        return 'Look for published vibes with a tech stack that matches your needs. React and Vue templates are usually tagged with those frameworks, while plain HTML/CSS/JS apps are best for quick remixing.'
    if 'compare' in prompt or 'easy' in prompt or 'fork' in prompt:
        return 'Use the Nolo compare tool on an app page to compare features, file count, and tech stack. The easiest vibes to fork are the ones with few files and a clear README.'
    return 'Ask about new apps, templates, or which vibe is easiest to fork. If you want, mention a technology like React, Django, or plain HTML.'
