from django.contrib import admin
from .models import Category, Tag, AppProject, AppFile, Comment, Star, ProjectCoOwner, CommentReport


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

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('project','user','parent','is_hidden','created_at')
    list_filter = ('is_hidden',)
    search_fields = ('body','user__username','project__slug')

@admin.register(CommentReport)
class CommentReportAdmin(admin.ModelAdmin):
    list_display = ('comment','reason','reporter','resolved','created_at')
    list_filter = ('resolved','reason')
    search_fields = ('comment__body','reporter__username')
