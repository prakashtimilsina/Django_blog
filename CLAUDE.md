# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Layout

The Django project lives inside `blog_project/`. All `manage.py` commands must be run from that directory.

```
Django_blog/
└── blog_project/        ← working directory for all manage.py commands
    ├── manage.py
    ├── blog/            ← sole Django app
    ├── blog_project/    ← project settings/urls/wsgi
    ├── templates/       ← project-wide templates (base.html + blog/)
    ├── static/          ← CSS/JS/images (gitignored)
    ├── media/           ← user-uploaded files (gitignored)
    └── db.sqlite3       ← SQLite database (gitignored)
```

## Common Commands

All commands run from `blog_project/`:

```bash
# Run development server
python manage.py runserver

# Database migrations
python manage.py makemigrations
python manage.py migrate

# Seed database with sample data (creates admin/admin123 superuser + 4 categories + 4 posts)
python manage.py create_sample_data

# Run tests
python manage.py test blog

# Run a single test class or method
python manage.py test blog.tests.MyTestClass
python manage.py test blog.tests.MyTestClass.test_method

# Open Django shell
python manage.py shell
```

## Environment Setup

Create `blog_project/.env` (loaded by `python-decouple`):

```
SECRET_KEY=your-secret-key-here
DEBUG=True
```

`SECRET_KEY` and `DEBUG` fall back to insecure defaults if `.env` is absent — only acceptable for local dev.

## Architecture

**Single-app structure.** All blog logic lives in the `blog` app. There are no other apps.

**Models** (`blog/models.py`):
- `Category` — slug-based, used for URL routing and filtering
- `Post` — has `status` (`draft`/`published`) and `featured` flag; `published_at` is auto-set on first publish inside `save()`; auto-generates `excerpt` from content if blank; resizes `featured_image` to max 1200×800 px on save
- `Comment` — anonymous (name + email), has `active` flag for moderation; displayed/hidden via `active=True` filter in views
- `Profile` — one-to-one with `User`, created automatically via signal in `blog/signals.py`; avatar resized to max 300×300 px on save

**Signals** (`blog/signals.py`): `post_save` on `User` auto-creates and auto-saves the linked `Profile`. Signals are registered in `BlogConfig.ready()` (`blog/apps.py`).

**Views** (`blog/views.py`): All views are class-based (`ListView`, `DetailView`). `PostDetailView` handles both GET (render post) and POST (submit comment) via a `post()` method override. View counts are incremented on every `get_object()` call in `PostDetailView`.

**URL namespace**: `blog:` — always use `{% url 'blog:...' %}` in templates and `reverse('blog:...')` in code.

**Templates**: All templates extend `templates/base.html`. App-specific templates live in `templates/blog/`. The base template loads Bootstrap 5.1.3 and Font Awesome 6 from CDN — no local build step.

**Media files**: Served by Django only in `DEBUG=True` mode (configured in `blog_project/urls.py`). For production, a proper media-serving setup is required.

**No linting or formatting config** is present in this repo. Use standard Django/Python conventions.
