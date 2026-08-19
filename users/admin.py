from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import User

from gallery.profanity import validate_public_text

from .models import AdminLog, Profile, StarEvent


def _clean_language(value):
    """The public-language gate for usernames edited in Django admin.

    Staff edits go through full_clean(), so a blocked username becomes a
    form error here instead of @slur on every card. (Shell writes still
    bypass forms — the render-time display backstop catches those.)
    """
    return validate_public_text(value, allow_blank=False)


class BlaqUserChangeForm(UserChangeForm):
    def clean_username(self):
        # UserChangeForm defines no clean_username of its own — read the
        # field straight from cleaned_data and gate it.
        return _clean_language(self.cleaned_data.get('username'))


class BlaqUserCreationForm(UserCreationForm):
    def clean_username(self):
        return _clean_language(super().clean_username())


# The stock auth admin lets staff type any username; the gate above
# replaces it. Unregister + re-register keeps every other UserAdmin
# behavior (password reset, permissions) intact.
admin.site.unregister(User)


@admin.register(User)
class BlaqUserAdmin(DjangoUserAdmin):
    form = BlaqUserChangeForm
    add_form = BlaqUserCreationForm
    list_display = ('username', 'email', 'is_staff', 'date_joined')
    search_fields = ('username', 'email')


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'stars_balance', 'is_pro', 'email_verified', 'created_at')
    list_filter = ('role', 'is_pro', 'email_verified')
    search_fields = ('user__username', 'user__email')
    # The wallet is moved by ledgered code paths only. Editing the integer
    # here would desync it from StarEvent — use a StarEvent('admin_adjust')
    # via the ledger instead.
    readonly_fields = ('stars_balance',)


@admin.register(StarEvent)
class StarEventAdmin(admin.ModelAdmin):
    """Append-only in admin too: no add, no edit, no delete."""
    list_display = ('user', 'delta', 'reason', 'ref', 'created_at')
    list_filter = ('reason',)
    search_fields = ('user__username', 'ref')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdminLog)
class AdminLogAdmin(admin.ModelAdmin):
    list_display = ('actor', 'action', 'target', 'created_at')
    search_fields = ('actor__username', 'action', 'target')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
