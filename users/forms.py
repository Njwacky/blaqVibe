from django import forms
from .models import Profile
import bleach

# 5 Whys: Why bleach bio? Bio is user input rendered as |safe| in profile — XSS risk.
ALLOWED_BIO_TAGS = []
class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['bio','location','website','github','twitter','avatar']
        widgets = {
            'bio': forms.TextInput(attrs={'placeholder': 'AI builder • Stock tools • Durban, ZA', 'maxlength': 280}),
            'location': forms.TextInput(attrs={'placeholder': 'Durban, ZA'}),
            'website': forms.URLInput(attrs={'placeholder': 'https://your.site'}),
            'github': forms.TextInput(attrs={'placeholder': 'nolo-ai'}),
            'twitter': forms.TextInput(attrs={'placeholder': 'nol0ai'}),
        }
    def clean_bio(self):
        bio = self.cleaned_data.get('bio','')
        # Strip all HTML — bio is plain text
        return bleach.clean(bio, tags=[], strip=True)[:280]
    def clean_avatar(self):
        f = self.cleaned_data.get('avatar')
        if f:
            if f.size > 2*1024*1024:
                raise forms.ValidationError("Avatar max 2MB")
            if not f.content_type.startswith('image/'):
                raise forms.ValidationError("Only images")
        return f
