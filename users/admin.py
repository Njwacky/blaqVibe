from django.contrib import admin

from .models import AdminLog, Profile, StarEvent


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'stars_balance', 'is_pro', 'email_verified', 'created_at')
    list_filter = ('role', 'is_pro', 'email_verified')
    search_fields = ('user__username', 'user__email', 'website', 'github', 'twitter', 'canvas_url')
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
