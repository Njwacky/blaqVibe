from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from .models import Profile
import bleach


ALLOWED_BIO_TAGS = []


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['bio', 'location', 'website', 'github', 'twitter', 'avatar']
        widgets = {
            'bio': forms.TextInput(attrs={
                'placeholder': 'AI builder • Stock tools • Durban, ZA',
                'maxlength': 280,
                'class': 'field-input',
            }),
            'location': forms.TextInput(attrs={'placeholder': 'Durban, ZA', 'class': 'field-input'}),
            'website': forms.URLInput(attrs={'placeholder': 'https://your.site', 'class': 'field-input'}),
            'github': forms.TextInput(attrs={'placeholder': 'nolo-ai', 'class': 'field-input'}),
            'twitter': forms.TextInput(attrs={'placeholder': 'nol0ai', 'class': 'field-input'}),
        }

    def clean_bio(self):
        bio = self.cleaned_data.get('bio', '')
        return bleach.clean(bio, tags=[], strip=True)[:280]

    def clean_avatar(self):
        f = self.cleaned_data.get('avatar')
        if f:
            if f.size > 2 * 1024 * 1024:
                raise forms.ValidationError("Avatar max 2MB")
            content_type = getattr(f, 'content_type', '') or ''
            if content_type and not content_type.startswith('image/'):
                raise forms.ValidationError("Only images")
        return f


class TipForm(forms.Form):
    """Gratitude stars — amount + optional note.

    5 Whys: Why a form instead of parsing request.POST by hand? The same
    bounds (1–1000) and sanitizer must apply no matter who calls the view —
    a form is the one place they live, and the view stays a thin shell.
    """
    amount = forms.IntegerField(min_value=1, max_value=1000)
    message = forms.CharField(required=False, max_length=200)

    def clean_message(self):
        # Same bleach policy as bio: no tags, stripped, hard cap.
        return bleach.clean(self.cleaned_data.get('message', ''), tags=[], strip=True)[:200]


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'you@email.com',
            'autocomplete': 'email',
            'class': 'field-input',
        }),
    )
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'username',
            'autocomplete': 'username',
            'class': 'field-input',
        }),
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Password (8+ characters)',
            'autocomplete': 'new-password',
            'class': 'field-input',
        }),
    )
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password',
            'autocomplete': 'new-password',
            'class': 'field-input',
        }),
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("Username is required.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class StyledAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'placeholder': 'Username',
        'autocomplete': 'username',
        'class': 'field-input',
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Password',
        'autocomplete': 'current-password',
        'class': 'field-input',
    }))


class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'placeholder': 'you@email.com',
        'autocomplete': 'email',
        'class': 'field-input',
    }))


class StyledSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'New password (8+ characters)',
        'autocomplete': 'new-password',
        'class': 'field-input',
    }))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Confirm new password',
        'autocomplete': 'new-password',
        'class': 'field-input',
    }))
