from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm, SetPasswordForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.forms import modelformset_factory
from .models import (
    NAME_COLORS,
    NAME_FONTS,
    NAME_FX,
    NAME_FX_LABELS,
    NAME_FONT_LABELS,
    NAME_PERSONAS,
    NAME_SIZES,
    NAME_SIZE_LABELS,
    NAME_COLOR_LABELS,
    FooterContact,
    Profile,
    SiteSettings,
)
from .footer_contacts import normalize_value
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

class FooterContactForm(forms.ModelForm):
    """One row of the footer contact editor: a kind, and the value for it.

    The value box is deliberately one field for every kind — email, phone
    number, handle or URL — because an operator adding a WhatsApp number does
    not want to decide which of five boxes it belongs in. The kind dropdown
    says how to read it, and users/footer_contacts.py validates and turns it
    into a link.
    """
    class Meta:
        model = FooterContact
        fields = ['kind', 'value', 'label', 'position', 'is_active']
        labels = {
            'kind': 'Type',
            'value': 'Address / number / handle',
            'label': 'Display text',
            'position': 'Order',
            'is_active': 'Show',
        }
        help_texts = {
            'value': 'Email, phone number, handle or URL, depending on the type.',
            'label': 'Optional — what visitors read instead of the raw value.',
            'position': 'Lower numbers appear first.',
            'is_active': 'Untick to hide a method without deleting it.',
        }
        widgets = {
            'kind': forms.Select(attrs={
                'class': 'field-input footer-contact-kind',
                'aria-label': 'Contact type',
            }),
            'value': forms.TextInput(attrs={
                'class': 'field-input', 'maxlength': 200, 'autocomplete': 'off',
                'placeholder': 'support@blaqvibes.co.za',
                'aria-label': 'Contact address, number, handle or URL',
            }),
            'label': forms.TextInput(attrs={
                'class': 'field-input', 'maxlength': 80, 'autocomplete': 'off',
                'placeholder': 'Optional',
                'aria-label': 'Display text',
            }),
            'position': forms.NumberInput(attrs={
                'class': 'field-input footer-contact-position', 'min': 0, 'step': 1,
                'aria-label': 'Order in the footer',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'footer-contact-check', 'aria-label': 'Show in the footer',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # value is required for a row the operator is keeping, but checks it
        # in clean() rather than with `required=True`, so the blank spare rows
        # at the bottom of the editor can be submitted untouched (has_changed).
        self.fields['value'].required = False
        self.fields['position'].required = False

    def clean_value(self):
        raw = (self.cleaned_data.get('value') or '').strip()
        # Control characters (a pasted newline, a zero-width joiner) would
        # survive into the footer and break its layout. Everything else is
        # handled by the per-kind validator and by template escaping — the
        # value is NOT bleached here, because escaping '&' would corrupt a URL
        # that carries a query string.
        return ''.join(char for char in raw if char.isprintable())[:200]

    def clean_label(self):
        # Public text. Strip markup even though template escaping is also on,
        # so a pasted tag can never become the stored display name.
        label = bleach.clean((self.cleaned_data.get('label') or '').strip(), tags=[], strip=True)[:80]
        return validate_public_text(label)

    def clean_position(self):
        position = self.cleaned_data.get('position')
        return 0 if position in (None, '') else position

    def has_changed(self):
        """A spare row with no value was never used — do not count it.

        Django ignores a formset's extra rows with `empty_permitted and not
        has_changed()`, which is almost right: a spare row renders a suggested
        Order number, so it comes back looking changed even when the operator
        never touched it, and would then fail validation for having no value.
        Blank means blank, so say it here instead — this is what lets the page
        offer two spare rows without forcing anyone to fill them in.
        """
        if self.instance.pk is None and not (self['value'].value() or '').strip():
            return False
        return super().has_changed()

    def clean(self):
        cleaned = super().clean()
        # A row being deleted needs nothing else from the operator.
        if cleaned.get('DELETE'):
            return cleaned
        kind = cleaned.get('kind')
        value = (cleaned.get('value') or '').strip()
        if not value:
            self.add_error('value', 'Add the address, number, handle or URL people should use.')
            return cleaned
        try:
            cleaned['value'] = normalize_value(kind, value)
        except ValidationError as exc:
            self.add_error('value', exc.messages)
        return cleaned


# Two blank rows: "add another method" is one click, and an operator adding a
# third does not have to save twice. Rows are ordered by `position`, and new
# rows start below everything that exists (see next_positions()).
FooterContactFormSet = modelformset_factory(
    FooterContact,
    form=FooterContactForm,
    extra=2,
    can_delete=True,
)


class TipForm(forms.Form):
    """Gratitude stars — amount + optional note. A form (rather than parsing
    request.POST by hand) keeps the bounds (1-1000) and sanitizer in one place
    no matter who calls the view, leaving the view a thin shell.
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
            # Gated at signup too, not just rename: "admin"/"support"/"nolo"
            # phishing works wherever the handle can appear, and signup is the
            # first place it can. One shared list (users/rename.py) gates both
            # doors with no drift. The message mentions sign-in because
            # operators try to *register* as admin, get this error, and report
            # "admin login never works" — point them at the real door.
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

class StyledPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'class': 'field-input'}))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'field-input'}))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'field-input'}))

class RenameForm(forms.Form):
    """The rename-card form. Format rules live here; money, cooldown and
    reservation rules live in users.rename (the form can't be tricked into
    charging nobody, because it never charges — rename_user does).

    The form does NOT check uniqueness/reservation: those answers expire
    between render and POST. It checks only what can't drift (charset, length,
    language); rename_user re-checks everything under the lock.
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
        not know. Anything else dies as a form error before the wallet moves.
    """

    name_persona = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'field-input', 'data-style': 'persona'}),
        choices=lambda: [
            (slug, f"{meta['label']} — {meta['blurb']}")
            for slug, meta in NAME_PERSONAS.items()
        ],
    )
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

    def clean_name_persona(self):
        value = self.cleaned_data.get('name_persona') or 'classic'
        if value not in NAME_PERSONAS:
            raise forms.ValidationError('Pick a people-style from the list.')
        return value

