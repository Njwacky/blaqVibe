from django import forms
from .models import AppProject
from .validators import validate_zip
from .prompt_sanitize import sanitize_prompt

class AppUploadForm(forms.ModelForm):
    zip_file = forms.FileField(required=False, validators=[validate_zip])
    class Meta:
        model = AppProject
        fields = ['title','category','short_description','readme','tech_stack','ai_generated','ai_tool','ai_prompt','html_code','css_code','js_code','zip_file','thumbnail','star_cost','price_zar']
        widgets = {
            'readme': forms.Textarea(attrs={'rows':10, 'placeholder':'# My App\n## What is this?\n## How to Run\n```bash\npip install -r requirements.txt\n```'}),
            'short_description': forms.TextInput(attrs={'placeholder':'One-line what it does'}),
            'tech_stack': forms.TextInput(attrs={'placeholder':'Django, React, Tailwind'}),
            'ai_prompt': forms.Textarea(attrs={'rows':3, 'placeholder':'If AI-generated, paste prompt...'}),
            'html_code': forms.Textarea(attrs={'rows':6, 'placeholder':'<div>Snippet HTML (for snippet only)</div>'}),
            'star_cost': forms.NumberInput(attrs={'min':0,'max':5, 'placeholder':'0=free, 2=Bronze'}),
        }
    def clean_readme(self):
        md = self.cleaned_data.get('readme', '') or ''
        if len(md.strip()) < 100:
            raise forms.ValidationError("README must be at least 100 characters. Explain what the app does, stack, and how to run.")
        if '# ' not in md:
            raise forms.ValidationError("README needs at least one heading (e.g. '# My App').")
        return md

    def clean_ai_prompt(self):
        prompt = self.cleaned_data.get('ai_prompt', '') or ''
        if prompt and len(prompt) > 5000:
            raise forms.ValidationError("Prompt max 5000 chars")
        return sanitize_prompt(prompt)

    def clean_short_description(self):
        txt = self.cleaned_data.get('short_description', '') or ''
        import bleach
        return bleach.clean(txt, tags=[], strip=True)[:260]

    def clean_tech_stack(self):
        txt = self.cleaned_data.get('tech_stack', '') or ''
        import bleach
        return bleach.clean(txt, tags=[], strip=True)[:200]

    def clean(self):
        cleaned = super().clean()
        zipf = cleaned.get('zip_file')
        html = (cleaned.get('html_code') or '').strip()
        if not zipf and not html:
            raise forms.ValidationError("Provide either a ZIP file (full app) or HTML snippet.")
        return cleaned

