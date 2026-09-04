import os, logging, re, difflib, json
logger = logging.getLogger(__name__)

FALLBACK_IDEAS = [
    ("Spaza Shop Stock Tracker", "Build a spaza shop stock tracker — add, sell, low-stock alert. Zulu/English mix."),
    ("Taxi Rank Queue App", "Taxi rank queue — take number, see position, SMS when taxi ready."),
    ("School Fees Tracker", "School fees tracker for parents — pay, see balance, receipt PDF."),
    ("Church Donation Landing", "Church donation landing in Setswana — hero, story, Paystack donate."),
    ("Clinic Booking in isiZulu", "Clinic booking — pick date, doctor, SMS reminder in isiZulu."),
    ("Street Food Menu", "Street food menu — kota, bunny chow, daily special, WhatsApp order."),
    ("Burial Society Tracker", "Burial society tracker — contributions, payouts, member list, SMS reminder."),
    ("Salon Booking App", "Salon booking — choose style, stylist, time, pay deposit via Paystack."),
    ("Car Wash Queue", "Car wash queue — take ticket, see wait time, notify when bay ready."),
    ("Community Garden Planner", "Community garden planner — plot map, planting calendar, harvest log."),
]

def get_past_challenge_titles():
    try:
        from .models import Challenge
        return list(Challenge.objects.values_list('title', flat=True))
    except Exception:
        return []

def is_duplicate(new_title, past_titles, cutoff=0.7):
    """Check if new_title is too similar to past — crush silently."""
    try:
        for past in past_titles:
            ratio = difflib.SequenceMatcher(None, new_title.lower(), past.lower()).ratio()
            if ratio > cutoff:
                return True
            if difflib.get_close_matches(new_title.lower(), [past.lower()], n=1, cutoff=cutoff):
                return True
        return False
    except Exception:
        return False

def generate_challenge_candidates(past_titles, n=3):
    """Try Gemini, then Groq, then fallback list — backend only, crush silently."""
    candidates = []
    prompt_base = f"""You are BlaqVibes challenge maker for Durban vibe coders.
Past challenges (do NOT repeat, must be different): {past_titles}

Generate {n} NEW challenges. Each must be:
- Title: short, 3-6 words
- Description: 1 sentence, what to build, mention Tailwind/Django/React and local context (Setswana, isiZulu, spaza, taxi)
- Bounty: 10 stars
- Tag: challenge-week-{{number}}

Return ONLY JSON array: [{{"title":"...", "description":"...", "bounty_stars":10, "tag":"challenge-week-13"}}]
Make them different from past and from each other.
"""
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt_base, generation_config={"temperature":0.7, "max_output_tokens":600})
            txt = getattr(resp, 'text', '') or ""
            m = re.search(r'\[.*\]', txt, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                for item in data[:n]:
                    title = item.get('title','').strip()
                    if title and not is_duplicate(title, past_titles + [c['title'] for c in candidates]):
                        candidates.append({"title": title, "description": item.get('description','')[:200], "bounty_stars": 10, "tag": item.get('tag', f"challenge-week-{len(past_titles)+len(candidates)+13}")})
                if len(candidates) >= n:
                    return candidates[:n]
        except Exception as e:
            logger.warning(f"Gemini challenge gen failed: {e}")

    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user","content":prompt_base}], max_tokens=600, temperature=0.7)
            txt = resp.choices[0].message.content or ""
            m = re.search(r'\[.*\]', txt, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                for item in data[:n]:
                    title = item.get('title','').strip()
                    if title and not is_duplicate(title, past_titles + [c['title'] for c in candidates]):
                        candidates.append({"title": title, "description": item.get('description','')[:200], "bounty_stars": 10, "tag": item.get('tag', f"challenge-week-{len(past_titles)+len(candidates)+13}")})
                if len(candidates) >= n:
                    return candidates[:n]
        except Exception as e:
            logger.warning(f"Groq challenge gen failed: {e}")

    for title, desc in FALLBACK_IDEAS:
        if not is_duplicate(title, past_titles + [c['title'] for c in candidates]):
            tag = f"challenge-week-{len(past_titles)+len(candidates)+13}"
            candidates.append({"title": title, "description": desc, "bounty_stars": 10, "tag": tag})
            if len(candidates) >= n:
                break
    return candidates[:n]

def create_draft_challenges():
    """Called by Celery beat weekly or superadmin button — creates 3 drafts, is_active=False for approval."""
    try:
        from .models import Challenge
        from django.utils import timezone
        from datetime import timedelta
        past = get_past_challenge_titles()
        cands = generate_challenge_candidates(past, n=3)
        created = []
        from .profanity import contains_profanity
        for cand in cands:
            if Challenge.objects.filter(tag=cand['tag']).exists():
                continue
            if contains_profanity(cand.get('title')) or contains_profanity(cand.get('description')):
                logger.warning('dropped draft challenge with blocked language')
                continue
            ch = Challenge.objects.create(
                title=cand['title'],
                description=cand['description'],
                bounty_stars=cand['bounty_stars'],
                tag=cand['tag'],
                start=timezone.now(),
                end=timezone.now()+timedelta(days=7),
                is_active=False,
                created_by=None
            )
            created.append(ch)
        logger.info(f"Created {len(created)} draft challenges")
        return created
    except Exception as e:
        logger.exception(f"create_draft_challenges crush: {e}")
        return []
