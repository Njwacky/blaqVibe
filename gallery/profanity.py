"""Public-language gate — vulgar, abusive, and hateful words never go live.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

PUBLIC_LANGUAGE_ERROR = (
    'Please reword this. Public text on BlaqVibes cannot include '
    'vulgar, abusive, or hateful language.'
)


class PublicLanguageError(ValidationError):
    """The refusal itself, as a type — not a string to pattern-match.

    Views need to tell a language refusal apart from "this field is required"
    for two reasons: the person who wrote it must be told their account was
    recorded (`users.quarantine.note_blocked_language`), and the attempt must
    never be confused with an ordinary validation error. Subclassing
    ValidationError keeps every existing `except ValidationError` working.

    `code` carries the verdict: 'blocked' is the matcher saying the words are
    abusive, 'unavailable' is the matcher failing. Both refuse the text (fail
    closed), but only 'blocked' may count against the author — nobody is
    quarantined for a bug on our side.
    """


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

# Masking glyphs people type to dodge a list: f*ck, sh*t, b*tch, f_ck.
# A RUN of them between two letters is deleted — masks usually stand in for the
# letters that were removed, so "f**k" → "fk" and "a**hole" → "ahole".
# At a word edge they stay separators, so "great *stuff*" cannot be glued into
# one fake token ("greatstuff"). A mask inside a word is itself the signal that
# something was hidden, so that token is ALSO looked up in _BLOCKED_MASKED,
# which allows the one or two letters the asterisks replaced.
# The boundary, honestly: three-or-more replaced letters ("n****r") do not
# resolve, and no evasive mask can make an innocent word look blocked.
_MASK_CHARS = '\u002a\u2217\uff0a_\u2022\u00b7'
_MASK_BETWEEN_LETTERS = re.compile(rf'(?<=[a-z])[{_MASK_CHARS}]+(?=[a-z])')
# A whole word that carries an internal mask run — "f**k", "a**hole", "b*tch".
# Whole-word (not whole-text) is what keeps this honest: only the token that
# was actually mangled is looked up as mangled, so a masked "w*s" is read as a
# slur while the ordinary word "was" in the same sentence is not.
_WORD_WITH_MASK = re.compile(rf'[a-z]+(?:[{_MASK_CHARS}]+[a-z]+)+')
# A word whose TAIL is masked: "f***", "sh**", "n****" — the author erased the
# end of it. Deliberately NOT "f***ing": a letter after the run means the word
# continues and the internal rule handles it.
_WORD_TRAILING_MASK = re.compile(rf'(?:^|[^a-z])([a-z]+)([{_MASK_CHARS}]+)(?![a-z])')

def _fold(text: str) -> str:
    """Unicode fold → lowercase Latin-ish letters and spaces only."""
    return _fold_with_masks(text)[0]

def _fold_with_masks(text: str) -> tuple[str, frozenset[str], frozenset[tuple[str, int]]]:
    """Fold, and report what was masked.

    Returns (folded_text, masked_words, masked_prefixes):
      * masked_words      — tokens that had a mask INSIDE them ("f**k" → 'fk'),
      * masked_prefixes   — (visible_prefix, finished_length) for a masked TAIL
                            ("f***" → ('f', 4), so the finished word is one of
                            the never-legitimate words that fit exactly).

    This list is what keeps the deleted-letter lookup below honest: an ordinary
    word is never checked against it, only one the author deliberately mangled —
    so "itch" can never be read as "bitch", and the "was" in
    "grade A*B was fine" stays the word "was".
    """
    if not text:
        return '', frozenset(), frozenset()
    text = unicodedata.normalize('NFKC', text)
    text = text.translate(_HOMOGLYPHS)
    text = text.lower()
    text = text.translate(_LEET)
    masked_words = frozenset(
        _MASK_BETWEEN_LETTERS.sub('', word)
        for word in _WORD_WITH_MASK.findall(text)
    )
    masked_prefixes = frozenset(
        (match.group(1), len(match.group(1)) + len(match.group(2)))
        for match in _WORD_TRAILING_MASK.finditer(text)
    )
    text = _MASK_BETWEEN_LETTERS.sub('', text)
    # Remaining punctuation / stars / underscores become spaces so
    # "f.u.c.k" tokenises instead of surviving as one unknown blob.
    return _NON_LETTER.sub(' ', text), masked_words, masked_prefixes

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

def _mask_variants(word: str) -> set[str]:
    """Every form of `word` with ONE or TWO letters missing.

    "bitch" → 'btch', 'bich' …; "asshole" → 'ahole' …; "fuck" → 'fk'.
    Only consulted for a token that carried a mask character, so an ordinary
    word can never be mistaken for a slur, and a masked innocent ("cl*ss" →
    'clss') matches nothing. Two characters is the floor: 'f**k' is 'fk'.
    """
    out = set()
    for i in range(len(word)):
        stripped = word[:i] + word[i + 1:]
        if len(stripped) >= 2:
            out.add(stripped)
        for j in range(len(stripped)):
            variant = stripped[:j] + stripped[j + 1:]
            if len(variant) >= 2:
                out.add(variant)
    return out

# Built once at import from the words an evasion would be hiding: the exact
# list plus the sticky prefixes. A masked token is matched against this, so
# "b*tch" is caught while "cl*ss" stays clean.
_BLOCKED_MASKED: dict[str, set[str]] = {}
for _word in (_BLOCKED_WORDS | _STICKY):
    if len(_word) < 4:
        continue
    for _variant in _mask_variants(_word):
        _BLOCKED_MASKED.setdefault(_variant, set()).add(_word)
del _word, _variant

# "f***" → the finished word is 'fuck'. Only the never-legitimate words are
# listed here (sticky set, 4+ letters), so a masked "d***" is not read as
# "dick" and a redacted name "D***" stays a redacted name.
_MASKED_PREFIX: dict[tuple[str, int], str] = {
    (word[:cut], len(word)): word
    for word in _STICKY
    if len(word) >= 4
    for cut in range(1, len(word))
}

def _masked_is_blocked(token: str) -> bool:
    """A masked token: the visible letters must still agree with the word.

    Requiring the first AND last visible letter to survive is what stops a
    mangled ordinary word from being read as abuse ('was' cannot become
    'twats'), while "f**k" → 'fk' → 'fuck' and "a**hole" → 'ahole' → 'asshole'
    both still land.
    """
    if not token:
        return False
    for word in _BLOCKED_MASKED.get(token, ()):
        if token[0] == word[0] and token[-1] == word[-1]:
            return True
    return False

def _token_is_blocked(token: str, *, masked: bool = False) -> bool:
    candidates = {token, _collapse_repeats(token), _fully_collapse(token)}
    if candidates & _BLOCKED_WORDS:
        return True
    if masked and any(_masked_is_blocked(form) for form in candidates):
        return True
    # Prefix only. Infix would ban Scunthorpe (contains "cunt") and
    # cocktail is already safe because "cock" is not sticky.
    for form in candidates:
        for bad in _STICKY:
            if form == bad or form.startswith(bad):
                return True
    return False

def scan_public_text(text: str | None) -> str:
    """Classify public text: 'clean' | 'blocked' | 'unavailable'.

    'unavailable' means the matcher itself could not run. The text is still
    refused (never publish what we could not read) but it is NOT evidence
    against the author: `users.quarantine` only records a rule breach for
    'blocked'. Without that distinction a bug in this file would quarantine
    innocent people for 30 days.
    """
    try:
        if not text:
            return 'clean'
        folded, masked_words, masked_prefixes = _fold_with_masks(text)
        if not folded.strip():
            return 'clean'
        compact = folded.replace(' ', '')
        for phrase in _BLOCKED_PHRASES:
            if phrase.replace(' ', '') in compact:
                return 'blocked'
        for prefix, finished_len in masked_prefixes:
            if (prefix, finished_len) in _MASKED_PREFIX:
                return 'blocked'
        tokens = _merge_single_letters(folded.split())
        if any(_token_is_blocked(tok, masked=tok in masked_words) for tok in tokens):
            return 'blocked'
        return 'clean'
    except Exception:
        # A crash in the filter must not fail-open (that would publish the
        # words), so the caller still refuses the text. It is reported as
        # 'unavailable' rather than 'blocked' so the person is never punished
        # for our failure.
        logger.exception('profanity scan crashed — text refused, not recorded')
        return 'unavailable'

def contains_profanity(text: str | None) -> bool:
    """True when `text` would be abusive on a public page.

    Fail closed: 'unavailable' is treated as unclean, exactly as before, so
    every existing gate keeps refusing text it could not check.
    """
    return scan_public_text(text) != 'clean'

def validate_public_text(text: str | None, *, allow_blank: bool = True) -> str:
    """Form/view helper. Returns the original text or raises PublicLanguageError.

    We do not rewrite the author's words. Masking ("f***") still *is*
    the word. They rephrase, or they do not publish.
    """
    value = text or ''
    if not value.strip():
        if allow_blank:
            return value
        raise ValidationError('This field cannot be blank.')
    verdict = scan_public_text(value)
    if verdict != 'clean':
        # code='blocked' → the words are abusive (recorded, and the author is
        # told). code='unavailable' → the matcher failed; the text is still
        # refused, but the author is not blamed for it.
        raise PublicLanguageError(PUBLIC_LANGUAGE_ERROR, code=verdict)
    return value

def public_text_is_clean(text: str | None) -> bool:
    """Boolean wrapper for views that do not want to catch ValidationError."""
    return not contains_profanity(text)
