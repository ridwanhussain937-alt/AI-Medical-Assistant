from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name="gpt_format")
def gpt_format(value):
    if value is None:
        return ""
    text = str(value)
    lines = text.splitlines()
    out = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue

        if stripped.startswith(("- ", "* ", "+ ")):
            if not in_list:
                out.append('<ul class="gpt-bullets">')
                in_list = True
            out.append(f"<li>{escape(stripped[2:].strip())}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{escape(stripped)}</p>")

    if in_list:
        out.append("</ul>")

    return mark_safe("".join(out))