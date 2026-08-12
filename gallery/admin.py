from django.contrib import admin
from .models import Category, Tag, AppProject, AppFile, Comment, Star

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
