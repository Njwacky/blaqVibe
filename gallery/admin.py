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
