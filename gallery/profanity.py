"""Public-language gate — vulgar, abusive, and hateful words never go live.

5 Whys (no shortcuts):

1. Why filter at all? Comments, reviews, titles, bios, tips, usernames and
   PRs are public. XSS sanitizers (bleach/nh3) stop scripts, not slurs.
   A gallery a stranger can open must not greet them with abuse.

2. Why reject at write time instead of silently hiding after save?
   The comments spec said "auto-hide if banned words". Hide-after-save
   still fires the owner's notification with the quote, and the author
   thinks it posted. A form error lets them reword. Nothing public is
   created. Nothing is mailed.

3. Why one module, not a list pasted into each view?
   post_comment, post_review, TipForm, SignUpForm, AppUploadForm and
   create_pr would drift the first time someone adds a field. One
   function is the one truth; every caller asks it.

4. Why word-boundaries + normalisation, not ``"ass" in text``?
   "class", "password", "assistant", "analysis", "Scunthorpe",
   "cocktail" would all die. We normalise leetspeak / separators /
   homoglyphs, then match tokens. "f.u.c.k" and "sh1t" still fail;
   "classic views" still pass.

5. Why still scan on model.save and in notify()?
   Admin, the shell, a future API, and a forgotten view can bypass a
   form. save() refuses to render the words publicly (comments hide;
   review text blanks). notify() drops a quote that slipped through
   so an inbox is never the leak. At 10k vibes this is the only
   thing that still works when a new write path appears.
"""
from __future__ import annotations

import re
import unicodedata

from django.core.exceptions import ValidationError

PUBLIC_LANGUAGE_ERROR = (
    'Please reword this. Public text on BlaqVibes cannot include '
    'vulgar, abusive, or hateful language.'
)

# Whole-token list. Keep compounds here ("asshole") rather than the short
# stem "ass" — "class" / "pass" / "asset" must stay publishable.
_BLOCKED_WORDS = frozenset({
    # English expletives / sexual abuse
    'fuck', 'fucker', 'fuckers', 'fucking', 'fucked', 'fuckhead',
    'fuk', 'fck', 'fvck', 'phuck',
    'motherfucker', 'motherfuckers', 'motherfucking',
    'shit', 'shits', 'shitty', 'bullshit', 'shithead', 'sht',
    'bitch', 'bitches', 'bitchy', 'sonofabitch',
    'cunt', 'cunts',
    'cock', 'cocks', 'cocksucker',
    'dick', 'dicks', 'dickhead',
    'pussy', 'pussies',
    'asshole', 'assholes', 'asshat', 'asswipe',
    'dumbass', 'jackass', 'smartass', 'hardass',
    'bastard', 'bastards',
    'slut', 'sluts', 'slutty',
    'whore', 'whores',
    'wank', 'wanker', 'wankers', 'wanking',
    'twat', 'twats',
    'bollocks',
    'prick', 'pricks',
    'tosser',
    'blowjob', 'handjob', 'handjobs',
    'jizz',
    # Slurs — racial, homophobic, ableist. No "reclaimed use" exception
    # on a public gallery: a stranger did not consent to read them.
    'nigger', 'niggers', 'nigga', 'niggas', 'negro',
    'faggot', 'faggots', 'fag', 'fags',
    'tranny', 'shemale',
    'retard', 'retards', 'retarded',
    'kike', 'spic', 'chink', 'gook', 'wetback',
    # South Africa — this product is Durban-first. The worst local slurs
    # and everyday Afrikaans abuse belong on the same list.
    'kaffir', 'kaffirs', 'kaffer', 'kaffers',
    'poes', 'poeslik',
    'doos',
    'fok', 'fokken', 'fokof', 'fokkol',
    'moer',
    'kak',
    'naai',
})

# Multi-word phrases that never appear as a single token.
_BLOCKED_PHRASES = (
    'son of a bitch',
    'piece of shit',
    'piece of kak',
)

# Common lookalikes used to dodge a list. Applied AFTER NFKC.
_HOMOGLYPHS = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'і': 'i', 'ѕ': 's',
    'с': 'c', 'р': 'p', 'у': 'y', 'х': 'x', 'ԁ': 'd',
    'Α': 'a', 'Ε': 'e', 'Ο': 'o', 'Ι': 'i',
    'α': 'a', 'ε': 'e', 'ο': 'o', 'ι': 'i',
})

# Leetspeak / decoration. Digits that also appear in real words are
# mapped only when we are building the *lookup* form of a token, and
# we still require the collapsed form to be an exact blocked word.
_LEET = str.maketrans({
    '0': 'o',
    '1': 'i',
    '3': 'e',
    '4': 'a',
    '5': 's',
    '7': 't',
    '8': 'b',
    '@': 'a',
    '$': 's',
    '!': 'i',
    '+': 't',
})

_NON_LETTER = re.compile(r'[^a-z]+')
_REPEAT = re.compile(r'(.)\1{2,}')


def _fold(text: str) -> str:
    """Unicode fold → lowercase Latin-ish letters and spaces only."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text)
    text = text.translate(_HOMOGLYPHS)
    text = text.lower()
    text = text.translate(_LEET)
    # Punctuation / stars / underscores become spaces so "f.u.c.k"
    # and "f*ck" tokenise instead of surviving as one unknown blob.
    text = _NON_LETTER.sub(' ', text)
    return text


def _collapse_repeats(token: str) -> str:
    """fuuuuck → fuck. bookkeeper stays bookkeeper (max 2 of a letter)."""
    return _REPEAT.sub(r'\1\1', token)


def _fully_collapse(token: str) -> str:
    """fuuuuck → fuck for the lookup only. 'book' → 'bok' is fine: not listed."""
    return re.sub(r'(.)\1+', r'\1', token)


def _merge_single_letters(tokens: list[str]) -> list[str]:
    """Join runs of 1-letter tokens so 'f u c k' is one word."""
    merged: list[str] = []
    buf = []
    for tok in tokens:
        if len(tok) == 1:
            buf.append(tok)
            continue
        if buf:
            if len(buf) >= 3:
                merged.append(''.join(buf))
            else:
                merged.extend(buf)
            buf = []
        merged.append(tok)
    if buf:
        if len(buf) >= 3:
            merged.append(''.join(buf))
        else:
            merged.extend(buf)
    return merged


# Words that are never a legitimate prefix/infix of a longer token.
# "fuckyou" and "shithead2" must fail; "cocktail" and "dickens" must not,
# so "cock"/"dick" stay whole-token only.
_STICKY = frozenset({
    'fuck', 'fuk', 'fck', 'fvck', 'phuck',
    'shit',
    'cunt',
    'nigger', 'nigga',
    'faggot',
    'kaffir', 'kaffer',
    'retard',
    'pussy',
    'whore', 'slut', 'bitch',
    'asshole',
    'motherfuck',
})


def _token_is_blocked(token: str) -> bool:
    candidates = {token, _collapse_repeats(token), _fully_collapse(token)}
    if candidates & _BLOCKED_WORDS:
        return True
    # Prefix only. Infix would ban Scunthorpe (contains "cunt") and
    # cocktail is already safe because "cock" is not sticky.
    for form in candidates:
        for bad in _STICKY:
            if form == bad or form.startswith(bad):
                return True
    return False


def contains_profanity(text: str | None) -> bool:
    """True when `text` would be abusive on a public page."""
    try:
        if not text:
            return False
        folded = _fold(text)
        if not folded.strip():
            return False
        compact = folded.replace(' ', '')
        for phrase in _BLOCKED_PHRASES:
            if phrase.replace(' ', '') in compact:
                return True
        tokens = _merge_single_letters(folded.split())
        return any(_token_is_blocked(tok) for tok in tokens)
    except Exception:
        # A crash in the filter must not fail-open (that would publish
        # the words). Fail closed: treat unparseable input as unclean.
        return True


def validate_public_text(text: str | None, *, allow_blank: bool = True) -> str:
    """Form/view helper. Returns the original text or raises ValidationError.

    We do not rewrite the author's words. Masking ("f***") still *is*
    the word. They rephrase, or they do not publish.
    """
    value = text or ''
    if not value.strip():
        if allow_blank:
            return value
        raise ValidationError('This field cannot be blank.')
    if contains_profanity(value):
        raise ValidationError(PUBLIC_LANGUAGE_ERROR)
    return value


def public_text_is_clean(text: str | None) -> bool:
    """Boolean wrapper for views that do not want to catch ValidationError."""
    return not contains_profanity(text)
