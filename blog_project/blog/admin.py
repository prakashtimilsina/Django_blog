from django.contrib import admin
from django.utils.html import format_html
from .models import Category, Post, Comment, Profile

# Register your models here.
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'post_count', 'created_at']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'description']
    list_filter = ['created_at']
    
    def post_count(self, obj):
        return obj.posts.count()
    post_count.short_description = 'Posts'

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['title', 'author', 'category', 'status', 'featured', 'views', 'published_at']
    list_filter = ['status', 'created_at', 'published_at', 'category', 'featured']
    search_fields = ['title', 'content']
    prepopulated_fields = {'slug': ('title',)}
    raw_id_fields = ['author']
    date_hierarchy = 'published_at'
    ordering = ['status', '-created_at']
    list_editable = ['status', 'featured']
    
    fieldsets = (
        ('Post Information', {
            'fields': ('title', 'slug', 'author', 'category')
        }),
        ('Content', {
            'fields': ('excerpt', 'content', 'featured_image')
        }),
        ('Publication', {
            'fields': ('status', 'featured', 'published_at')
        }),
        ('Metadata', {
            'fields': ('views',),
            'classes': ('collapse',)
        })
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # If creating new post
            obj.author = request.user
        super().save_model(request, obj, form, change)

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['name', 'post', 'email', 'created_at', 'active']
    list_filter = ['active', 'created_at']
    search_fields = ['name', 'email', 'content']
    actions = ['approve_comments', 'reject_comments']
    
    def approve_comments(self, request, queryset):
        queryset.update(active=True)
        self.message_user(request, f"{queryset.count()} comments approved.")
    approve_comments.short_description = "Approve selected comments"
    
    def reject_comments(self, request, queryset):
        queryset.update(active=False)
        self.message_user(request, f"{queryset.count()} comments rejected.")
    reject_comments.short_description = "Reject selected comments"

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'website', 'twitter', 'github']
    search_fields = ['user__username', 'user__email', 'bio']

# Customize admin site
admin.site.site_header = "Personal Blog Admin"
admin.site.site_title = "Blog Admin"
admin.site.index_title = "Welcome to Blog Administration"