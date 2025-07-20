# Setup Instructions (README.md style)
"""
SETUP INSTRUCTIONS:

1. Create project structure:
   mkdir django_blog
   cd django_blog
   
2. Create virtual environment:
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
3. Install dependencies:
   pip install Django Pillow python-decouple
   
4. Create Django project:
   django-admin startproject blog_project .
   cd blog_project
   python manage.py startapp blog
   
5. Copy all the code files to their respective locations
   
6. Create directories:
   mkdir templates static media
   mkdir templates/blog
   mkdir static/css static/js static/images
   
7. Create .env file in root directory:
   SECRET_KEY=your-secret-key-here
   DEBUG=True
   
8. Run migrations:
   python manage.py makemigrations
   python manage.py migrate
   
9. Create sample data:
   python manage.py create_sample_data
   
10. Run development server:
    python manage.py runserver
    
11. Access the site:
    - Blog: http://127.0.0.1:8000/
    - Admin: http://127.0.0.1:8000/admin/
    - Login: admin/admin123

FEATURES:
✅ Blog post management
✅ Category system
✅ Comment system
✅ User profiles
✅ Image uploads
✅ Search functionality
✅ Admin panel customization
✅ SEO-friendly URLs
✅ Responsive design ready
✅ Featured posts
✅ View counting
"""