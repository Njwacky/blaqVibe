from django.contrib import admin
from .models import Category, Tag, AppProject, AppFile, Comment, Star, ProjectCoOwner

@admin.register(ProjectCoOwner)
class ProjectCoOwnerAdmin(admin.ModelAdmin):
    list_display = ('project', 'user', 'share_percent', 'created_at')
    search_fields = ('project__slug', 'user__username')
    list_filter = ('share_percent',)
    # Support tool only: the app validates Σ ≤ 100 through CoOwnerForm; the
    # CheckConstraint guards per-row bounds. No add/edit shortcuts here that
    # could bypass the sum rule — inline editing is disabled.
    def has_change_permission(self, request, obj=None):
        return False

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name','slug','type','order')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}

class AppFileInline(admin.TabularInline):
    model = AppFile
    extra = 0

@admin.register(AppProject)
class AppProjectAdmin(admin.ModelAdmin):
    list_display = ('title','owner','category','status','ai_generated','stars','clones','views','created_at')
    list_filter = ('status','category','ai_generated','is_featured')
    search_fields = ('title','owner__username','tech_stack')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [AppFileInline]
    # `trust` is the single field only the trust pipeline (gallery/trust.py)
    # may write — it is the "this passed the human + scanner gauntlet"
    # signal that the marketplace ranks on. Letting a superuser hand-edit it
    # in admin bypasses the pipeline, so it is read-only here. Status, scan
    # results and prices flow through the scan/publish actions, not admin.
    readonly_fields = ('trust',)

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('project','user','parent','is_hidden','created_at')
    list_filter = ('is_hidden',)

# ----------------------------------------------------------------------
# Attention cases. Read-mostly on purpose: the state machine (open → decided →
# deleted, with a 24h clock that ENDS IN AN ERASURE) is written by
# gallery/attention.py alone, and an admin form that could set `status` or
# `final_delete_at` by hand would be a way to delete a user's build without the
# notification that promises it. Staff get visibility, not the pen.
# ----------------------------------------------------------------------
from .models import AttentionCandidate, AttentionCase, Notification

class AttentionCandidateInline(admin.TabularInline):
    model = AttentionCandidate
    extra = 0
    fields = ('title', 'role', 'outcome', 'score', 'parked_at', 'deleted_at')
    readonly_fields = ('title', 'role', 'outcome', 'score', 'parked_at', 'deleted_at')

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(AttentionCase)
class AttentionCaseAdmin(admin.ModelAdmin):
    list_display = ('pk', 'user', 'kind', 'severity', 'status', 'decision_source',
                    'headline', 'remind_count', 'expires_at', 'created_at')
    list_filter = ('kind', 'severity', 'status', 'decision_source')
    search_fields = ('user__username', 'headline', 'detail')
    readonly_fields = (
        'user', 'kind', 'severity', 'status', 'headline', 'detail', 'fix_hint',
        'subject', 'pair_key', 'evidence', 'decision_source', 'keeper',
        'rationale', 'scores', 'created_at', 'updated_at', 'expires_at',
        'decided_at', 'acknowledged_at', 'final_delete_at', 'deleted_at',
        'reminded_at', 'remind_count', 'dismissed_reason',
    )
    inlines = [AttentionCandidateInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # View-only: see the evidence, the rationale and the clock. Acting on a
        # case is the owner's job, through /attention/.
        return False

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('pk', 'user', 'kind', 'category', 'title', 'is_read',
                    'reminded_at', 'created_at')
    list_filter = ('category', 'kind', 'is_read')
    search_fields = ('user__username', 'title', 'body')
    readonly_fields = ('attention_case', 'reminded_at')
    # The inbox is a person's private surface; staff may look, not write.
    def has_add_permission(self, request):
        return False
