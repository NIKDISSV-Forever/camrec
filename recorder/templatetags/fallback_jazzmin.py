from django import template

register = template.Library()


@register.simple_tag
def get_jazzmin_ui_tweaks(*args, **kwargs):
    try:
        from jazzmin.templatetags.jazzmin import get_jazzmin_ui_tweaks as real
        return real(*args, **kwargs)
    except ImportError:
        return {"button_classes": {"primary": "btn-primary"}}
