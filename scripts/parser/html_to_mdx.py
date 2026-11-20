from __future__ import annotations

import json
import re
import textwrap
import unicodedata
from typing import cast

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, ProcessingInstruction, Tag
from loguru import logger

GREEK_AND_MATH_CHARS: dict[str, str] = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\varepsilon",
    "ϵ": r"\epsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ϑ": r"\vartheta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ϖ": r"\varpi",
    "ρ": r"\rho",
    "ϱ": r"\varrho",
    "σ": r"\sigma",
    "ς": r"\varsigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\varphi",
    "ϕ": r"\phi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Θ": r"\Theta",
    "Λ": r"\Lambda",
    "Ξ": r"\Xi",
    "Π": r"\Pi",
    "Σ": r"\Sigma",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
    "∞": r"\infty",
    "∂": r"\partial",
    "∇": r"\nabla",
    "ℏ": r"\hbar",
    "ℓ": r"\ell",
    "ℜ": r"\Re",
    "ℑ": r"\Im",
    "℘": r"\wp",
    "∅": r"\emptyset",
    "∈": r"\in",
    "∉": r"\notin",
    "∋": r"\ni",
    "⊂": r"\subset",
    "⊃": r"\supset",
    "⊆": r"\subseteq",
    "⊇": r"\supseteq",
    "∪": r"\cup",
    "∩": r"\cap",
    "∧": r"\wedge",
    "∨": r"\vee",
    "¬": r"\neg",
    "∀": r"\forall",
    "∃": r"\exists",
    "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow",
    "→": r"\to",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "↦": r"\mapsto",
    "≤": r"\le",
    "≥": r"\ge",
    "≠": r"\ne",
    "≈": r"\approx",
    "≡": r"\equiv",
    "∼": r"\sim",
    "≃": r"\simeq",
    "≅": r"\cong",
    "∝": r"\propto",
    "≪": r"\ll",
    "≫": r"\gg",
    "−": "-",
    "±": r"\pm",
    "∓": r"\mp",
    "×": r"\times",
    "÷": r"\div",
    "⋅": r"\cdot",
    "·": r"\cdot",
    "∗": r"\ast",
    "∘": r"\circ",
    "⊕": r"\oplus",
    "⊗": r"\otimes",
    "∑": r"\sum",
    "∏": r"\prod",
    "∐": r"\coprod",
    "∫": r"\int",
    "∬": r"\iint",
    "∭": r"\iiint",
    "∮": r"\oint",
    "…": r"\dots",
    "⋯": r"\cdots",
    "⋮": r"\vdots",
    "⋱": r"\ddots",
    "⟨": r"\langle",
    "⟩": r"\rangle",
    "⌊": r"\lfloor",
    "⌋": r"\rfloor",
    "⌈": r"\lceil",
    "⌉": r"\rceil",
}

BIG_OPERATORS = {r"\sum", r"\prod", r"\coprod", r"\int", r"\iint", r"\iiint", r"\oint"}


def normalize_math_text(text: str) -> str:
    result: list[str] = []
    for ch in text:
        normalized = unicodedata.normalize("NFKC", ch)
        if normalized in GREEK_AND_MATH_CHARS:
            result.append(GREEK_AND_MATH_CHARS[normalized])
        elif ch in GREEK_AND_MATH_CHARS:
            result.append(GREEK_AND_MATH_CHARS[ch])
        else:
            result.append(normalized)
    return "".join(result)


def mathml_node_to_latex(node) -> str:
    if isinstance(node, NavigableString):
        return normalize_math_text(str(node))

    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()

    if name == "math":
        parts = [mathml_node_to_latex(c) for c in node.children]
        return " ".join(p for p in parts if p).strip()

    if name == "mrow":
        children = [c for c in node.children if not (isinstance(c, NavigableString) and not str(c).strip())]
        parts: list[str] = []
        i = 0
        while i < len(children):
            cur = children[i]
            if (
                isinstance(cur, Tag)
                and cur.name == "mo"
                and cur.get_text(strip=True) == "{"
                and i + 1 < len(children)
                and isinstance(children[i + 1], Tag)
                and children[i + 1].name == "mtable"
                and "cases" in (children[i + 1].get("class") or [])
            ):
                i += 1
                continue
            rendered = mathml_node_to_latex(cur)
            if rendered:
                parts.append(rendered)
            i += 1
        return " ".join(parts).strip()

    if name == "mi":
        val = normalize_math_text(node.get_text(strip=True))
        if len(val) > 1 and not val.startswith("\\"):
            return rf"\mathrm{{{val}}}"
        return val

    if name == "mn":
        return node.get_text(strip=True)

    if name == "mo":
        raw = node.get_text(strip=True)
        val = normalize_math_text(raw)
        if raw == "d":
            return r"\mathrm{d}"
        if raw in {"max", "min", "sin", "cos", "tan", "ln", "log", "exp", "lim", "sup", "inf"}:
            return f"\\{raw}"
        return val

    if name == "mtext":
        txt = node.get_text().replace("{", r"\{").replace("}", r"\}")
        return rf"\text{{{txt}}}"

    if name == "mspace":
        width = str(node.get("width", ""))
        if "1em" in width:
            return r"\quad"
        if "0.16" in width or "0.22" in width:
            return r"\,"
        return r"\ "

    if name == "mfrac":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            return rf"\frac{{{mathml_node_to_latex(args[0])}}}{{{mathml_node_to_latex(args[1])}}}"

    if name == "msqrt":
        inner = " ".join(mathml_node_to_latex(c) for c in node.children).strip()
        return rf"\sqrt{{{inner}}}"

    if name == "mroot":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            return rf"\sqrt[{mathml_node_to_latex(args[1])}]{{{mathml_node_to_latex(args[0])}}}"

    if name == "msup":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            base = mathml_node_to_latex(args[0])
            sup = mathml_node_to_latex(args[1])
            return f"{base}^{{{sup}}}"

    if name == "msub":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            base = mathml_node_to_latex(args[0])
            sub = mathml_node_to_latex(args[1])
            return f"{base}_{{{sub}}}"

    if name == "msubsup":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 3:
            base = mathml_node_to_latex(args[0])
            sub = mathml_node_to_latex(args[1])
            sup = mathml_node_to_latex(args[2])
            return f"{base}_{{{sub}}}^{{{sup}}}"

    if name == "munder":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            base = mathml_node_to_latex(args[0])
            under = mathml_node_to_latex(args[1])
            if base in BIG_OPERATORS or base.startswith(r"\lim"):
                return f"{base}_{{{under}}}"
            return rf"\underset{{{under}}}{{{base}}}"

    if name == "mover":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 2:
            base = mathml_node_to_latex(args[0])
            over = mathml_node_to_latex(args[1])
            if base in BIG_OPERATORS:
                return f"{base}^{{{over}}}"
            return rf"\overset{{{over}}}{{{base}}}"

    if name == "munderover":
        args = [c for c in node.children if isinstance(c, Tag)]
        if len(args) >= 3:
            base = mathml_node_to_latex(args[0])
            under = mathml_node_to_latex(args[1])
            over = mathml_node_to_latex(args[2])
            if base in BIG_OPERATORS or base.startswith((r"\max", r"\min", r"\lim")):
                return f"{base}_{{{under}}}^{{{over}}}"
            return rf"\overset{{{over}}}{{\underset{{{under}}}{{{base}}}}}"

    if name == "mtable":
        classes = node.get("class") or []
        env = "cases" if "cases" in classes else "aligned" if "aligned" in classes else "matrix"
        rows = []
        for mtr in node.find_all("mtr", recursive=False):
            cells = [mathml_node_to_latex(mtd) for mtd in mtr.find_all("mtd", recursive=False)]
            rows.append(" & ".join(cells))
        body = " \\\\ ".join(rows)
        return rf"\begin{{{env}}} {body} \end{{{env}}}"

    if name == "mfenced":
        open_ch = str(node.get("open", "("))
        close_ch = str(node.get("close", ")"))
        inner = ", ".join(mathml_node_to_latex(c) for c in node.children if isinstance(c, Tag))
        return rf"\left{open_ch} {inner} \right{close_ch}"

    return " ".join(mathml_node_to_latex(c) for c in node.children).strip()


def clean_latex(expr: str) -> str:
    expr = re.sub(r"\s+", " ", expr).strip()
    expr = re.sub(r"\{\s*([+\-])\s+", r"{\1", expr)
    expr = re.sub(r"\(\s+", "(", expr)
    expr = re.sub(r"\s+\)", ")", expr)
    expr = re.sub(r"\s+([,;:.!])", r"\1", expr)
    return expr


def process_math(element: Tag, math_format: str = "latex") -> str:
    if math_format == "mathml":
        raw = str(element)
        soup_copy = BeautifulSoup(raw, "html.parser")
        for mo in soup_copy.find_all("mo"):
            if mo.string:
                mo.string.replace_with(mo.string.replace("<", "&lt;").replace(">", "&gt;"))
        for tag in soup_copy.find_all(True):
            if tag.has_attr("class"):
                tag["className"] = tag.pop("class")
        return str(soup_copy)

    is_block = element.get("display") == "block"
    latex = clean_latex(mathml_node_to_latex(element))

    # Для блочных формул используем fenced code-блок ```math, который MDX/Acorn НЕ парсит как JSX!
    if is_block:
        return f"```math\n{latex}\n```"

    # Для простых инлайн-выражений без сложных операторов (буквы, числа, греческие символы)
    if not any(c in latex for c in ("{", "}", "\\frac", "\\sqrt", "\\begin")):
        return f"${latex}$"

    # Если внутри инлайн-формулы есть фигурные скобки, оборачиваем в чистый инлайн-код math, чтобы Acorn не падал
    return f"`{latex}`{{:math}}"


def escape_mdx_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("<", "\\<")
    text = text.replace(">", "\\>")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    text = text.replace("*", "\\*")
    text = text.replace("_", "\\_")
    text = text.replace("`", "\\`")
    return text


def format_inline_code(text: str, lang: str | None = None) -> str:
    max_ticks = 0
    for match in re.finditer(r"`+", text):
        max_ticks = max(max_ticks, len(match.group(0)))

    fence = "`" * (max_ticks + 1)
    suffix = f"{{:{lang}}}" if lang else ""

    if max_ticks > 0 or text.startswith("`") or text.endswith("`"):
        return f"{fence} {text}{suffix} {fence}"
    return f"{fence}{text}{suffix}{fence}"


def format_fenced_code(code_text: str, lang: str = "typst") -> str:
    code_text = code_text.rstrip("\n")
    max_ticks = 2
    for match in re.finditer(r"`{3,}", code_text):
        max_ticks = max(max_ticks, len(match.group(0)))
    fence = "`" * (max_ticks + 1)
    return f"{fence}{lang}\n{code_text}\n{fence}"


def map_lang(raw_lang: str | None) -> str:
    if not raw_lang:
        return ""
    if raw_lang in {"typ", "typc", "typst"}:
        return "typst"
    return raw_lang


def extract_code_text(code_tag: Tag) -> str:
    clone = BeautifulSoup(str(code_tag), "html.parser")
    for br in clone.find_all("br"):
        br.replace_with("\n")
    return clone.get_text()


def parse_style_to_jsx(style_str: str) -> str:
    if not style_str:
        return ""

    jsx_props = []
    declarations = [d.strip() for d in style_str.split(";") if d.strip()]

    for decl in declarations:
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip()
        val = val.strip()
        if "-" in prop:
            parts = prop.split("-")
            prop = parts[0] + "".join(p.capitalize() for p in parts[1:])
        val = val.replace("'", "\\'")
        jsx_props.append(f"{prop}: '{val}'")

    return "{{" + ", ".join(jsx_props) + "}}"


def normalize_href(href: str, link_prefix: str = "/") -> str:
    if not href or href.startswith(("http://", "https://", "mailto:", "#")):
        return href

    path, sep, anchor = href.partition("#")
    path = path.strip("/")

    if not path:
        base = link_prefix if link_prefix else "/"
    elif link_prefix == "":
        base = path
    else:
        prefix = link_prefix if link_prefix.endswith("/") else f"{link_prefix}/"
        base = f"{prefix}{path}"

    return f"{base}{sep}{anchor}" if sep else base


def rewrite_image_src(src: str, image_public_path: str = "/docs-assets/images") -> str:
    if not src:
        return src
    if src.startswith("/assets/images/"):
        filename = src[len("/assets/images/"):]
        prefix = image_public_path.rstrip("/")
        return f"{prefix}/{filename}"
    if src.startswith("assets/images/"):
        filename = src[len("assets/images/"):]
        prefix = image_public_path.rstrip("/")
        return f"{prefix}/{filename}"
    return src


def format_img_jsx(img: Tag, image_public_path: str = "/docs-assets/images") -> str:
    raw_src = str(img.get("src", ""))
    src = rewrite_image_src(raw_src, image_public_path)
    alt = str(img.get("alt", ""))
    attrs = [f"src={json.dumps(src)}", f"alt={json.dumps(alt)}"]

    style = cast(str | None, img.get("style"))
    if style:
        attrs.append(f"style={parse_style_to_jsx(style)}")

    if img.get("width"):
        attrs.append(f"width={json.dumps(str(img.get('width')))}")
    if img.get("height"):
        attrs.append(f"height={json.dumps(str(img.get('height')))}")

    return f"<img {' '.join(attrs)} />"


def process_preview_code(element: Tag, image_public_path: str = "/docs-assets/images") -> str:
    pre_block = element.find("pre")
    preview_block = element.find("div", class_="preview")
    images = preview_block.find_all("img") if isinstance(preview_block, Tag) else element.find_all("img")

    if not pre_block:
        return "\n\n".join(format_img_jsx(img, image_public_path) for img in images if isinstance(img, Tag))

    code_tag = pre_block.find("code")
    if isinstance(code_tag, Tag):
        code_text = extract_code_text(code_tag).rstrip()
    else:
        pre_clone = BeautifulSoup(str(pre_block), "html.parser")
        for copy_div in pre_clone.find_all("div", class_="copy"):
            copy_div.decompose()
        code_text = pre_clone.get_text().rstrip()

    if not images:
        lang = map_lang(cast(str | None, code_tag.get("data-lang")) if isinstance(code_tag, Tag) else "typ") or "typst"
        return format_fenced_code(code_text, lang)

    first_img = images[0]
    raw_src = str(first_img.get("src", ""))
    src = rewrite_image_src(raw_src, image_public_path)
    alt = str(first_img.get("alt", ""))

    escaped_code = (
        code_text.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )
    indented_code = textwrap.indent(escaped_code, "  ")
    code_prop = "{`\n" + indented_code + "\n`}"

    preview_mdx = (
        f"<TypstPreview\n"
        f"  code={code_prop}\n"
        f"  image={json.dumps(src)}\n"
        f"  alt={json.dumps(alt)}\n"
        f"  editable={{true}}\n"
        f"/>"
    )

    if len(images) > 1:
        extra_imgs = "\n\n".join(format_img_jsx(img, image_public_path) for img in images[1:] if isinstance(img, Tag))
        return f"{preview_mdx}\n\n{extra_imgs}"

    return preview_mdx


def process_pre(element: Tag) -> str:
    code_tag = element.find("code")
    if isinstance(code_tag, Tag):
        code_text = extract_code_text(code_tag)
        lang = map_lang(cast(str | None, code_tag.get("data-lang")))
    else:
        pre_clone = BeautifulSoup(str(element), "html.parser")
        for copy_div in pre_clone.find_all("div", class_="copy"):
            copy_div.decompose()
        code_text = pre_clone.get_text()
        lang = "typst"

    return format_fenced_code(code_text, lang)


def process_code_definition(element: Tag) -> str:
    clone = BeautifulSoup(str(element), "html.parser")
    root = clone.find("div", class_="code-definition")
    if not isinstance(root, Tag):
        return ""

    args_container = root.find(class_="arguments")
    prefix_parts: list[str] = []
    return_parts: list[str] = []
    seen_args = False

    for child in root.children:
        if child == args_container:
            seen_args = True
            continue
        if not seen_args:
            if isinstance(child, Tag):
                prefix_parts.append(child.get_text())
            elif isinstance(child, NavigableString):
                prefix_parts.append(str(child))
        else:
            if isinstance(child, Tag):
                if "pill" in (child.get("class") or []):
                    return_parts.append(child.get_text(strip=True))
                else:
                    return_parts.append(child.get_text())
            elif isinstance(child, NavigableString):
                return_parts.append(str(child))

    prefix = "".join(prefix_parts).strip()
    params: list[str] = []
    if isinstance(args_container, Tag):
        for param in args_container.find_all("span", class_="overview-param", recursive=False):
            p_clone = BeautifulSoup(str(param), "html.parser")
            p_span = p_clone.find("span", class_="overview-param")
            if not isinstance(p_span, Tag):
                continue

            pills = [p.get_text(strip=True) for p in p_span.find_all(class_="pill")]
            for pill in p_span.find_all(class_="pill"):
                pill.replace_with("__PILL__")

            raw_text = p_span.get_text().strip().rstrip(",").strip()
            types_str = " | ".join(pills)

            if "__PILL__" in raw_text:
                before_pills = raw_text.split("__PILL__", 1)[0]
                params.append(f"{before_pills}{types_str}")
            else:
                params.append(raw_text)

    ret_pills = [
        p.get_text(strip=True)
        for p in root.find_all(class_="pill")
        if not (isinstance(args_container, Tag) and p in args_container.descendants)
    ]
    ret_str = f" -> {' | '.join(ret_pills)}" if ret_pills else ""

    if len(params) <= 1 and ("single-arg" in (element.get("class") or []) or not params):
        sig = f"{prefix}{', '.join(params)}){ret_str}"
    else:
        sig = f"{prefix}\n" + ",\n".join(f"  {p}" for p in params) + f",\n){ret_str}"

    return format_fenced_code(sig, "typst")


def process_heading(element: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    level = int(element.name[1])
    heading_id = cast(str | None, element.get("id"))

    clone = BeautifulSoup(str(element), "html.parser")
    h_tag = clone.find(element.name)
    assert isinstance(h_tag, Tag)

    for ui in h_tag.find_all(class_=["tooltip-context", "sources-link"]):
        ui.decompose()

    meta_parts: list[str] = []
    extra_blocks: list[str] = []

    additional_info = h_tag.find("div", class_="additional-info")
    if isinstance(additional_info, Tag):
        type_parts: list[str] = []
        mod_parts: list[str] = []

        for child in additional_info.children:
            if not isinstance(child, Tag):
                continue
            classes = child.get("class") or []
            if "mod" in classes:
                mod_txt = child.get_text(strip=True)
                if mod_txt:
                    mod_parts.append(mod_txt)
            elif child.name == "small" and child.get_text(strip=True).startswith("Since:"):
                meta_parts.append(f"*{child.get_text(strip=True)}*")
            elif "pill" in classes:
                type_parts.append(process_inline(child, link_prefix, math_format, image_public_path).strip())
            elif child.name == "div":
                t_str = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in child.children).strip()
                if t_str:
                    type_parts.append(t_str)

        if type_parts:
            meta_parts.append(f"**Type:** {' '.join(type_parts)}")
        if mod_parts:
            meta_parts.append(f"**Modifiers:** {', '.join(mod_parts)}")
        additional_info.decompose()

    default_tag = h_tag.find("small", class_="default")
    if isinstance(default_tag, Tag):
        code_in_default = default_tag.find("code")
        if isinstance(code_in_default, Tag):
            default_code = extract_code_text(code_in_default)
            if "\n" in default_code:
                extra_blocks.append("**Default:**\n\n" + format_fenced_code(default_code, "typst"))
            else:
                lang = map_lang(cast(str | None, code_in_default.get("data-lang"))) or "typst"
                meta_parts.append(f"**Default:** {format_inline_code(default_code.strip(), lang)}")
        else:
            meta_parts.append(default_tag.get_text(strip=True))
        default_tag.decompose()

    for sm in h_tag.find_all("small"):
        txt = sm.get_text(strip=True)
        if txt:
            meta_parts.append(f"*{txt}*")
        sm.decompose()

    title_text = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in h_tag.children).strip()
    title_text = re.sub(r"\s+", " ", title_text)

    id_suffix = f" [#{heading_id}]" if heading_id else ""
    heading_line = f"{'#' * level} {title_text}{id_suffix}"

    blocks = [heading_line]
    if meta_parts:
        blocks.append(" · ".join(meta_parts))
    blocks.extend(extra_blocks)
    return "\n\n".join(blocks)


def process_h1(element: Tag) -> str | None:
    clone = BeautifulSoup(str(element), "html.parser")
    h1 = clone.find("h1")
    if not isinstance(h1, Tag):
        return None

    for ui in h1.find_all(class_=["tooltip-context", "sources-link"]):
        ui.decompose()

    smalls = [s.get_text(strip=True) for s in h1.find_all("small") if s.get_text(strip=True)]
    heading_id = h1.get("id")

    parts = []
    if heading_id == "summary":
        parts.append('<span id="summary" />')
    if smalls:
        parts.append(" · ".join(f"*{s}*" for s in smalls))

    return "\n\n".join(parts) if parts else None


def escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _render_cell(cell: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    blocks = [c for c in cell.children if isinstance(c, Tag) and c.name in ("p", "ul", "ol")]
    if blocks:
        parts = []
        for child in cell.children:
            if isinstance(child, Tag) and child.name == "p":
                txt = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in child.children).strip()
                if txt:
                    parts.append(txt)
            elif isinstance(child, Tag) and child.name in ("ul", "ol"):
                li_texts = [
                    "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in li.children).strip()
                    for li in child.find_all("li", recursive=False)
                ]
                parts.append("; ".join(filter(None, li_texts)))
            else:
                txt = process_inline(child, link_prefix, math_format, image_public_path).strip()
                if txt:
                    parts.append(txt)
        return escape_table_cell(" <br /> ".join(parts))

    content = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in cell.children)
    return escape_table_cell(content)


def process_table(element: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    rows_md: list[str] = []
    thead = element.find("thead")
    headers: list[str] = []

    if isinstance(thead, Tag):
        header_row = thead.find("tr")
        if isinstance(header_row, Tag):
            for th in header_row.find_all(["th", "td"]):
                headers.append(_render_cell(th, link_prefix, math_format, image_public_path))

    if headers:
        rows_md.append("| " + " | ".join(headers) + " |")
        rows_md.append("| " + " | ".join(["---"] * len(headers)) + " |")

    tbody = element.find("tbody")
    tr_source = tbody if isinstance(tbody, Tag) else element
    tr_list = tr_source.find_all("tr", recursive=False)

    for tr in tr_list:
        if tr.parent and tr.parent.name == "thead":
            continue

        cells = [_render_cell(td, link_prefix, math_format, image_public_path) for td in tr.find_all(["td", "th"], recursive=False)]

        if cells:
            if not headers and not rows_md:
                rows_md.append("| " + " | ".join([""] * len(cells)) + " |")
                rows_md.append("| " + " | ".join(["---"] * len(cells)) + " |")
            rows_md.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows_md)


def process_list(element: Tag, link_prefix: str, math_format: str, image_public_path: str, depth: int = 0) -> str:
    is_ordered = element.name == "ol"
    items: list[str] = []
    indent = "  " * depth
    child_indent = "  " * (depth + 1)

    for i, li in enumerate(element.find_all("li", recursive=False)):
        marker = f"{i + 1}. " if is_ordered else "- "
        parts: list[str] = []
        inline_buf: list[str] = []

        def flush_inline():
            if inline_buf:
                text = "".join(inline_buf).strip()
                inline_buf.clear()
                if text:
                    parts.append(text)

        for sub in li.children:
            if isinstance(sub, Tag) and sub.name in ("ul", "ol"):
                flush_inline()
                parts.append(process_list(sub, link_prefix, math_format, image_public_path, depth + 1))
            elif isinstance(sub, Tag) and sub.name == "p":
                flush_inline()
                p_text = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in sub.children).strip()
                if p_text:
                    parts.append(p_text)
            elif isinstance(sub, Tag) and sub.name in ("pre", "div", "blockquote", "table"):
                flush_inline()
                block = process_element(sub, link_prefix, math_format, image_public_path)
                if block:
                    parts.append(textwrap.indent(block, child_indent))
            else:
                inline_buf.append(process_inline(sub, link_prefix, math_format, image_public_path))

        flush_inline()

        if not parts:
            continue

        first = parts[0]
        item_lines = [f"{indent}{marker}{first}"]
        for extra in parts[1:]:
            if extra.startswith(child_indent):
                item_lines.append(extra)
            else:
                item_lines.append(f"\n{child_indent}{extra}")
        items.append("\n".join(item_lines))

    return "\n".join(items)


def process_details(element: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    is_open = element.has_attr("open")
    summary_tag = element.find("summary", recursive=False)
    summary_text = "Details"
    if isinstance(summary_tag, Tag):
        summary_text = summary_tag.get_text(" ", strip=True) or "Details"

    body_blocks: list[str] = []
    for child in element.children:
        if child == summary_tag:
            continue
        res = process_element(child, link_prefix, math_format, image_public_path)
        if res:
            body_blocks.append(res)

    open_attr = " open" if is_open else ""
    inner = "\n\n".join(body_blocks)
    return f"<details{open_attr}>\n<summary>{escape_mdx_text(summary_text)}</summary>\n\n{inner}\n\n</details>"


def process_info_box(element: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    children_processed = [process_element(c, link_prefix, math_format, image_public_path) for c in element.children]
    inner_content = "\n\n".join(filter(None, children_processed))
    return f"<Callout>\n{inner_content}\n</Callout>"


def process_footnote_definition(element: Tag, link_prefix: str, math_format: str, image_public_path: str) -> str:
    fn_id = element.get("id")
    clone = BeautifulSoup(str(element), "html.parser")
    fn_el = clone.find("div", class_="footnote-definition")
    if not isinstance(fn_el, Tag):
        return ""
    label = fn_el.find(class_="footnote-definition-label")
    if isinstance(label, Tag):
        label.decompose()
    content = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in fn_el.children).strip()
    return f"[^{fn_id}]: {content}"


def process_element(
    element,
    link_prefix: str = "/",
    math_format: str = "latex",
    image_public_path: str = "/docs-assets/images",
) -> str | None:
    if isinstance(element, (Comment, Doctype, ProcessingInstruction)):
        return None

    if isinstance(element, NavigableString):
        text = str(element).strip()
        return escape_mdx_text(text) if text else None

    if not isinstance(element, Tag):
        return None

    classes = element.get("class") or []

    if element.name == "ul" and "breadcrumbs" in classes:
        return None
    if element.name == "div" and any(c in classes for c in ("page-end-buttons", "tooltip-context", "copy")):
        return None

    if element.name == "h1":
        return process_h1(element)

    if element.name in ("h2", "h3", "h4", "h5", "h6"):
        return process_heading(element, link_prefix, math_format, image_public_path)

    if element.name == "math":
        return process_math(element, math_format)

    if element.name == "details":
        return process_details(element, link_prefix, math_format, image_public_path)

    if element.name == "div":
        if "previewed-code" in classes:
            return process_preview_code(element, image_public_path)
        if "code-definition" in classes:
            return process_code_definition(element)
        if "info-box" in classes:
            return process_info_box(element, link_prefix, math_format, image_public_path)
        if "footnote-definition" in classes:
            return process_footnote_definition(element, link_prefix, math_format, image_public_path)

    if element.name in ("div", "section", "article", "aside", "figure", "figcaption", "header", "footer"):
        blocks = [process_element(c, link_prefix, math_format, image_public_path) for c in element.children]
        joined = "\n\n".join(filter(None, blocks))
        return joined or None

    if element.name == "p":
        content = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children).strip()
        return content or None

    if element.name == "blockquote":
        inner_blocks = [process_element(c, link_prefix, math_format, image_public_path) for c in element.children]
        inner = "\n\n".join(filter(None, inner_blocks))
        return textwrap.indent(inner, "> ") if inner else None

    if element.name == "hr":
        return "---"

    if element.name == "pre":
        return process_pre(element)

    if element.name == "table":
        return process_table(element, link_prefix, math_format, image_public_path)

    if element.name in ("ul", "ol"):
        return process_list(element, link_prefix, math_format, image_public_path)

    if element.name == "img":
        return format_img_jsx(element, image_public_path)

    if element.name == "svg":
        return None

    inline = process_inline(element, link_prefix, math_format, image_public_path).strip()
    return inline or None


def process_inline(
    element,
    link_prefix: str = "/",
    math_format: str = "latex",
    image_public_path: str = "/docs-assets/images",
) -> str:
    if isinstance(element, (Comment, Doctype, ProcessingInstruction)):
        return ""

    if isinstance(element, NavigableString):
        return escape_mdx_text(str(element).replace("\n", " "))

    if not isinstance(element, Tag):
        return ""

    classes = element.get("class") or []

    if any(c in classes for c in ("tooltip-context", "sources-link", "copy")):
        return ""

    if element.name == "math":
        return process_math(element, math_format)

    if element.name == "br":
        return "<br />"

    if element.name == "img":
        return format_img_jsx(element, image_public_path)

    if element.name == "svg":
        return ""

    if element.name == "a":
        href = str(element.get("href", ""))
        href = normalize_href(href, link_prefix)
        text = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children).strip()
        if not text:
            return ""
        return f"[{text}]({href})"

    if element.name in ("strong", "b"):
        inner = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children)
        stripped = inner.strip()
        if not stripped:
            return ""
        lead = " " if inner.startswith(" ") else ""
        trail = " " if inner.endswith(" ") else ""
        return f"{lead}**{stripped}**{trail}"

    if element.name in ("em", "i"):
        inner = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children)
        stripped = inner.strip()
        if not stripped:
            return ""
        lead = " " if inner.startswith(" ") else ""
        trail = " " if inner.endswith(" ") else ""
        return f"{lead}_{stripped}_{trail}"

    if element.name in ("sup", "sub"):
        inner = "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children)
        return f"<{element.name}>{inner}</{element.name}>"

    if element.name == "code":
        raw_lang = cast(str | None, element.get("data-lang"))
        lang = map_lang(raw_lang) if raw_lang else None
        text = extract_code_text(element).strip()
        return format_inline_code(text, lang)

    if element.name == "span" and "pill" in classes:
        return format_inline_code(element.get_text(strip=True))

    return "".join(process_inline(c, link_prefix, math_format, image_public_path) for c in element.children)


def extract_page_metadata(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")

    title = "Untitled"
    title_tag = soup.find("title")
    if isinstance(title_tag, Tag):
        raw_title = title_tag.get_text(strip=True)
        title = re.sub(r"\s*-\s*Typst Documentation$", "", raw_title).strip() or raw_title

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if isinstance(meta_desc, Tag) and meta_desc.get("content"):
        description = str(meta_desc.get("content")).replace("\n", " ").strip()

    return title, description


def html_to_mdx(
    html_content: str,
    link_prefix: str = "/",
    math_format: str = "latex",
    image_public_path: str = "/docs-assets/images",
) -> str:
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.find("main") or soup.body or soup

    output: list[str] = []
    for element in container.children:
        result = process_element(
            element,
            link_prefix=link_prefix,
            math_format=math_format,
            image_public_path=image_public_path,
        )
        if result:
            output.append(result)

    return "\n\n".join(output)