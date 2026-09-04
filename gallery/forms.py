from django import forms
from .models import AppProject
from .validators import validate_zip
from .prompt_sanitize import sanitize_prompt
from .profanity import validate_public_text
from .taxonomy import UPLOAD_KIND_CHOICES, coerce_kind

class AppUploadForm(forms.ModelForm):
    zip_file = forms.FileField(required=False, validators=[validate_zip])
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
            'ai_generated': forms.CheckboxInput(attrs={'aria-describedby':'ai-origin-help'}),
            'ai_tool': forms.TextInput(attrs={'placeholder':'e.g. Claude, Gemini, ChatGPT'}),
            'ai_prompt': forms.Textarea(attrs={'rows':3, 'placeholder':'If AI helped create it, briefly share the prompt or workflow...'}),
            'html_code': forms.Textarea(attrs={'rows':6, 'placeholder':'<div>Snippet HTML (for snippet only)</div>'}),
            'star_cost': forms.NumberInput(attrs={'min':0,'max':5, 'placeholder':'0=free, 2=Bronze'}),
        }
        labels = {
            'ai_generated': 'AI-assisted creation',
            'ai_tool': 'AI tool used (if any)',
            'ai_prompt': 'AI creation notes',
        }
        help_texts = {
            'ai_generated': 'Be transparent if AI materially helped create this project. This is shown as provenance, not a quality score.',
            'ai_tool': 'Optional unless you mark the project as AI-assisted. You can name more than one tool.',
            'ai_prompt': 'Share the useful prompt or workflow when you can. Do not include secrets, API keys, or private data.',
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

    def clean_ai_tool(self):
        tool = (self.cleaned_data.get('ai_tool') or '').strip()
        if len(tool) > 50:
            raise forms.ValidationError("AI tool name must be 50 characters or fewer.")
        return validate_public_text(tool)

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
        # AI provenance must be internally consistent. We never ask creators
        # to hide AI use: marking it requires enough evidence to explain the
        # origin, while keeping the notes optional for human-built projects.
        if cleaned.get('ai_generated'):
            if not (cleaned.get('ai_tool') or '').strip():
                self.add_error('ai_tool', 'Name the AI tool used so visitors can understand the project provenance.')
            if not (cleaned.get('ai_prompt') or '').strip():
                self.add_error('ai_prompt', 'Add a short prompt/workflow note so the AI-assisted origin is verifiable.')
        if self.errors.get('zip_file'):
            return cleaned
        zipf = cleaned.get('zip_file')
        html = (cleaned.get('html_code') or '').strip()
        if not zipf and not html:
            raise forms.ValidationError("Provide either a ZIP file (full app) or HTML snippet.")
        return cleaned

class CommentForm(forms.Form):
    """The one place comment rules live.
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
    """Add a co-owner to a vibe — username + share % of star trades. The same
    three rules (user exists, not the owner, not already a co-owner, shares
    total ≤ 100) must hold no matter who calls the view; a form is the one
    place they live, leaving the view a thin shell.
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
