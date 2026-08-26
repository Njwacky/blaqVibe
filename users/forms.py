from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator
from .models import (
    NAME_COLORS,
    NAME_FONTS,
    NAME_FX,
    NAME_FX_LABELS,
    NAME_FONT_LABELS,
    NAME_SIZES,
    NAME_SIZE_LABELS,
    NAME_COLOR_LABELS,
    Profile,
)
from .rename import RESERVED_USERNAMES
from gallery.profanity import validate_public_text
import bleach


ALLOWED_BIO_TAGS = []


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['bio', 'location', 'website', 'github', 'twitter', 'canvas_url', 'avatar']
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
            'canvas_url': forms.URLInput(attrs={
                'placeholder': 'https://koboyo.com/s/your-canvas',
                'class': 'field-input',
            }),
        }

    def clean_bio(self):
        bio = bleach.clean(self.cleaned_data.get('bio', ''), tags=[], strip=True)[:280]
        return validate_public_text(bio)

    def clean_location(self):
        location = bleach.clean(self.cleaned_data.get('location', ''), tags=[], strip=True)[:80]
        return validate_public_text(location)

    def clean_github(self):
        github = (self.cleaned_data.get('github') or '').strip().lstrip('@')[:80]
        return validate_public_text(github)

    def clean_twitter(self):
        twitter = (self.cleaned_data.get('twitter') or '').strip().lstrip('@')[:80]
        return validate_public_text(twitter)

    def clean_canvas_url(self):
        # URLField already validates http(s) shape; trimming here keeps the
        # profile chip neat and avoids persisting accidental whitespace.
        return (self.cleaned_data.get('canvas_url') or '').strip()

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
        # Then the public-language gate — a tip note shows on the profile.
        note = bleach.clean(self.cleaned_data.get('message', ''), tags=[], strip=True)[:200]
        return validate_public_text(note)


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
        if username.lower() in RESERVED_USERNAMES:
            # 5 Whys: why here too, not just at rename? "admin"/"support" /
            # "nolo" phishing works wherever the handle can appear, and
            # signup is the FIRST place it can appear. One shared list
            # (users/rename.py) gates both doors — no drift.
            # Why mention sign-in? Operators try to *register* as admin
            # with a placeholder password, get this error, and report
            # "admin login never works." Point them at the real door.
            raise forms.ValidationError(
                "That username is reserved. Sign in if you already have "
                "an operator account, or pick another name."
            )
        return validate_public_text(username, allow_blank=False)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class ChangeEmailForm(forms.Form):
    """Fix the mailbox before we send another confirmation.

    5 Whys: why a form instead of mailing User.email again?
    1. Why edit first? The activate banner used to POST-resend to whatever
       was stored. A typo at signup then mailed a mailbox the person
       cannot open, forever.
    2. Why the same uniqueness rule as SignUpForm? Two accounts with the
       same address makes email-login a coin flip (see AuthenticationForm).
    3. Why lowercase? Signup stores lowercased email; a mixed-case twin
       would look unique and then collide on login.
    4. Why exclude self? Resending to the current address must stay valid.
    5. Why not live on ProfileForm? Bio/avatar is public chrome; the
       mailbox is an auth credential and has its own confirm flow.
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'you@email.com',
            'autocomplete': 'email',
            'class': 'field-input',
        }),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError('Email is required.')
        taken = User.objects.filter(email__iexact=email)
        if self.user is not None:
            taken = taken.exclude(pk=self.user.pk)
        if taken.exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


class StyledAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'placeholder': 'Username or email',
        'autocomplete': 'username',
        'class': 'field-input',
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Password',
        'autocomplete': 'current-password',
        'class': 'field-input',
    }))

    def clean(self):
        # Settings promise email login (ACCOUNT_LOGIN_METHODS has 'email'),
        # but stock AuthenticationForm only tries User.USERNAME_FIELD. Without
        # this, typing admin@blaqvibes.co.za + the correct password still
        # fails — the "my admin account never works" support ticket. Resolve
        # a single matching email to its username before auth runs. An unknown
        # address falls through to the normal (failing) path, so the error
        # message never leaks whether an email is registered.
        #
        # An AMBIGUOUS address must be blocked here, not left to fall through.
        # allauth's AuthenticationBackend sits in AUTHENTICATION_BACKENDS and
        # ACCOUNT_LOGIN_METHODS includes 'email', so authenticate() resolves
        # emails on its own and returns whichever match it finds first — login
        # for a shared address silently becomes a coin flip between accounts.
        username = self.cleaned_data.get('username', '')
        if username and '@' in username:
            matches = list(User.objects.filter(email__iexact=username)[:2])
            if len(matches) == 1:
                self.cleaned_data['username'] = matches[0].username
            elif len(matches) > 1:
                # Raise the stock invalid-credentials error verbatim: identical
                # wording and code to the bad-password path, so an ambiguous
                # address is indistinguishable from a wrong one and still does
                # not reveal that the address is registered.
                raise forms.ValidationError(
                    self.error_messages['invalid_login'],
                    code='invalid_login',
                    params={'username': self.username_field.verbose_name},
                )
        return super().clean()


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


class RenameForm(forms.Form):
    """The rename-card form. Format rules live here; money, cooldown and
    reservation rules live in users.rename (the form cannot be tricked
    into charging nobody, because it never charges — rename_user does).

    5 Whys: why does the form NOT check uniqueness/reservation? Those
    answers expire between render and POST. The form checks what cannot
    drift (charset, length, language); rename_user re-checks everything
    under the lock. One gate that matters, zero dead duplication.
    """
    new_username = forms.CharField(
        max_length=150,
        min_length=3,
        widget=forms.TextInput(attrs={
            'placeholder': 'new-username',
            'autocomplete': 'off',
            'class': 'field-input',
        }),
    )

    def clean_new_username(self):
        new = (self.cleaned_data.get('new_username') or '').strip()
        if not new:
            raise forms.ValidationError("Type the username you want.")
        try:
            UnicodeUsernameValidator()(new)
        except Exception:
            raise forms.ValidationError(
                "Letters, numbers and @/./+/-/_ only — no spaces or symbols."
            )
        return validate_public_text(new, allow_blank=False)


class NameStyleForm(forms.Form):
    """Whitelist-only style picker. Choices are built FROM the models.NAME_*
    dicts — the form can never offer, or accept, a slug the renderer does
    not know. Anything else dies as a form error before the wallet moves."""

    name_font = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'field-input', 'data-style': 'font'}),
        choices=lambda: [
            (slug, NAME_FONT_LABELS.get(slug, slug)) for slug in NAME_FONTS
        ],
    )
    name_color = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'field-input', 'data-style': 'color'}),
        choices=lambda: [
            (slug, NAME_COLOR_LABELS.get(slug, slug)) for slug in NAME_COLORS
        ],
    )
    name_size = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'field-input', 'data-style': 'size'}),
        choices=lambda: [
            (slug, NAME_SIZE_LABELS.get(slug, slug)) for slug in NAME_SIZES
        ],
    )
    name_fx = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'field-input', 'data-style': 'fx'}),
        choices=lambda: [
            (slug, NAME_FX_LABELS.get(slug, slug)) for slug in NAME_FX
        ],
    )

