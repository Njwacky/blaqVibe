from django import forms
from .models import AppProject
from .validators import validate_zip
from .prompt_sanitize import sanitize_prompt
from .profanity import validate_public_text
from .taxonomy import UPLOAD_KIND_CHOICES, coerce_kind

class AppUploadForm(forms.ModelForm):
    zip_file = forms.FileField(required=False, validators=[validate_zip])
    # 5 Whys — why offer the creator a kind picker at all when we auto-detect?
    # 1. Detection reads filenames; the creator read their own intent.
    # 2. Why default to blank (auto)? Most uploads are obvious, and a
    #    required dropdown is one more thing between a person and publishing.
    # 3. Why a fixed choice list, not free text? An off-taxonomy value is
    #    unfilterable and unlearnable (see gallery/taxonomy.py).
    # 4. Why not let them set preview_mode too? Whether our sandbox can run
    #    it is a fact about us, not a preference — see taxonomy.preview_mode_for.
    # 5. Why keep it on edit as well? A creator who sees a wrong badge needs
    #    a way to fix it that is not "email support".
    creator_kind = forms.ChoiceField(
        choices=UPLOAD_KIND_CHOICES,
        required=False,
        label='What kind of program is this?',
        help_text='Leave on auto-detect and we will work it out from your files.',
    )

    class Meta:
        model = AppProject
        fields = ['title','category','creator_kind','short_description','readme','tech_stack','ai_generated','ai_tool','ai_prompt','html_code','css_code','js_code','zip_file','thumbnail','star_cost','price_zar']
        widgets = {
            'readme': forms.Textarea(attrs={'rows':10, 'placeholder':'# My App\n## What is this?\n## How to Run\n```bash\npip install -r requirements.txt\n```'}),
            'short_description': forms.TextInput(attrs={'placeholder':'One-line what it does'}),
            'tech_stack': forms.TextInput(attrs={'placeholder':'Django, React, Tailwind'}),
            'ai_prompt': forms.Textarea(attrs={'rows':3, 'placeholder':'If AI-generated, paste prompt...'}),
            'html_code': forms.Textarea(attrs={'rows':6, 'placeholder':'<div>Snippet HTML (for snippet only)</div>'}),
            'star_cost': forms.NumberInput(attrs={'min':0,'max':5, 'placeholder':'0=free, 2=Bronze'}),
        }
    def clean_title(self):
        title = (self.cleaned_data.get('title') or '').strip()
        return validate_public_text(title, allow_blank=False)

    def clean_readme(self):
        md = self.cleaned_data.get('readme', '') or ''
        if len(md.strip()) < 100:
            raise forms.ValidationError("README must be at least 100 characters. Explain what the app does, stack, and how to run.")
        if '# ' not in md:
            raise forms.ValidationError("README needs at least one heading (e.g. '# My App').")
        return validate_public_text(md)

    def clean_ai_prompt(self):
        prompt = self.cleaned_data.get('ai_prompt', '') or ''
        if prompt and len(prompt) > 5000:
            raise forms.ValidationError("Prompt max 5000 chars")
        return validate_public_text(sanitize_prompt(prompt))

    def clean_short_description(self):
        txt = self.cleaned_data.get('short_description', '') or ''
        import bleach
        return validate_public_text(bleach.clean(txt, tags=[], strip=True)[:260])

    def clean_tech_stack(self):
        txt = self.cleaned_data.get('tech_stack', '') or ''
        import bleach
        return validate_public_text(bleach.clean(txt, tags=[], strip=True)[:200])

    def clean_creator_kind(self):
        """Blank stays blank (auto-detect); anything else must be in the taxonomy."""
        value = (self.cleaned_data.get('creator_kind') or '').strip()
        if not value:
            return ''
        return coerce_kind(value)

    def clean(self):
        cleaned = super().clean()
        zipf = cleaned.get('zip_file')
        html = (cleaned.get('html_code') or '').strip()
        if not zipf and not html:
            raise forms.ValidationError("Provide either a ZIP file (full app) or HTML snippet.")
        return cleaned


class CommentForm(forms.Form):
    """The one place comment rules live.

    5 Whys: Why a form instead of parsing request.POST in the view?
    1. Length, sanitize, and the language gate must apply no matter who
       calls the view — a form is that one place.
    2. Why not only Comment.save()? save() can hide a row, but the author
       deserves an error they can act on. A hidden comment looks like a bug.
    3. Why not JS? Anyone can POST. The browser is not a gate.
    4. Why sanitize AND the language gate? bleach stops scripts; the
       language gate stops slurs. They are different attacks.
    5. Why at 10k comments? A future /api/comments/ that reuses this form
       cannot forget the rule.
    """
    body = forms.CharField(min_length=5, max_length=2000)
    parent_id = forms.IntegerField(required=False)

    def clean_body(self):
        raw = (self.cleaned_data.get('body') or '').strip()
        body = sanitize_prompt(raw)[:2000]
        if len(body) < 5:
            raise forms.ValidationError('Comment must be 5–2000 characters.')
        return validate_public_text(body, allow_blank=False)


class ReviewForm(forms.Form):
    """Same reason as CommentForm: one gate for rating + public text."""
    rating = forms.IntegerField(min_value=1, max_value=5)
    text = forms.CharField(required=False, max_length=1000)

    def clean_text(self):
        text = sanitize_prompt(self.cleaned_data.get('text') or '')[:1000]
        return validate_public_text(text)


class CoOwnerForm(forms.Form):
    """Add a co-owner to a vibe — username + share % of star trades.

    5 Whys: Why a form instead of inline view checks? The same three rules
    (user exists, not the owner, not already a co-owner, Σ shares ≤ 100)
    must hold no matter who calls the view; a form is the one place they
    live and the view stays a thin shell.
    """
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={
        'placeholder': 'username',
        'class': 'field-input',
        'autocomplete': 'off',
    }))
    share_percent = forms.IntegerField(min_value=1, max_value=100, widget=forms.NumberInput(attrs={
        'placeholder': '30',
        'class': 'field-input',
        'min': 1,
        'max': 100,
    }))

    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError('Enter a username.')
        from django.contrib.auth.models import User
        user = User.objects.filter(username=username).first()
        if not user:
            raise forms.ValidationError('No user with that username.')
        if self.project and user.pk == self.project.owner_id:
            raise forms.ValidationError('The owner already keeps the remainder — no need to add them.')
        if self.project and self.project.co_owners.filter(user=user).exists():
            raise forms.ValidationError(f'@{username} is already a co-owner.')
        return username

    def clean(self):
        cleaned = super().clean()
        share = cleaned.get('share_percent')
        if self.project and share:
            existing = sum(c.share_percent for c in self.project.co_owners.all())
            if existing + share > 100:
                raise forms.ValidationError(
                    f'Co-owner shares already total {existing}% — adding {share}% would '
                    f'exceed 100% (the owner must keep a remainder).'
                )
        return cleaned
