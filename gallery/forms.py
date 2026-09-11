from django import forms
from .models import AppProject, Category
from .validators import validate_zip
from .prompt_sanitize import sanitize_prompt
from .profanity import validate_public_text
from .taxonomy import UPLOAD_KIND_CHOICES, coerce_kind
from .proof import scaffold_readme

# Phones (especially the installed PWA) open the photo gallery for a bare
# <input type="file">. A non-image accept list forces Files / Documents
# instead of Pictures. Do not add image/* here.
ZIP_FILE_ACCEPT = (
    '.zip,application/zip,application/x-zip-compressed,application/x-zip'
)

# The ONE publish-time build-method question. Four taps maximum, no
# questionnaire: the pick is recorded and the details are invited later.
# 'remixed' is offered because it is one of BlaqVibes' four canonical build
# methods, but it can only be *honoured* for a project with a remix parent —
# lineage is a platform fact, not a self-declared label (see clean()).
BUILD_METHOD_PUBLISH_CHOICES = [
    ('human', 'Human-built'),
    ('ai_assisted', 'AI-assisted'),
    ('ai_generated', 'AI-generated'),
    ('remixed', 'Remixed'),
]

BUILD_METHOD_HINTS = {
    'human': 'I wrote it myself.',
    'ai_assisted': 'I built it — AI helped write parts.',
    'ai_generated': 'AI produced most of it; I directed and checked.',
    'remixed': 'My version of another builder’s project.',
}


class AppUploadForm(forms.ModelForm):
    zip_file = forms.FileField(
        required=False,
        validators=[validate_zip],
        widget=forms.FileInput(attrs={
            'accept': ZIP_FILE_ACCEPT,
            'class': 'zip-picker__input',
            'aria-describedby': 'zip-picker-hint',
        }),
    )
    creator_kind = forms.ChoiceField(
        choices=UPLOAD_KIND_CHOICES,
        required=False,
        label='What kind of program is this?',
        help_text='Leave on auto-detect and we will work it out from your files.',
    )
    # The same one-tap build-method control as the publish form. Optional:
    # blank keeps the legacy behaviour (derive the label from ai_generated /
    # ai_tool) so every old flow and old row keeps meaning what it meant.
    build_choice = forms.ChoiceField(
        choices=[('', 'Let BlaqVibes detect it')] + BUILD_METHOD_PUBLISH_CHOICES,
        required=False,
        label='Build method',
        help_text='One tap now; details are optional and can be added any time.',
    )

    class Meta:
        model = AppProject
        fields = ['title','category','creator_kind','build_choice','short_description','readme','tech_stack','ai_generated','ai_tool','ai_prompt','problem_statement','human_did','ai_got_wrong','remix_changed','remix_why','html_code','css_code','js_code','zip_file','thumbnail','star_cost','price_zar']
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
            'ai_generated': 'Shown as provenance, not a quality score. Details below are optional — add them any time.',
            'ai_tool': 'Optional — naming it strengthens your proof. You can name more than one tool.',
            'ai_prompt': 'Optional. Share the prompt or workflow when you can. No secrets, API keys, or private data.',
            'problem_statement': 'Optional — makes the Build evidence, not a dump.',
            'human_did': 'Optional — in an AI world the scarce proof is your judgment.',
            'ai_got_wrong': 'Turns AI failure into a lesson. Optional.',
            'remix_changed': 'Required in spirit when this is a remix — credit plus delta.',
            'remix_why': 'Learning through continuation.',
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

    def _clean_proof_line(self, key):
        txt = (self.cleaned_data.get(key) or '').strip()
        import bleach
        return validate_public_text(bleach.clean(txt, tags=[], strip=True)[:400])

    def clean_problem_statement(self):
        return self._clean_proof_line('problem_statement')

    def clean_human_did(self):
        return self._clean_proof_line('human_did')

    def clean_ai_got_wrong(self):
        return self._clean_proof_line('ai_got_wrong')

    def clean_remix_changed(self):
        return self._clean_proof_line('remix_changed')

    def clean_remix_why(self):
        return self._clean_proof_line('remix_why')

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
        # Build method: one tap, mapped onto the recorded facts. Detailed AI
        # provenance is INVITED (help texts, proof checklist, strengthen
        # nudges) but never demanded — transparency is the label, not a
        # questionnaire (see the publish-first principle).
        apply_build_method(cleaned, self, bool(getattr(self.instance, 'forked_from_id', None)))
        if self.errors.get('zip_file'):
            return cleaned
        zipf = cleaned.get('zip_file')
        html = (cleaned.get('html_code') or '').strip()
        if not zipf and not html:
            raise forms.ValidationError("Provide either a ZIP file (full app) or HTML snippet.")
        # Remix is learning through continuation: a child without a delta is theft-shaped.
        if self.instance and getattr(self.instance, 'forked_from_id', None):
            if not (cleaned.get('remix_changed') or '').strip():
                self.add_error(
                    'remix_changed',
                    'Say what you changed. Remix without a delta is just a copy.',
                )
        return cleaned


def apply_build_method(cleaned, form, has_fork_parent):
    """Map the ONE build-method pick onto the model's recorded facts.

    Shared by QuickPublishForm (field `build_method`) and AppUploadForm
    (field `build_choice`) so both surfaces mean exactly the same thing.

    - human        → clears AI records (a later-named ai_tool still upgrades
                     the label — evidence beats claim, never the reverse)
    - ai_assisted  → stored as the creator's explicit claim
    - ai_generated → also sets the legacy ai_generated flag
    - remixed      → honoured only with a real fork parent; otherwise a
                     gentle, actionable error (lineage is platform-written)
    - blank        → legacy: derive from the plain ai_generated checkbox
    """
    field_name = 'build_method' if 'build_method' in form.fields else 'build_choice'
    choice = (cleaned.get(field_name) or '').strip()
    if choice == 'remixed':
        if not has_fork_parent:
            form.add_error(
                field_name,
                'This project has no remix parent on BlaqVibes yet. Open the '
                'original project and tap “Remix” — your version then keeps '
                'the lineage and credit automatically.',
            )
            return
        cleaned['build_choice'] = ''
        cleaned['ai_generated'] = False
    elif choice == 'ai_assisted':
        cleaned['build_choice'] = 'ai_assisted'
        cleaned['ai_generated'] = False
    elif choice == 'ai_generated':
        cleaned['build_choice'] = 'ai_generated'
        cleaned['ai_generated'] = True
    elif choice == 'human':
        cleaned['build_choice'] = ''
        cleaned['ai_generated'] = False
    else:
        # No explicit pick (older clients): keep deriving from the facts.
        # The legacy checkbox wins when it is actually posted (old studio
        # payloads); on the edit page it is no longer rendered, so an
        # absent value must PRESERVE the stored flag instead of silently
        # un-marking an AI project.
        if 'ai_generated' in form.data:
            legacy_ai = form.data.get('ai_generated') in ('on', 'true', 'True', '1')
        else:
            legacy_ai = bool(getattr(form.instance, 'ai_generated', False))
        if legacy_ai:
            cleaned['ai_generated'] = True
            cleaned['build_choice'] = 'ai_generated'
    # Write onto the instance as well: QuickPublishForm does not carry
    # ai_generated/build_choice as model-form fields, so save() alone
    # would drop the mapping.
    form.instance.build_choice = cleaned.get('build_choice', '') or ''
    form.instance.ai_generated = bool(cleaned.get('ai_generated'))


class QuickPublishForm(forms.ModelForm):
    """PUBLISH FIRST. BUILD THE PROOF LATER.

    The whole form is four primary inputs — name, what-you-built, the
    project itself (ZIP or snippet), and one build-method tap. A README is
    scaffolded on the creator's behalf, category is picked automatically,
    and pricing stays free until the creator chooses otherwise on the edit
    page.

    Fields that sibling surfaces post (studio's README/tech stack, the
    legacy ai_generated checkbox, star/price) are tolerated as OPTIONAL so
    every existing poster keeps working through the same one publish path.
    What is deliberately ABSENT: problem_statement, human_did,
    ai_got_wrong, remix fields, ai_tool/ai_prompt requirements, thumbnail.
    They live on the edit page and the proof checklist — never as a
    publishing gate.
    """

    zip_file = forms.FileField(
        required=False,
        validators=[validate_zip],
        widget=forms.FileInput(attrs={
            'accept': ZIP_FILE_ACCEPT,
            'class': 'zip-picker__input',
            'aria-describedby': 'zip-picker-hint',
        }),
    )
    build_method = forms.ChoiceField(
        choices=BUILD_METHOD_PUBLISH_CHOICES,
        required=False,  # required in the UI (native `required` radio); blank falls back to facts
        widget=forms.RadioSelect,
        label='How did you build it?',
    )
    # ---- tolerated extras (optional; posted by studio / legacy clients) ----
    readme = forms.CharField(required=False, widget=forms.Textarea)
    tech_stack = forms.CharField(required=False, max_length=200)
    creator_kind = forms.ChoiceField(choices=UPLOAD_KIND_CHOICES, required=False)
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(), required=False,
    )
    star_cost = forms.IntegerField(required=False, min_value=0, max_value=5)
    price_zar = forms.IntegerField(required=False, min_value=0, max_value=9999)
    ai_generated = forms.BooleanField(required=False)

    class Meta:
        model = AppProject
        # Every field here is overridden as OPTIONAL on the form class
        # above — Meta.fields is what ModelForm.save() writes back, so the
        # tolerated extras (readme/tech_stack/category/pricing/kind) must be
        # listed for the scaffold and auto-defaults to land.
        fields = ['title', 'short_description', 'html_code', 'css_code', 'js_code',
                  'zip_file', 'readme', 'tech_stack', 'category', 'creator_kind',
                  'star_cost', 'price_zar']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Inventory Tracker',
                'autocomplete': 'off',
                'autocapitalize': 'sentences',
            }),
            'short_description': forms.TextInput(attrs={
                'placeholder': 'A simple inventory system for small businesses.',
                'autocomplete': 'off',
            }),
            'html_code': forms.Textarea(attrs={'rows': 5, 'placeholder': '<main><h1>It runs!</h1></main>'}),
            'css_code': forms.Textarea(attrs={'rows': 3, 'placeholder': 'main { color: rebeccapurple }'}),
            'js_code': forms.Textarea(attrs={'rows': 3, 'placeholder': '// optional'}),
        }

    def clean_title(self):
        title = (self.cleaned_data.get('title') or '').strip()
        return validate_public_text(title, allow_blank=False)

    def clean_short_description(self):
        txt = self.cleaned_data.get('short_description', '') or ''
        import bleach
        txt = bleach.clean(txt, tags=[], strip=True)[:260].strip()
        if not txt:
            raise forms.ValidationError('Tell people what you built — one line is enough.')
        return validate_public_text(txt)

    def clean_readme(self):
        """No length rule, no heading rule at publish — a scaffold is added
        in clean() when this is blank. Sanitised exactly like a real one."""
        md = self.cleaned_data.get('readme', '') or ''
        return validate_public_text(md)

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
        if self.errors.get('zip_file'):
            return cleaned
        zipf = cleaned.get('zip_file')
        html = (cleaned.get('html_code') or '').strip()
        if not zipf and not html:
            raise forms.ValidationError(
                'Add your project — a ZIP file or a pasted snippet.'
            )
        apply_build_method(cleaned, self, bool(getattr(self.instance, 'forked_from_id', None)))
        # README: scaffolded on the creator's behalf, honestly labelled as
        # such. The strengthen step replaces it; publishing never waits.
        if not (cleaned.get('readme') or '').strip():
            cleaned['readme'] = scaffold_readme(
                cleaned.get('title') or '',
                cleaned.get('short_description') or '',
            )
        # Category: chosen automatically. It is internal shelf-space, not
        # information a creator should have to supply in order to publish.
        if not cleaned.get('category'):
            from .repo_import import suggest_category
            cleaned['category'] = suggest_category(None)
        # Pricing: free until the creator decides otherwise (edit page).
        if cleaned.get('star_cost') is None:
            cleaned['star_cost'] = 0
        if cleaned.get('price_zar') is None:
            cleaned['price_zar'] = 0
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
    """Witness, not a like. Can I run it? Is the README honest?"""
    rating = forms.IntegerField(min_value=1, max_value=5)
    text = forms.CharField(required=False, max_length=1000)
    ran_it = forms.BooleanField(required=False)
    readme_clear = forms.BooleanField(required=False)

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
