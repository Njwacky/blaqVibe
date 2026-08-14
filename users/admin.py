from django.contrib import admin

from .models import AdminLog, Profile, StarEvent


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
