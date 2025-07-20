from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from blog.models import Category, Post
from django.utils.text import slugify
import random

class Command(BaseCommand):
    help = 'Create sample blog data'
    
    def handle(self, *args, **options):
        # Create superuser if not exists
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
            self.stdout.write('Created superuser: admin/admin123')
        
        # Create categories
        categories = [
            ('Technology', 'Latest tech trends and tutorials'),
            ('Travel', 'Travel experiences and guides'),
            ('Food', 'Delicious recipes and food reviews'),
            ('Lifestyle', 'Daily life tips and experiences'),
        ]
        
        for name, desc in categories:
            Category.objects.get_or_create(
                name=name,
                defaults={'slug': slugify(name), 'description': desc}
            )
        
        # Create sample posts
        admin_user = User.objects.get(username='admin')
        sample_posts = [
            {
                'title': 'Getting Started with Django',
                'content': 'Django is a powerful web framework for Python that makes it easy to build web applications. In this post, we will explore the basics of Django and how to get started with your first project.',
                'category': 'Technology',
            },
            {
                'title': 'My Trip to Japan',
                'content': 'Japan is an amazing country with rich culture and beautiful landscapes. From the bustling streets of Tokyo to the serene temples of Kyoto, every moment was magical.',
                'category': 'Travel',
            },
            {
                'title': 'Best Pasta Recipe Ever',
                'content': 'This pasta recipe will change your life! With simple ingredients and easy steps, you can create restaurant-quality pasta at home.',
                'category': 'Food',
            },
            {
                'title': 'Work-Life Balance Tips',
                'content': 'Maintaining a healthy work-life balance is crucial for both productivity and happiness. Here are some practical tips to help you achieve it.',
                'category': 'Lifestyle',
            },
        ]
        
        for post_data in sample_posts:
            category = Category.objects.get(name=post_data['category'])
            Post.objects.get_or_create(
                title=post_data['title'],
                defaults={
                    'slug': slugify(post_data['title']),
                    'author': admin_user,
                    'category': category,
                    'content': post_data['content'],
                    'status': 'published',
                    'featured': random.choice([True, False]),
                }
            )
        
        self.stdout.write(self.style.SUCCESS('Sample data created successfully!'))