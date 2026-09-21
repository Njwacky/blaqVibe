from django.contrib import admin

from .models import (
    AdminLog,
    FeedbackMessage,
    FeedbackThread,
    FooterContact,
    Profile,
    ProfileLink,
    QuarantineAppeal,
    RuleViolation,
    StarEvent,
    UserQuarantine,
)

class ProfileLinkInline(admin.TabularInline):
    """A builder's website rows, editable straight from their Profile page.

    The same ProfileLink.clean() gates run here as on the public editor —
    a moved row without a new address cannot be saved from either place.
    """

    model = ProfileLink
    extra = 0
    max_num = ProfileLink.MAX_LINKS
    fields = ('label', 'url', 'status', 'moved_to', 'position')

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'stars_balance', 'is_pro', 'email_verified', 'created_at')
    list_filter = ('role', 'is_pro', 'email_verified')
    search_fields = ('user__username', 'user__email', 'website', 'github', 'twitter', 'canvas_url')
    inlines = [ProfileLinkInline]
    # The wallet is moved by ledgered code paths only. Editing the integer
    # here would desync it from StarEvent — use a StarEvent('admin_adjust')
    # via the ledger instead.
    readonly_fields = ('stars_balance',)

@admin.register(ProfileLink)
class ProfileLinkAdmin(admin.ModelAdmin):
    """Every member website row, with its status light — for moderation.

    Green/amber/grey/⇗ lives on the row, so an operator can answer "is this
    member's site up?" without leaving the admin.
    """

    list_display = ('label', 'profile', 'url', 'status', 'moved_to', 'position')
    list_filter = ('status',)
    search_fields = ('label', 'url', 'moved_to', 'profile__user__username')
    autocomplete_fields = ('profile',)


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

@admin.register(FooterContact)
class FooterContactAdmin(admin.ModelAdmin):
    """The public footer contact list, for operators who prefer /django-admin/.

    The same validation as the operator page runs here: FooterContact.clean()
    rejects a value that does not match its kind, so a pasted javascript: URL
    cannot reach the public footer from either editor.
    """
    list_display = ('kind', 'value', 'label', 'position', 'is_active', 'updated_at')
    list_filter = ('kind', 'is_active')
    list_editable = ('position', 'is_active')
    search_fields = ('value', 'label')
    ordering = ('position', 'id')


# Quarantine tables are append-only in the Django admin too: the ONLY writers
# are users/quarantine.py's functions (they notify the person and fan out to
# staff). An operator who edits a row here would silently change somebody's
# access without telling them — point them at /moderation/appeals/ instead.
@admin.register(UserQuarantine)
class UserQuarantineAdmin(admin.ModelAdmin):
    list_display = ('user', 'reason', 'status', 'source', 'started_at', 'ends_at', 'imposed_by')
    list_filter = ('status', 'reason', 'source')
    search_fields = ('user__username', 'detail', 'lift_note')
    date_hierarchy = 'started_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(QuarantineAppeal)
class QuarantineAppealAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'created_at', 'reviewed_by', 'reviewed_at')
    list_filter = ('status',)
    search_fields = ('user__username', 'message', 'decision_note')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(RuleViolation)
class RuleViolationAdmin(admin.ModelAdmin):
    list_display = ('user', 'kind', 'surface', 'quarantined', 'created_at')
    list_filter = ('kind', 'surface', 'quarantined')
    search_fields = ('user__username', 'evidence', 'detail')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Feedback conversations are read-only in the Django admin too: the ONLY
# writers are users/feedback.py's views, which keep the superadmin inbox,
# the nav badge and the user's notification rows in sync. An operator who
# edits a row here would desync the unread counts — they reply from
# /admin/feedback/ instead, which is the whole point of the channel.
class FeedbackMessageInline(admin.TabularInline):
    model = FeedbackMessage
    extra = 0
    can_delete = False
    # Do not render ImageField.url in Django admin: a staff member who is not
    # an authorized superadmin must not receive a direct media URL. The app's
    # feedback conversation endpoint performs the role/owner check.
    fields = ('sender', 'from_staff', 'body', 'attachment_status', 'created_at')
    readonly_fields = fields

    @admin.display(description='Attachment')
    def attachment_status(self, obj):
        return 'Screenshot attached — open the feedback conversation to view it securely.' if obj and obj.attachment else '—'

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FeedbackThread)
class FeedbackThreadAdmin(admin.ModelAdmin):
    list_display = ('pk', 'user', 'status', 'last_user_message_at', 'admin_last_read_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'status', 'last_user_message_at', 'admin_last_read_at', 'created_at', 'updated_at')
    inlines = [FeedbackMessageInline]
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
