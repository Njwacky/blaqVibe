from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm, SetPasswordForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator
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


class StyledPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'class': 'field-input'}))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'field-input'}))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'field-input'}))


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
    not know. Anything else dies as a form error before the wallet moves.

    5 Whys — why the people-style is a form field too (four points each):
    1. Why not trust a hidden input the template printed?
       a. Hidden fields are still POST data; a crafted slug must die here
          before set_name_style runs, same as a crafted font.
       b. Choices come FROM NAME_PERSONAS, so the form cannot accept a
          21st people-style the CSS file does not define.
       c. required=False lets a no-JS client omit the select (old bookmarks,
          existing tests) and still save a Classic mix.
       d. clean_name_persona maps empty → classic so the writer never sees
          None and the ledger ref always has a slug.
    2. Why ONE <select> dropdown and not a grid of cards?
       a. The picker lives on Edit Profile ("my profile"), next to the
          bio/avatar form — a 21-card grid crowded the page into a
          showroom; one dropdown keeps it a form.
       b. A <select> cannot preview each look in place, so the option text
          carries the label + blurb and the live preview span beside the
          dropdown renders the real composed look on every change — the
          styles display moved, it did not die.
       c. The widget still exists on the form so a future admin tool that
          renders {{ form }} gets a valid field, not a missing key.
       d. One <select> name=name_persona on the wire — the exact POST
          shape the radio grid produced, so no-JS clients and existing
          tests post unchanged.
    3. Why reject an unknown persona at the form instead of coercing?
       a. A POST that invents "namepersona-xss" is an attack, not a typo.
          Fail the form, charge nothing, same as comic-sans-custom.
       b. Direct callers of set_name_style still coerce — the form is the
          HTTP gate, the composer is the storage gate.
       c. Existing font tests already assert form.errors['name_font'];
          persona must behave identically or the policy forks.
       d. Coercing at the form would hide bugs in the template (a typo'd
          radio value would silently become Classic and look like a no-op).
    4. Why keep the four dropdowns after adding people-styles?
       a. Classic + mix is the documented custom path; removing the
          dropdowns would lock every non-recipe look behind a code change.
       b. Fine-tune that no longer matches a recipe clears the persona
          (compose_name_style) — the dropdowns are how that mix is posted.
       c. No-JS users who never open the people-style list can still
          restyle via the four fields, same as before this feature.
       d. Preview JS fills the dropdowns from the recipe so what you save
          is what the preview showed, even if they never touch a <select>.
    5. Why is Classic in NAME_PERSONAS if it is not one of the twenty?
       a. The dropdown needs a default option; Classic is that option.
       b. compose_name_style treats 'classic' as "no extra class", so the
          slug and the look stay aligned.
       c. One dict drives form choices, preview maps, and CSS class
          lookup — a parallel CLASSIC_SLUG constant would drift.
       d. Tests count people_style_slugs() == 20 and assert 'classic'
          is present; both invariants live next to the same dict.
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

