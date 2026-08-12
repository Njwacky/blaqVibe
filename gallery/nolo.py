# Nolo Compare — Backend only, no judgment, just facts side-by-side
# 5 Whys: Why not LLM? LLM judges. Why backend extraction? Markdown is spec, parse features deterministically.

def extract_features(project):
    try:
        readme = (project.readme or '').lower()
        features = []
        keywords = {
            'chart': 'Chart', 'auth': 'Auth', 'api': 'API', 'tailwind': 'Tailwind', 'react': 'React', 'django': 'Django',
            'stripe': 'Stripe', 'realtime': 'Realtime', 'websocket': 'WebSocket', 'csv': 'CSV', 'tradingview': 'TradingView',
            'twelve': 'TwelveData', 'table': 'Table', 'dashboard': 'Dashboard'
        }
        for k, label in keywords.items():
            if k in readme or k in (project.tech_stack or '').lower():
                features.append(label)
        return sorted(set(features))
    except Exception:
        return []  # crush silently

def compare_apps(a, b):
    try:
        def info(p):
            try:
                lang = p.language_stats if getattr(p, 'language_stats', None) else {}
                return {
                    'title': p.title,
                    'slug': p.slug,
                    'tech_stack': p.tech_stack,
                    'languages': lang,
                    'file_count': p.file_count,
                    'stars': p.stars,
                    'clones': p.clones,
                    'features': extract_features(p),
                    'readme_len': len(p.readme or ''),
                }
            except Exception:
                return {'title': getattr(p,'title','?'), 'slug': getattr(p,'slug','?'), 'features': []}
        ia, ib = info(a), info(b)
        diff = {
            'only_in_a': [f for f in ia['features'] if f not in ib['features']],
            'only_in_b': [f for f in ib['features'] if f not in ia['features']],
            'common': [f for f in ia['features'] if f in ib['features']],
        }
        return {'a': ia, 'b': ib, 'diff': diff}
    except Exception:
        return {'a': {}, 'b': {}, 'diff': {'only_in_a':[],'only_in_b':[],'common':[]}}  # crush silently
